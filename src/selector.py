# -*- coding: utf-8 -*-
"""
四重选题调度器（Selection Scheduler）

每次诊断固定配额：基础51 / 提升38 / 升华30（可配置）
四重机制（基于 4 领域调研整合，见 deliverables/selector_design.md）：
  第1层 节点均衡：每节点≥1题，未测节点优先（内容平衡 —— CAT）
  第2层 曝光控制：未测70% + 已测30% 混合（置换新题 + 保留熟悉题做提取练习）
  第3层 薄弱优先：confidence<0.67 节点从未测池优先补题（知识追踪 —— BKT）
  第4层 期望困难：变体由 variant_generator 处理（本模块输出原始题，含 qid）

追踪单位：qid（tests.json 中 node_id#序号），变体继承原题 qid → 做过=知识点已覆盖。
"""

import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Set

ROLE_DIR = Path(__file__).resolve().parent.parent / "data" / "roles"

# 默认配额：基础51 / 提升38 / 升华30（村长 2026-08-05 确认）
QUOTAS = {"foundation": 51, "advanced": 38, "transcendent": 30}
# 已测题保留比例（提取练习，测试效应）
TESTED_RATIO = 0.3
# 掌握阈值（与 server.py 一致）
MASTERY_THRESHOLD = 0.67

LAYER_PREFIX = {"foundation": "L1", "advanced": "L2", "transcendent": "L3"}


def _load_tests(role: str) -> List[dict]:
    """加载题库（带 qid）"""
    p = ROLE_DIR / role / "tests.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _load_nodes(role: str) -> List[dict]:
    """加载节点（含 layer, weight）"""
    p = ROLE_DIR / role / "nodes.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _rng(seed: Optional[int]) -> random.Random:
    return random.Random(seed) if seed is not None else random.Random()


def _pick(pool: List[dict], rng: random.Random) -> dict:
    """从池中随机取一个并返回（不修改池）"""
    return rng.choice(pool)


def select_questions(
    role: str = "insurance-agent",
    levels: Optional[List[str]] = None,
    tested_qids: Optional[Set[str]] = None,
    node_confidence: Optional[Dict[str, float]] = None,
    seed: Optional[int] = None,
    quotas: Optional[Dict[str, int]] = None,
) -> List[dict]:
    """
    四重调度：按配额从题库选题。

    Args:
        role: 岗位
        levels: 选择的层级，如 ["foundation","advanced","transcendent"]；None=全部
        tested_qids: 该用户已测过的 qid 集合（历史 questions_summary 提取）
        node_confidence: 节点掌握度 {node_id: confidence 0~1}（历史 node_status 提取）
        seed: 随机种子
        quotas: 覆盖默认配额

    Returns:
        选出的原始题列表（含 qid；不生成变体，变体由 variant_generator 处理）
    """
    tests = _load_tests(role)
    nodes = _load_nodes(role)
    if not tests:
        return []

    q = quotas or dict(QUOTAS)
    tested = set(tested_qids or ())
    conf = dict(node_confidence or {})

    # 按层分组
    by_layer: Dict[str, List[dict]] = {}
    for t in tests:
        nid = t.get("node_id", "")
        prefix = nid[:2].upper()
        layer = None
        for lname, pfx in LAYER_PREFIX.items():
            if prefix == pfx:
                layer = lname
                break
        if layer is None:
            continue
        by_layer.setdefault(layer, []).append(t)

    # 节点信息索引
    node_layer = {}
    for n in nodes:
        node_layer[n["id"]] = n.get("layer", "")
    node_weight = {}
    for n in nodes:
        node_weight[n["id"]] = n.get("weight", 1)

    rng = _rng(seed)
    result: List[dict] = []
    used_qids: Set[str] = set()

    for layer in LAYER_PREFIX:
        if levels and layer not in levels:
            continue
        quota = q.get(layer, QUOTAS[layer])
        pool = by_layer.get(layer, [])
        if not pool:
            continue
        layer_result = _select_layer(pool, quota, tested, conf, rng)
        for t in layer_result:
            result.append(t)
            if t.get("qid"):
                used_qids.add(t["qid"])

    return result


def _select_layer(
    pool: List[dict],
    quota: int,
    tested: Set[str],
    conf: Dict[str, float],
    rng: random.Random,
) -> List[dict]:
    """单层内调度：节点均衡 → 70/30 曝光控制 → 薄弱优先 → 随机补足"""
    # 按节点分组
    by_node: Dict[str, List[dict]] = {}
    for t in pool:
        by_node.setdefault(t.get("node_id", ""), []).append(t)

    node_ids = sorted(by_node.keys())
    selected: List[dict] = []
    used: Set[str] = set()

    def _untested(node_pool: List[dict]) -> List[dict]:
        return [t for t in node_pool if t.get("qid") not in tested and t.get("qid") not in used]

    def _tested(node_pool: List[dict]) -> List[dict]:
        return [t for t in node_pool if t.get("qid") in tested and t.get("qid") not in used]

    # 目标比例：未测 70% / 已测 30%
    tested_target = int(quota * TESTED_RATIO)
    untested_target = quota - tested_target

    # ── 第1层 节点均衡：每节点 ≥1 题（内容平衡）──
    # 统一未测优先：节点"已测"≠节点内所有题已测（首次每节点仅测1-2题），
    # 节点内仍有未测题时优先出未测题（置换最大化）；
    # 30% 熟悉题完全由第2层曝光控制提供，均衡轮不承担熟悉感配额。
    for nid in node_ids:
        node_pool = by_node[nid]
        candidates = _untested(node_pool) or _tested(node_pool)
        if not candidates:
            continue
        pick = rng.choice(candidates)
        selected.append(pick)
        used.add(pick["qid"])

    # 配额可能小于节点数（罕见）：截断
    if len(selected) > quota:
        return rng.sample(selected, quota)

    # 均衡轮后统计已测题数，若超过目标 → 换出多余已测题，补入未测题
    tested_count = sum(1 for t in selected if t.get("qid") in tested)
    if tested_count > tested_target:
        excess = tested_count - tested_target
        # 找出多余已测题（从已测节点中选，但要保留每个节点至少 1 题）
        excess_items = []
        for t in selected:
            if t.get("qid") in tested:
                excess_items.append(t)
        rng.shuffle(excess_items)
        # 换出：确保换出后该节点仍有题（均衡轮每节点仅 1 题，直接换出即可，
        # 但换出后该节点可能 0 题 —— 允许，因为该节点有已测题记录）
        swapped = 0
        for t in excess_items:
            if swapped >= excess:
                break
            # 从未测池找一个替换（任意节点）
            cand = [x for x in pool if x.get("qid") not in tested and x.get("qid") not in used]
            if not cand:
                break
            rep = rng.choice(cand)
            selected.remove(t)
            used.discard(t["qid"])
            selected.append(rep)
            used.add(rep["qid"])
            swapped += 1

    remaining = quota - len(selected)
    if remaining <= 0:
        return selected

    # 重新计算剩余预算（保持总比例）
    cur_tested = sum(1 for t in selected if t.get("qid") in tested)
    cur_untested = len(selected) - cur_tested
    tested_budget = max(0, tested_target - cur_tested)
    untested_budget = max(0, untested_target - cur_untested)
    # 若两类预算之和 < remaining（目标已满但配额未满），把差值作为自由池
    free_budget = remaining - (tested_budget + untested_budget)

    # ── 第2层+第3层：薄弱节点优先补未测题（知识追踪）──
    weak_nodes = [nid for nid in node_ids if conf.get(nid, 1.0) < MASTERY_THRESHOLD]
    for nid in weak_nodes:
        if untested_budget <= 0:
            break
        candidates = _untested(by_node.get(nid, []))
        if not candidates:
            continue
        pick = rng.choice(candidates)
        selected.append(pick)
        used.add(pick["qid"])
        untested_budget -= 1

    # 未测池补足（节点分散）
    untested_pool = [t for t in pool if t.get("qid") not in tested and t.get("qid") not in used]
    untested_pool.sort(key=lambda t: t.get("node_id", ""))
    rng.shuffle(untested_pool)
    for t in untested_pool:
        if untested_budget <= 0:
            break
        selected.append(t)
        used.add(t["qid"])
        untested_budget -= 1

    # 已测池补足（答错优先 —— confidence 升序）
    tested_pool = [t for t in pool if t.get("qid") in tested and t.get("qid") not in used]
    tested_pool.sort(key=lambda t: conf.get(t.get("node_id", ""), 1.0))
    rng.shuffle(tested_pool)
    for t in tested_pool:
        if tested_budget <= 0:
            break
        selected.append(t)
        used.add(t["qid"])
        tested_budget -= 1

    # ── 兜底：配额未满时从任意剩余题补（自由池）──
    if len(selected) < quota:
        leftover = [t for t in pool if t.get("qid") not in used]
        rng.shuffle(leftover)
        for t in leftover:
            if len(selected) >= quota:
                break
            selected.append(t)
            used.add(t["qid"])

    return selected[:quota]


# ── 历史提取 ──────────────────────────────
def extract_tested_qids(sessions: List[dict]) -> Set[str]:
    """
    从用户历史 session 列表提取已测 qid 集合。
    session 结构：{"questions": [{"id":..., "qid":..., "node_id":...}]}
    兼容旧数据（无 qid 字段）：无法追踪，返回空。
    """
    qids: Set[str] = set()
    for s in sessions:
        for q in s.get("questions", []):
            if q.get("qid"):
                qids.add(str(q["qid"]))
    return qids


def extract_node_confidence(sessions: List[dict]) -> Dict[str, float]:
    """
    从用户历史 session 提取节点掌握度（取最近一次的 confidence）。
    session 结构：{"node_status": {node_id: {"confidence": 0~1}}}
    """
    conf: Dict[str, float] = {}
    for s in sessions:
        ns = s.get("node_status") or {}
        for nid, info in ns.items():
            if isinstance(info, dict) and "confidence" in info:
                conf[nid] = float(info["confidence"])
    return conf


# ── 自测 ──────────────────────────────
if __name__ == "__main__":
    print("=== 调度器自测：0 历史（第 1 次诊断）===")
    qs = select_questions(seed=42)
    from collections import Counter
    layers = Counter()
    for t in qs:
        layers[t["node_id"][:2]] += 1
    print(f"总题数: {len(qs)}")
    print(f"层级分布: {dict(layers)}")
    assert len(qs) == sum(QUOTAS.values()), f"总题数应为 {sum(QUOTAS.values())}"
    assert len({t['qid'] for t in qs}) == len(qs), "qid 重复"

    print("\n=== 调度器自测：1 次历史（模拟第 2 次诊断）===")
    # 模拟历史：基础层每个节点测过 1 题
    tests = _load_tests("insurance-agent")
    mock_qids = set()
    seen_nodes = set()
    for t in tests:
        nid = t.get("node_id", "")
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            mock_qids.add(t["qid"])
    mock_conf = {nid: 0.5 for nid in seen_nodes}  # 全部薄弱
    qs2 = select_questions(seed=7, tested_qids=mock_qids, node_confidence=mock_conf)
    new_count = sum(1 for t in qs2 if t["qid"] not in mock_qids)
    old_count = sum(1 for t in qs2 if t["qid"] in mock_qids)
    print(f"总题数: {len(qs2)}")
    print(f"未测(新)题: {new_count} ({new_count/len(qs2)*100:.0f}%), 已测(旧)题: {old_count} ({old_count/len(qs2)*100:.0f}%)")
    assert len(qs2) == sum(QUOTAS.values())
    # 已测比例应接近 30%（允许 ±10%）
    ratio = old_count / len(qs2)
    assert 0.2 <= ratio <= 0.45, f"已测比例异常: {ratio:.2f}"
    print("[PASS] 已测比例在 20%~45% 区间（目标 30%）")

    print("\n=== 调度器自测：配额可配置 ===")
    qs3 = select_questions(seed=1, quotas={"foundation": 10, "advanced": 5, "transcendent": 3})
    c3 = Counter(t["node_id"][:2] for t in qs3)
    print(f"总题数: {len(qs3)}, 层级: {dict(c3)}")
    assert len(qs3) == 18
    print("[PASS] 配额可配置")
    print("\n全部自测通过 ✅")
