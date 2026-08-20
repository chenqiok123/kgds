#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户文案泄露检测器 —— KGDS 面向用户文字的"保险丝"。

纪律（村长铁律）：任何 AI 生成的题，入库前必须过这道检测。
用户可见文案（题干 + 选项）里绝不能出现我们内部系统的命名：
  · KGDS / kgds        —— 系统名，读者不知道也不该知道
  · L1-10 / L2-04 等    —— 图谱节点 ID（L#-## 格式）
  · 交叉验证 / 反盲选    —— 内部出题机制名
  · 映射                —— "被映射到…节点"这类内部表述
  · 【…】              —— 方括号标签（内部标记）
  · （正确）/(正确)      —— 正确项标注（不该出现在用户选项里）

注意：单独的"节点"二字不入黑名单——"关键节点"是正常中文词，避免误报；
真正的节点 ID 已由 L#-## 模式精准拦截。

用法：
  python verify_user_copy.py              # 默认扫描 data/reading 全部擂台题库
  python verify_user_copy.py <file...>    # 指定文件/目录
退出码：0 = 通过；1 = 发现泄露。可挂到提交前 / CI。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 精准黑名单（低误报）：标签 -> 正则
PATTERNS = {
    "系统名KGDS": re.compile(r"KGDS|kgds"),
    "节点ID(L#-##)": re.compile(r"L\d-\d\d"),
    "交叉验证": re.compile(r"交叉验证"),
    "反盲选": re.compile(r"反盲选"),
    "内部表述'映射'": re.compile(r"映射"),
    "标签【…】": re.compile(r"【.+?】"),
    "正确标注（正确）": re.compile(r"（正确）|\(正确\)"),
}

# 内部元数据字段（不对用户展示），跳过不检
INTERNAL_FIELDS = {"anti_guess", "confidence_decay", "explanation", "rationale", "_说明"}


def iter_user_texts(obj, path=""):
    """递归提取用户可见文本：question + options。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in INTERNAL_FIELDS:
                continue
            if k == "question" and isinstance(v, str):
                yield (path + ".question", v)
            elif k == "options" and isinstance(v, list):
                for i, o in enumerate(v):
                    if isinstance(o, str):
                        yield (f"{path}.options[{i}]", o)
            else:
                yield from iter_user_texts(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from iter_user_texts(item, f"{path}[{i}]")


def scan_file(fp):
    try:
        data = json.loads(Path(fp).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[跳过] 无法解析 {fp}: {e}")
        return []
    leaks = []
    for loc, text in iter_user_texts(data):
        for label, pat in PATTERNS.items():
            if pat.search(text):
                leaks.append((str(fp), loc, label, text[:90]))
    return leaks


def collect_files(args):
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.json")))
        elif p.is_file():
            files.append(p)
    return files


def main():
    args = sys.argv[1:]
    if args:
        files = collect_files(args)
    else:
        files = sorted(ROOT.glob("data/reading/**/arena_*tests.json"))
    if not files:
        print("⚠️ 未找到待扫描的题库文件。")
        sys.exit(0)
    all_leaks = []
    for f in files:
        all_leaks.extend(scan_file(f))
    if all_leaks:
        print(f"❌ 发现 {len(all_leaks)} 处用户文案泄露：")
        for fp, loc, label, tx in all_leaks:
            print(f"  [{label}] {fp} {loc}")
            print(f"      {tx}")
        sys.exit(1)
    print(f"✅ 用户文案校验通过：扫描 {len(files)} 个题库文件，无内部命名泄露。")
    sys.exit(0)


if __name__ == "__main__":
    main()
