"""
飞轮引擎 v2 — 群体智慧 + 个体进度追踪

三个飞轮：
  飞轮一 — 知识图谱自校准（节点权重/层级/前置依赖）
  飞轮二 — 反盲选阈值自进化（误判率校准）
  飞轮三 — 试题质量迭代（IRT 参数评估 + 差题淘汰）

群体智慧机制：
  - 用户越多 → 节点难度收敛越精确 → 权重/层级校准置信度提升
  - 多用户跨层对比 → 自动发现前置依赖关系
  - 反盲选阈值随样本量自动收紧/放宽
  - 经验分层（0-1/1-3/3-5/5-10/10+年）区分度随用户数提升

数据流：
  单次答题 → data/users/{role}/{user_id}/{session_id}.json
  累积 ≥10 次新会话 → 自动触发 analyze() → 写回 nodes.json / tests.json

降级策略：
  < 10 条：仅记录，不分析
  10-50 条：分析 + 仅置信度≥0.85 自动写入
  50-200 条：置信度≥0.7 自动写入
  200+ 条：置信度≥0.6 自动写入
"""

import json, math, os, shutil
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

ROOT = Path(__file__).parent.parent
# 云端持久卷路径；本地开发回退到 ROOT
_DATA_BASE = Path(os.environ.get("KGDS_DATA_DIR", str(ROOT)))
USERS_DIR = _DATA_BASE / "data" / "users"
BACKUP_DIR = _DATA_BASE / "data" / "backups"
# ROLE_DIR 是只读的知识图谱数据，随代码部署
ROLE_DIR = ROOT / "data" / "roles"


# ── 数据结构 ────────────────────────────────────────────

@dataclass
class UserProgress:
    """单个用户的连续进步轨迹"""
    user_id: str
    session_count: int = 0
    first_score: float = 0.0
    latest_score: float = 0.0
    best_score: float = 0.0
    trend: str = "stable"           # "rising" | "falling" | "stable"
    sessions: List[dict] = field(default_factory=list)
    # 按节点的进步追踪
    node_progress: Dict[str, List[float]] = field(default_factory=dict)  # node_id → [score_seq]


@dataclass
class NodeStats:
    """一个知识节点的跨用户累积统计"""
    node_id: str
    label: str
    layer: str
    total_attempts: int = 0
    total_correct: int = 0
    user_count: int = 0          # 多少用户做过这个节点
    session_count: int = 0       # 总答题次数
    # 按经验分层的掌握率
    mastery_by_exp: Dict[str, Dict[str, int]] = field(default_factory=dict)  # exp → {correct, total}
    # 进步可塑性（低 = 天然难 / 高 = 容易学会）
    plasticity: float = 0.0      # 用户进步幅度均值
    plasticity_count: int = 0
    # 前置依赖发现
    co_mastered_with: Dict[str, int] = field(default_factory=dict)
    # 置信度（随样本量增长）
    confidence: float = 0.0


@dataclass
class QuestionStats:
    """一道试题的跨用户累积统计"""
    question_id: str
    node_id: str
    total_attempts: int = 0
    total_correct: int = 0
    # IRT 参数
    difficulty: float = 0.5       # b: 1 - 正确率
    discrimination: float = 0.0   # a: 高分通过率 - 低分通过率
    guessing: float = 0.25        # c: 低分组通过率
    # 状态
    flagged_bad: bool = False
    flag_reason: str = ""
    confidence: float = 0.0


@dataclass
class FlywheelStats:
    """飞轮健康度面板"""
    total_sessions: int = 0
    total_users: int = 0
    total_nodes: int = 0
    total_questions: int = 0
    last_analysis: Optional[str] = None
    auto_actions_applied: int = 0
    nodes_adjusted: int = 0
    questions_flagged: int = 0
    avg_node_confidence: float = 0.0
    avg_question_confidence: float = 0.0
    data_sufficiency: str = "insufficient"  # "insufficient" | "minimal" | "moderate" | "rich"
    recent_trend: str = ""         # 最近10次会话的趋势描述


# ── 飞轮引擎 ────────────────────────────────────────────

class FlywheelEngine:
    """三飞轮系统 v2 — 群体智慧 + 个体进度"""

    def __init__(self, role: str = "insurance-agent"):
        self.role = role
        self.nodes = self._load_nodes()
        self.edges = self._load_edges()
        self.tests = self._load_tests()
        self.users_dir = USERS_DIR / role
        self.users_dir.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # ── 加载 ─────────────────────────────────────────

    def _load_nodes(self) -> dict:
        path = ROLE_DIR / self.role / "nodes.json"
        if path.exists():
            return {n["id"]: n for n in json.loads(path.read_text(encoding="utf-8"))}
        return {}

    def _load_edges(self) -> list:
        path = ROLE_DIR / self.role / "edges.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def _load_tests(self) -> list:
        path = ROLE_DIR / self.role / "tests.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def _reload(self):
        """重新加载数据（校准后节点可能已更新）"""
        self.nodes = self._load_nodes()
        self.edges = self._load_edges()
        self.tests = self._load_tests()

    # ── 数据累积 ─────────────────────────────────────

    def save_session(self, session: dict, user_id: str = "") -> str:
        """保存一次答题会话"""
        session_id = session.get("session_id") or f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        record = {
            "session_id": session_id,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "profile": session.get("profile", {}),
            "answers": session.get("answers", {}),
            "node_status": session.get("node_status", {}),
            "blind_suspects": session.get("blind_suspects", {}),
            "overall_score": session.get("overall_score", 0)
        }
        uid = user_id if user_id else "anonymous"
        user_dir = self.users_dir / uid
        user_dir.mkdir(parents=True, exist_ok=True)
        filepath = user_dir / f"{session_id}.json"
        filepath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return session_id

    def load_all_sessions(self, user_id: str = None) -> List[dict]:
        """加载历史记录。user_id=None = 全部用户"""
        sessions = []
        if not self.users_dir.exists():
            return sessions
        search_dirs = [self.users_dir / user_id] if user_id else sorted(
            [d for d in self.users_dir.iterdir() if d.is_dir()])
        for d in search_dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    sessions.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return sessions

    def get_user_count(self) -> int:
        """统计独立用户数（排除 anonymous）"""
        if not self.users_dir.exists():
            return 0
        return len([d for d in self.users_dir.iterdir() if d.is_dir() and d.name != "anonymous"])

    def get_session_count(self) -> int:
        return len(self.load_all_sessions())

    # ── 个体进度追踪 ─────────────────────────────────

    def track_user_progress(self, user_id: str) -> UserProgress:
        """追踪单个用户的连续诊断进步"""
        sessions = self.load_all_sessions(user_id=user_id)
        sessions.sort(key=lambda s: s.get("timestamp", ""))

        up = UserProgress(user_id=user_id, session_count=len(sessions))
        if not sessions:
            return up

        scores = [s.get("overall_score", 0) for s in sessions]
        up.first_score = scores[0]
        up.latest_score = scores[-1]
        up.best_score = max(scores)

        # 趋势判断
        if len(scores) >= 3:
            # 简单线性趋势：后1/3 vs 前1/3
            third = max(len(scores) // 3, 1)
            early_avg = sum(scores[:third]) / third
            late_avg = sum(scores[-third:]) / third
            delta = late_avg - early_avg
            if delta > 5:
                up.trend = "rising"
            elif delta < -5:
                up.trend = "falling"
            else:
                up.trend = "stable"

        # 每节点的进步序列
        for s in sessions:
            ns = s.get("node_status", {})
            for nid, status in ns.items():
                conf = status.get("confidence", 0)
                up.node_progress.setdefault(nid, []).append(conf)
            up.sessions.append({
                "session_id": s.get("session_id"),
                "score": s.get("overall_score", 0),
                "timestamp": s.get("timestamp", "")
            })

        # 计算每个节点的可塑性（进步幅度）
        for nid, confs in up.node_progress.items():
            if len(confs) >= 2 and confs[0] > 0:
                gain = confs[-1] - confs[0]
                if nid in self.nodes:
                    ns = NodeStats(node_id=nid, label=self.nodes[nid].get("label", nid),
                                   layer=self.nodes[nid].get("layer", "unknown"))
                    ns.plasticity = gain
                    ns.plasticity_count = 1

        return up

    def get_all_user_progress(self) -> Dict[str, UserProgress]:
        """获取所有用户的进步追踪"""
        result = {}
        if not self.users_dir.exists():
            return result
        for d in self.users_dir.iterdir():
            if d.is_dir() and d.name != "anonymous":
                result[d.name] = self.track_user_progress(d.name)
        return result

    # ── 飞轮一：知识图谱自校准 ────────────────────────

    def calibrate_graph(self, sessions: List[dict]) -> dict:
        """跨用户分析 + 群体智慧聚合"""
        self._reload()
        node_stats: Dict[str, NodeStats] = {}
        user_progress = self.get_all_user_progress()

        # 1. 聚合
        for sess in sessions:
            ns_data = sess.get("node_status", {})
            profile = sess.get("profile", {})
            exp = profile.get("years", profile.get("experience", "unknown"))

            for nid, status in ns_data.items():
                if nid not in self.nodes:
                    continue
                if nid not in node_stats:
                    node = self.nodes[nid]
                    node_stats[nid] = NodeStats(node_id=nid, label=node.get("label", nid),
                                                layer=node.get("layer", "foundation"))
                ns = node_stats[nid]
                ns.total_attempts += status.get("total", 0)
                ns.total_correct += status.get("correct", 0)
                ns.session_count += 1
                ns.user_count = ns.session_count  # 简化：每 session 算一次接触

                # 经验分层
                mb = ns.mastery_by_exp.setdefault(exp, {"correct": 0, "total": 0})
                mb["total"] += 1
                if status.get("mastered"):
                    mb["correct"] += 1

            # 2. 共现
            mastered = [nid for nid, s in ns_data.items() if s.get("mastered") and nid in node_stats]
            for i, a in enumerate(mastered):
                for b in mastered[i + 1:]:
                    node_stats[a].co_mastered_with[b] = node_stats[a].co_mastered_with.get(b, 0) + 1
                    node_stats[b].co_mastered_with[a] = node_stats[b].co_mastered_with.get(a, 0) + 1

        # 3. 融入个体可塑性
        for uid, up in user_progress.items():
            for nid, confs in up.node_progress.items():
                if nid in node_stats and len(confs) >= 2:
                    ns = node_stats[nid]
                    ns.plasticity += (confs[-1] - confs[0])
                    ns.plasticity_count += 1

        # 4. 生成校准建议（带置信度）
        total_users = self.get_user_count()
        calibrations = {"layer_adjustments": [], "weight_adjustments": [],
                        "dependency_suggestions": [], "flag_issues": []}

        for nid, ns in node_stats.items():
            if ns.session_count < 5:
                continue

            node = self.nodes.get(nid, {})
            current_layer = node.get("layer", "foundation")
            current_weight = node.get("weight", 3)
            mastery_rate = ns.total_correct / max(ns.total_attempts, 1)
            # 置信度随样本量增长（0.3 起步 → 100+样本达到 0.95）
            ns.confidence = min(0.95, 0.3 + ns.session_count * 0.0065)

            # 4a. 层级校准
            if current_layer == "foundation" and mastery_rate < 0.35 and ns.session_count >= 10:
                sr = ns.mastery_by_exp.get("5-10", ns.mastery_by_exp.get("10+", {}))
                if sr.get("total", 0) >= 3:
                    sr_rate = sr["correct"] / sr["total"]
                    if sr_rate < 0.55:
                        calibrations["layer_adjustments"].append({
                            "node_id": nid, "label": ns.label,
                            "current_layer": "foundation", "suggested_layer": "advanced",
                            "reason": f"5年+经验者掌握率仅 {sr_rate:.0%}，不具备基础属性",
                            "confidence": ns.confidence
                        })

            if current_layer == "transcendent" and mastery_rate > 0.65 and ns.session_count >= 10:
                jr = ns.mastery_by_exp.get("0-1", ns.mastery_by_exp.get("1-3", {}))
                if jr.get("total", 0) >= 3:
                    jr_rate = jr["correct"] / jr["total"]
                    if jr_rate > 0.55:
                        calibrations["layer_adjustments"].append({
                            "node_id": nid, "label": ns.label,
                            "current_layer": "transcendent", "suggested_layer": "advanced",
                            "reason": f"1-3年经验者掌握率 {jr_rate:.0%}，不具升华属性",
                            "confidence": ns.confidence
                        })

            # 4b. 权重校准：基于经验区分度和可塑性
            jr = ns.mastery_by_exp.get("0-1", ns.mastery_by_exp.get("1-3", {}))
            sr = ns.mastery_by_exp.get("5-10", ns.mastery_by_exp.get("10+", {}))
            if jr.get("total", 0) >= 3 and sr.get("total", 0) >= 3:
                jr_rate = jr["correct"] / jr["total"]
                sr_rate = sr["correct"] / sr["total"]
                gap = abs(sr_rate - jr_rate)
                plasticity = ns.plasticity / max(ns.plasticity_count, 1) if ns.plasticity_count > 0 else 0

                if gap > 0.45:
                    suggested = min(current_weight + 3, 15)
                elif gap > 0.25 or plasticity > 0.15:
                    suggested = current_weight
                else:
                    suggested = max(current_weight - 2, 1)

                if abs(suggested - current_weight) >= 1:
                    calibrations["weight_adjustments"].append({
                        "node_id": nid, "label": ns.label,
                        "current_weight": current_weight, "suggested_weight": suggested,
                        "reason": f"经验区分度 {gap:.0%} (新手 {jr_rate:.0%} vs 老手 {sr_rate:.0%})，可塑性 {plasticity:+.0%}",
                        "confidence": ns.confidence
                    })

            # 4c. 前置依赖自动发现
            if ns.session_count >= 8:
                for co_nid, co_count in ns.co_mastered_with.items():
                    if co_count >= ns.session_count * 0.4 and co_nid in node_stats:
                        edge_exists = any(
                            (e.get("source") == nid and e.get("target") == co_nid) or
                            (e.get("source") == co_nid and e.get("target") == nid)
                            for e in self.edges
                        )
                        if not edge_exists:
                            calibrations["dependency_suggestions"].append({
                                "source": nid, "target": co_nid,
                                "source_label": ns.label,
                                "target_label": node_stats[co_nid].label,
                                "co_mastery_rate": co_count / ns.session_count,
                                "confidence": min(0.8, 0.3 + co_count * 0.05)
                            })

        # 群体智慧指标
        calibrations["population_metrics"] = {
            "total_users": total_users,
            "total_sessions": len(sessions),
            "avg_node_confidence": sum(ns.confidence for ns in node_stats.values()) / max(len(node_stats), 1),
            "actionable_count": len(calibrations["layer_adjustments"]) + len(calibrations["weight_adjustments"])
        }

        return calibrations

    # ── 飞轮二：反盲选阈值自进化 ──────────────────────

    def calibrate_blind_detection(self, sessions: List[dict]) -> dict:
        calibrations = {"threshold_adjustments": [], "false_positive_analysis": [],
                        "summary": {}}

        user_sessions: Dict[str, List[dict]] = defaultdict(list)
        for s in sessions:
            uid = s.get("user_id", s.get("profile", {}).get("name", "unknown"))
            user_sessions[uid].append(s)

        total_s, total_v, total_fp = 0, 0, 0
        for uid, slist in user_sessions.items():
            if len(slist) < 2:
                continue
            slist.sort(key=lambda x: x.get("timestamp", ""))
            for i in range(len(slist) - 1):
                prev, nxt = slist[i], slist[i + 1]
                for nid, reasons in prev.get("blind_suspects", {}).items():
                    total_s += 1
                    next_status = nxt.get("node_status", {}).get(nid, {})
                    if next_status:
                        if next_status.get("mastered"):
                            total_fp += 1
                            calibrations["false_positive_analysis"].append({
                                "user": uid, "node_id": nid, "reasons": reasons,
                                "outcome": "复测通过（疑似误判）"
                            })
                        else:
                            total_v += 1

        calibrations["summary"] = {
            "total_suspects": total_s, "verified": total_v,
            "false_positives": total_fp,
            "fp_rate": total_fp / max(total_s, 1)
        }

        fp_rate = calibrations["summary"]["fp_rate"]
        if fp_rate > 0.30 and total_s >= 10:
            calibrations["threshold_adjustments"].append({
                "suggestion": "放宽盲选阈值",
                "detail": "同节点答对 < 50% → 嫌疑（原为 < 100%且 ≥ 1）",
                "confidence": min(0.9, 0.5 + total_s * 0.02)
            })
        elif fp_rate < 0.08 and total_s >= 20:
            calibrations["threshold_adjustments"].append({
                "suggestion": "阈值精准，维持不变",
                "confidence": 0.85
            })

        return calibrations

    # ── 飞轮三：试题质量迭代 ──────────────────────────

    def evaluate_questions(self, sessions: List[dict]) -> dict:
        self._reload()
        tests = self.tests
        if not tests:
            # fallback: 从 sessions 的 answers 推断题号
            tests = []
            seen = set()
            for s in sessions:
                for qid in s.get("answers", {}).keys():
                    if qid not in seen:
                        seen.add(qid)
                        tests.append({"id": qid, "node_id": "", "correct_index": 0})

        test_map = {t.get("id", ""): t for t in tests}
        q_stats: Dict[str, QuestionStats] = {}

        # 按得分分组（高分/低分各 1/3）
        scored = []
        for s in sessions:
            ans = s.get("answers", {})
            tc = 0
            for qid, chosen in ans.items():
                t = test_map.get(qid, {})
                correct_idx = t.get("correct_index", t.get("correct", 0))
                if chosen == correct_idx:
                    tc += 1
            scored.append((s, tc, len(ans)))

        scored.sort(key=lambda x: x[1] / max(x[2], 1), reverse=True)
        n = len(scored)
        third = max(n // 3, 2)
        high_set = {s[0].get("user_id", s[0].get("profile", {}).get("name", "")) for s in scored[:third]}
        low_set = {s[0].get("user_id", s[0].get("profile", {}).get("name", "")) for s in scored[-third:]}

        # 聚合每题数据
        for s, tc, ta in scored:
            uid = s.get("user_id", s.get("profile", {}).get("name", ""))
            in_high = uid in high_set
            in_low = uid in low_set
            answers = s.get("answers", {})

            for qid, chosen in answers.items():
                t = test_map.get(qid, {})
                correct_idx = t.get("correct_index", t.get("correct", 0))
                is_correct = (chosen == correct_idx)

                if qid not in q_stats:
                    q_stats[qid] = QuestionStats(question_id=qid, node_id=t.get("node_id", ""))
                qs = q_stats[qid]
                qs.total_attempts += 1
                if is_correct:
                    qs.total_correct += 1
                if not hasattr(qs, '_h'):  # 用 dict 存分组计数
                    qs._h = qs._l = qs._ht = qs._lt = 0
                if in_high:
                    qs._ht += 1
                    if is_correct: qs._h += 1
                if in_low:
                    qs._lt += 1
                    if is_correct: qs._l += 1

        # 计算 IRT + 标记差题
        bad, good = [], []
        for qid, qs in q_stats.items():
            if qs.total_attempts < 5:
                continue
            qs.difficulty = 1 - (qs.total_correct / qs.total_attempts)
            hr = qs._h / max(qs._ht, 1)
            lr = qs._l / max(qs._lt, 1)
            qs.discrimination = hr - lr
            qs.guessing = min(lr, 0.5)
            qs.confidence = min(0.95, 0.25 + qs.total_attempts * 0.007)

            if qs.discrimination < 0.05:
                qs.flagged_bad = True
                qs.flag_reason = f"区分度过低({qs.discrimination:.2f})"
            elif qs.difficulty > 0.92:
                qs.flagged_bad = True
                qs.flag_reason = f"过难({qs.difficulty:.1%})"
            elif qs.guessing > 0.35:
                qs.flagged_bad = True
                qs.flag_reason = f"猜测度过高({qs.guessing:.0%})"

            if qs.flagged_bad:
                bad.append(qs)
            else:
                good.append(qs)

        return {
            "total_evaluated": len(q_stats),
            "bad_questions": [{"id": q.question_id, "node_id": q.node_id,
                               "difficulty": round(q.difficulty, 3),
                               "discrimination": round(q.discrimination, 3),
                               "guessing": round(q.guessing, 3),
                               "attempts": q.total_attempts,
                               "reason": q.flag_reason} for q in bad],
            "good_count": len(good),
            "summary": {
                "avg_difficulty": sum(q.difficulty for q in good) / max(len(good), 1) if good else 0,
                "avg_discrimination": sum(q.discrimination for q in good) / max(len(good), 1) if good else 0,
                "flagged_rate": len(bad) / max(len(q_stats), 1)
            }
        }

    # ── 综合运行 + 自动写回 ──────────────────────────

    def run_all(self, min_sessions: int = 10) -> dict:
        sessions = self.load_all_sessions()
        if len(sessions) < min_sessions:
            return {"status": "insufficient_data",
                    "message": f"需 ≥{min_sessions} 次，当前 {len(sessions)}", "current": len(sessions)}

        graph = self.calibrate_graph(sessions)
        blind = self.calibrate_blind_detection(sessions)
        quest = self.evaluate_questions(sessions)

        return {
            "status": "ok",
            "sessions_analyzed": len(sessions),
            "users": self.get_user_count(),
            "timestamp": datetime.now().isoformat(),
            "flywheel_1_graph": graph,
            "flywheel_2_blind": blind,
            "flywheel_3_questions": quest,
            "auto_actions": self._decide_auto_actions(graph, quest, len(sessions))
        }

    def _decide_auto_actions(self, graph: dict, quest: dict, total_sessions: int) -> list:
        """根据数据量决定自动执行的置信度阈值"""
        if total_sessions >= 200:
            threshold = 0.6
        elif total_sessions >= 50:
            threshold = 0.7
        elif total_sessions >= 10:
            threshold = 0.85
        else:
            return []

        actions = []
        for adj in graph.get("layer_adjustments", []):
            if adj.get("confidence", 0) >= threshold:
                actions.append({"action": "adjust_layer", "node_id": adj["node_id"],
                                "from": adj["current_layer"], "to": adj["suggested_layer"],
                                "confidence": adj["confidence"]})
        for adj in graph.get("weight_adjustments", []):
            if adj.get("confidence", 0) >= threshold:
                actions.append({"action": "adjust_weight", "node_id": adj["node_id"],
                                "from": adj["current_weight"], "to": adj["suggested_weight"],
                                "confidence": adj["confidence"]})
        for sugg in graph.get("dependency_suggestions", []):
            if sugg.get("confidence", 0) >= threshold:
                actions.append({"action": "add_edge", "source": sugg["source"],
                                "target": sugg["target"], "strength": sugg.get("co_mastery_rate", 0.5),
                                "confidence": sugg["confidence"]})
        for q in quest.get("bad_questions", []):
            actions.append({"action": "flag_question", "question_id": q["id"],
                            "reason": q["reason"], "confidence": 0.8})
        return actions

    def apply_auto_actions(self, analysis_result: dict) -> dict:
        """执行自动动作，写回 nodes.json / edges.json / tests.json"""
        actions = analysis_result.get("auto_actions", [])
        if not actions:
            return {"applied": 0, "details": []}

        # 备份
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for fn in ["nodes.json", "edges.json", "tests.json"]:
            src = ROLE_DIR / self.role / fn
            if src.exists():
                shutil.copy2(src, BACKUP_DIR / f"{fn.replace('.json','')}_{timestamp}.json")

        applied = []
        nodes_updated = False
        edges_updated = False

        for act in actions:
            a_type = act["action"]
            detail = {"action": a_type, "applied": True}

            if a_type == "adjust_layer":
                nid = act["node_id"]
                if nid in self.nodes:
                    self.nodes[nid]["layer"] = act["to"]
                    nodes_updated = True
                    detail["detail"] = f"{nid}: {act['from']} → {act['to']}"

            elif a_type == "adjust_weight":
                nid = act["node_id"]
                if nid in self.nodes:
                    self.nodes[nid]["weight"] = act["to"]
                    nodes_updated = True
                    detail["detail"] = f"{nid}: {act['from']} → {act['to']}"

            elif a_type == "add_edge":
                src, tgt = act["source"], act["target"]
                if not any((e.get("source") == src and e.get("target") == tgt) or
                           (e.get("source") == tgt and e.get("target") == src)
                           for e in self.edges):
                    self.edges.append({"source": src, "target": tgt,
                                       "strength": act.get("strength", 0.5),
                                       "label": "飞轮自动发现", "color": "#5B9BD5"})
                    edges_updated = True
                    detail["detail"] = f"{src} ↔ {tgt}"

            elif a_type == "flag_question":
                qid = act["question_id"]
                for t in self.tests:
                    if t.get("id") == qid:
                        t["flywheel_flagged"] = True
                        t["flywheel_reason"] = act.get("reason", "")
                        detail["detail"] = f"标记差题: {qid}"
                        break

            applied.append(detail)

        # 写回
        if nodes_updated:
            nodes_list = list(self.nodes.values())
            (ROLE_DIR / self.role / "nodes.json").write_text(
                json.dumps(nodes_list, ensure_ascii=False, indent=2), encoding="utf-8")
        if edges_updated:
            (ROLE_DIR / self.role / "edges.json").write_text(
                json.dumps(self.edges, ensure_ascii=False, indent=2), encoding="utf-8")
        # tests 总是写回（标记差题）
        (ROLE_DIR / self.role / "tests.json").write_text(
            json.dumps(self.tests, ensure_ascii=False, indent=2), encoding="utf-8")

        self._reload()
        return {"applied": len(applied), "details": applied}

    # ── 触发判断 ──────────────────────────────────────

    def should_auto_run(self, min_new: int = 10) -> Tuple[bool, str, int]:
        """判断是否应自动运行飞轮"""
        sessions = self.load_all_sessions()
        total = len(sessions)

        if total < 10:
            return False, f"数据不足({total}/10)", total

        # 检查上次分析时间
        marker = ROLE_DIR / self.role / ".flywheel_last_run"
        if marker.exists():
            try:
                last_ts = marker.read_text().strip()
                last_dt = datetime.fromisoformat(last_ts)
                if (datetime.now() - last_dt).days < 3:
                    # 检查是否有足够的新数据
                    count_at_last = int(marker.read_text().split("\n")[-1]) if "\n" in marker.read_text() else 0
                    new = total - count_at_last
                    if new < min_new:
                        return False, f"新增不足(+{new}/{min_new})", total
            except Exception:
                pass

        return True, f"就绪({total}条)", total

    def mark_analysis_done(self, session_count: int):
        """记录分析完成"""
        marker = ROLE_DIR / self.role / ".flywheel_last_run"
        marker.write_text(f"{datetime.now().isoformat()}\n{session_count}")

    # ── 健康度面板 ────────────────────────────────────

    def get_stats(self) -> FlywheelStats:
        """返回飞轮健康度面板数据"""
        sessions = self.load_all_sessions()
        total = len(sessions)
        users = self.get_user_count()

        marker = ROLE_DIR / self.role / ".flywheel_last_run"
        last_analysis = None
        if marker.exists():
            try:
                last_analysis = marker.read_text().split("\n")[0]
            except Exception:
                pass

        # 数据充足度
        if total < 10:
            sufficiency = "insufficient"
        elif total < 50:
            sufficiency = "minimal"
        elif total < 200:
            sufficiency = "moderate"
        else:
            sufficiency = "rich"

        # 近期趋势
        recent = sorted(sessions, key=lambda s: s.get("timestamp", ""))[-10:]
        if len(recent) >= 3:
            scores = [s.get("overall_score", 0) for s in recent]
            early = sum(scores[:3]) / 3
            late = sum(scores[-3:]) / 3
            trend = f"近10次均分 {sum(scores)/len(scores):.0f}，" + ("↑上升" if late - early > 5 else ("↓下降" if early - late > 5 else "→持平"))
        else:
            trend = "数据不足"

        # 自动动作统计
        actions = 0
        if total >= 10:
            try:
                result = self.run_all()
                actions = len(result.get("auto_actions", []))
            except Exception:
                pass

        return FlywheelStats(
            total_sessions=total, total_users=users,
            total_nodes=len(self.nodes), total_questions=len(self.tests),
            last_analysis=last_analysis, auto_actions_applied=actions,
            recent_trend=trend, data_sufficiency=sufficiency
        )


# ── 调度器 ────────────────────────────────────────────

class FlywheelScheduler:
    def __init__(self, engine: FlywheelEngine, min_new: int = 10):
        self.engine = engine
        self.min_new = min_new

    def try_run(self) -> Optional[dict]:
        """尝试触发飞轮分析。返回结果或 None"""
        ok, reason, total = self.engine.should_auto_run(self.min_new)
        if not ok:
            return None
        result = self.engine.run_all()
        applied = self.engine.apply_auto_actions(result)
        result["applied_result"] = applied
        self.engine.mark_analysis_done(total)
        return result


# ── CLI ─────────────────────────────────────────────────

if __name__ == "__main__":
    engine = FlywheelEngine()
    sessions = engine.load_all_sessions()
    print(f"📊 飞轮 v2 状态：{len(sessions)} 条会话, {engine.get_user_count()} 个用户")

    stats = engine.get_stats()
    print(f"   数据充足度: {stats.data_sufficiency}")
    print(f"   近况: {stats.recent_trend}")

    if len(sessions) < 10:
        print(f"\n[!] 数据不足（需 ≥10）。正在用模拟数据自测...")
        import random
        exp_levels = ["0-1", "1-3", "3-5", "5-10", "10+"]
        for i in range(15):
            uid = f"mock_user_{random.randint(1, 8)}"
            mock_status = {}
            for nid, node in engine.nodes.items():
                mock_status[nid] = {
                    "correct": random.randint(0, 3), "total": 3,
                    "confidence": random.random(),
                    "mastered": random.random() > 0.45,
                    "layer": node.get("layer", "foundation")
                }
            mock_answers = {}
            for j, t in enumerate(engine.tests):
                qid = t.get("id", f"Q{j:04d}")
                ci = t.get("correct_index", t.get("correct", 0))
                mock_answers[qid] = ci if random.random() > 0.4 else random.randint(0, 3)

            engine.save_session({
                "session_id": f"mock_v2_{i:03d}",
                "profile": {"name": f"测试_{uid}", "years": random.choice(exp_levels)},
                "answers": mock_answers,
                "node_status": mock_status,
                "blind_suspects": {},
                "overall_score": random.uniform(30, 95)
            }, user_id=uid)
        print(f"   已生成 15 条模拟记录。")

    report = engine.run_all()
    if report["status"] == "ok":
        print(f"\n═══ 飞轮综合报告（{report['sessions_analyzed']} 次/{report['users']} 用户）═══")
        g = report["flywheel_1_graph"]
        print(f"  飞轮一: {len(g.get('layer_adjustments',[]))} 层级 + {len(g.get('weight_adjustments',[]))} 权重 + {len(g.get('dependency_suggestions',[]))} 依赖")
        pm = g.get("population_metrics", {})
        print(f"    群体指标: {pm.get('total_users',0)} 用户, 节点均值信度 {pm.get('avg_node_confidence',0):.3f}")

        b = report["flywheel_2_blind"]
        print(f"  飞轮二: 嫌疑人 {b.get('summary',{}).get('total_suspects',0)}, 误判率 {b.get('summary',{}).get('fp_rate',0):.1%}")

        q = report["flywheel_3_questions"]
        print(f"  飞轮三: {q.get('total_evaluated',0)} 题, 差题 {len(q.get('bad_questions',[]))}")

        acts = report.get("auto_actions", [])
        print(f"\n  自动动作 ({len(acts)} 条):")
        for a in acts[:5]:
            print(f"    • {a['action']}: {a.get('node_id', a.get('source', a.get('question_id','?')))} @conf={a.get('confidence',0):.2f}")
        if len(acts) > 5:
            print(f"    … 等 {len(acts)} 条")

        # 试写
        applied = engine.apply_auto_actions(report)
        print(f"\n  ✅ 已自动写入 {applied['applied']} 条校准结果")
    else:
        print(f"\n⚠ {report['message']}")
