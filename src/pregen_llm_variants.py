"""
LLM 变体预生成脚本 — 后台批量预生成，测试时毫秒级取缓存

用法：
  python src/pregen_llm_variants.py              # 全部岗位全部题
  python src/pregen_llm_variants.py --role insurance-agent --layers L2,L3
  python src/pregen_llm_variants.py --stats      # 仅查看缓存覆盖情况

策略：
  - 每题调用 1 次 API，生成 3 个变体入缓存
  - 已有足够缓存的题跳过（幂等，可反复运行）
  - 并发 4 线程，避免 API 限流
  - 成本估算：163 题 × 1 次 × (500in + 800out tokens) ≈ ¥1 内
"""

import json
import sys
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
from llm_variant import generate_llm_variants, llm_available, _content_hash, _load_cache

ROOT = Path(__file__).parent.parent
ROLE_DIR = ROOT / "data" / "roles"
TARGET_VARIANTS = 3   # 每题目标变体数


def pregen_question(q: dict, idx: int, total: int) -> dict:
    """为一道题预生成变体（缓存足够则跳过）"""
    qhash = _content_hash(q)
    cached = _load_cache(qhash) or []
    if len(cached) >= TARGET_VARIANTS:
        return {"idx": idx, "status": "skip", "count": len(cached)}
    result = generate_llm_variants(q, count=TARGET_VARIANTS)
    status = "ok" if len(result) >= TARGET_VARIANTS else "partial"
    return {"idx": idx, "status": status, "count": len(result),
            "node": q.get("node_id", "?")}


def run(role: str, layers: set, workers: int = 4):
    tests_path = ROLE_DIR / role / "tests.json"
    if not tests_path.exists():
        print(f"题库不存在: {tests_path}")
        return

    originals = json.loads(tests_path.read_text(encoding="utf-8"))
    if layers:
        originals = [q for q in originals
                     if q.get("node_id", "")[:2].upper() in layers]

    total = len(originals)
    print(f"岗位: {role} | 目标题数: {total} | 每题变体: {TARGET_VARIANTS} | 并发: {workers}")
    if not llm_available():
        print("错误: 未找到 DEEPSEEK_API_KEY，请设置环境变量或 .env 文件")
        return

    ok, skip, partial, fail = 0, 0, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(pregen_question, q, i, total): i
                   for i, q in enumerate(originals)}
        for fut in as_completed(futures):
            r = fut.result()
            if r["status"] == "ok": ok += 1
            elif r["status"] == "skip": skip += 1
            elif r["status"] == "partial": partial += 1
            else: fail += 1
            done = ok + skip + partial + fail
            if done % 10 == 0 or done == total:
                print(f"  进度 {done}/{total} | 新生成 {ok} | 跳过 {skip} | 部分 {partial} | 失败 {fail}")

    print(f"\n完成: 新生成 {ok} | 已缓存跳过 {skip} | 部分成功 {partial} | 失败 {fail}")


def stats(role: str):
    tests_path = ROLE_DIR / role / "tests.json"
    originals = json.loads(tests_path.read_text(encoding="utf-8"))
    covered, total = 0, len(originals)
    by_layer = {}
    for q in originals:
        layer = q.get("node_id", "??")[:2].upper()
        by_layer.setdefault(layer, [0, 0])
        by_layer[layer][1] += 1
        cached = _load_cache(_content_hash(q)) or []
        if len(cached) >= TARGET_VARIANTS:
            covered += 1
            by_layer[layer][0] += 1
    print(f"缓存覆盖: {covered}/{total} ({covered*100//max(total,1)}%)")
    for layer, (c, t) in sorted(by_layer.items()):
        print(f"  {layer}: {c}/{t}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="insurance-agent")
    ap.add_argument("--layers", default="", help="如 L2,L3；空为全部")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        stats(args.role)
    else:
        layers = {s.strip().upper() for s in args.layers.split(",") if s.strip()}
        run(args.role, layers, args.workers)
