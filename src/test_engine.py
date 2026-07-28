"""
试题引擎：生成反盲选试题 + 交叉验证检测

核心逻辑：
- 每个知识节点生成2-3道题（不同问法、不同角度）
- 答对A题但答错B题同一知识点的 → 标记为盲选嫌疑
- 关联知识点：选了"精通分红险"但答不出"IRR计算" → 矛盾标记
"""

import json
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

ROLE_DIR = Path(__file__).parent.parent / "data" / "roles"


@dataclass
class Question:
    """一道试题"""
    id: str
    node_id: str            # 关联的知识节点
    question_type: str      # knowledge | scenario | calculation
    question: str
    options: List[str]
    correct_index: int
    difficulty: int         # 1-5, 匹配节点所在层级


@dataclass
class TestSession:
    """一次测试会话"""
    role: str
    user_name: str
    user_age: int
    user_gender: str
    questions: List[Question] = field(default_factory=list)
    answers: Dict[str, int] = field(default_factory=dict)  # question_id → chosen_index
    blind_suspects: Dict[str, List[str]] = field(default_factory=dict)  # node_id → reasons


class TestEngine:
    """试题引擎"""

    def __init__(self, role: str = "insurance-agent"):
        self.role = role
        self.nodes = self._load_nodes()
        self.edges = self._load_edges()
        self.tests = self._load_tests()
        self.questions: List[Question] = []

    def _load_nodes(self) -> List[dict]:
        path = ROLE_DIR / self.role / "nodes.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def _load_tests(self) -> List[dict]:
        path = ROLE_DIR / self.role / "tests.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def _load_edges(self) -> List[dict]:
        path = ROLE_DIR / self.role / "edges.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def generate_questions(self, user_profile: dict) -> List[Question]:
        """
        根据用户画像生成试题。
        优先使用 tests.json 中的真实试题，若无则生成占位题。
        """
        questions = []

        # 如果有真实题库，直接使用
        if self.tests:
            for t in self.tests:
                q = Question(
                    id=f"Q{len(questions)+1:04d}",
                    node_id=t["node_id"],
                    question_type=t.get("type", "knowledge"),
                    question=t["question"],
                    options=t["options"],
                    correct_index=t["correct"],
                    difficulty=t.get("difficulty", 2)
                )
                questions.append(q)
            random.shuffle(questions)
            self.questions = questions
            return questions

        # 无题库时 fallback 到占位模板（保持向后兼容）
        q_idx = 0
        for node in self.nodes:
            node_id = node["id"]
            layer = node["layer"]
            label = node["label"]

            if layer == "foundation":
                num_q = 3
                base_diff = 1
            elif layer == "advanced":
                num_q = 2
                base_diff = 3
            else:
                num_q = 2
                base_diff = 4

            for i in range(num_q):
                q_idx += 1
                q = self._build_question(node, i, q_idx, base_diff)
                questions.append(q)

        random.shuffle(questions)
        self.questions = questions
        return questions

    def _build_question(self, node: dict, variant: int, q_idx: int, base_diff: int) -> Question:
        """为单个知识节点构建一道题（模板化，以后替换为LLM生成）"""
        node_id = node["id"]
        label = node["label"]
        content = node.get("content", "")

        templates = {
            0: {
                "type": "knowledge",
                "templates": [
                    f"关于「{label}」，以下说法正确的是？",
                    f"以下哪项是「{label}」的核心内容？",
                ]
            },
            1: {
                "type": "scenario",
                "templates": [
                    f"客户询问「{label}」相关问题时，代理人应该怎么回答？",
                    f"以下场景中，哪项需要用到了解「{label}」？",
                ]
            },
            2: {
                "type": "application",
                "templates": [
                    f"以下哪种做法违反了「{label}」的要求？",
                    f"以下哪项最能体现对「{label}」的掌握？",
                ]
            }
        }

        t = templates.get(variant, templates[0])
        q_text = t["templates"][variant % len(t["templates"])]

        # 生成选项（占位——正式版由LLM生成）
        options = [
            f"A. {label}的正确理解方式一（占位）",
            f"B. {label}的常见误解（占位）",
            f"C. {label}的不相关描述（占位）",
            f"D. {label}的另一种理解（占位）",
        ]

        return Question(
            id=f"Q{q_idx:04d}",
            node_id=node_id,
            question_type=t["type"],
            question=q_text,
            options=options,
            correct_index=0,  # 占位，正式版由LLM确定
            difficulty=base_diff + variant
        )

    def detect_blind_guessing(self, session: TestSession) -> Dict[str, List[str]]:
        """
        反盲选检测：
        1. 同节点多题交叉验证：答对一题但答错另一题 → 嫌疑
        2. 关联节点验证：选了A节点但答错B节点 → 矛盾标记
        """
        suspects = {}

        # 1. 同节点交叉验证
        node_answers: Dict[str, List[tuple]] = {}
        for q in self.questions:
            ans = session.answers.get(q.id)
            if ans is not None:
                node_answers.setdefault(q.node_id, []).append((q.id, ans == q.correct_index))

        for node_id, results in node_answers.items():
            if len(results) >= 2:
                correct_count = sum(1 for _, correct in results if correct)
                if 0 < correct_count < len(results):
                    suspects[node_id] = [f"节点交叉检验：{len(results)}题中答对{correct_count}题，存在盲选嫌疑"]

        # 2. 关联节点验证
        for edge in self.edges:
            src = edge["source"]
            tgt = edge["target"]
            src_passed = all(ans for _, ans in node_answers.get(src, [(None, False)]))
            tgt_failed = not any(ans for _, ans in node_answers.get(tgt, [(None, False)])) if node_answers.get(tgt) else True

            if src_passed and tgt_failed and node_answers.get(tgt):
                suspects.setdefault(src, []).append(f"关联矛盾：自称掌握「{src}」但答错依赖节点「{tgt}」")

        session.blind_suspects = suspects
        return suspects

    def score(self, session: TestSession) -> dict:
        """评分：生成实际知识图谱 + 完整度评估"""
        node_status = {}  # node_id → {correct, total, confidence}
        total_correct = 0
        total_questions = 0

        for q in self.questions:
            ans = session.answers.get(q.id)
            if ans is None:
                continue
            total_questions += 1
            correct = (ans == q.correct_index)
            if correct:
                total_correct += 1

            status = node_status.setdefault(q.node_id, {"correct": 0, "total": 0})
            status["total"] += 1
            if correct:
                status["correct"] += 1

        # 每个节点的置信度
        for node_id, status in node_status.items():
            status["confidence"] = status["correct"] / status["total"] if status["total"] > 0 else 0
            status["mastered"] = status["confidence"] >= 0.67

        # 分层统计
        layer_stats = {"foundation": {"correct": 0, "total": 0},
                       "advanced": {"correct": 0, "total": 0},
                       "transcendent": {"correct": 0, "total": 0}}
        for node in self.nodes:
            layer = node["layer"]
            ns = node_status.get(node["id"], {"correct": 0, "total": 0})
            layer_stats[layer]["total"] += ns["total"]
            layer_stats[layer]["correct"] += ns["correct"]

        report = {
            "total_questions": total_questions,
            "total_correct": total_correct,
            "overall_score": total_correct / total_questions if total_questions > 0 else 0,
            "layer_scores": {
                layer: {
                    "score": s["correct"] / s["total"] if s["total"] > 0 else 0,
                    "completed": f"{s['correct']}/{s['total']}"
                }
                for layer, s in layer_stats.items()
            },
            "node_status": node_status,
            "mastered_nodes": [nid for nid, s in node_status.items() if s.get("mastered")],
            "gap_nodes": [nid for nid, s in node_status.items() if not s.get("mastered")],
            "blind_suspects": session.blind_suspects
        }
        return report


if __name__ == "__main__":
    engine = TestEngine()
    print(f"已加载 {len(engine.nodes)} 个知识节点")
    print(f"已加载 {len(engine.edges)} 条知识关系")
    qs = engine.generate_questions({"name": "测试", "age": 28})
    print(f"已生成 {len(qs)} 道试题")
