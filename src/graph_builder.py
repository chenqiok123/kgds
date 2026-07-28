"""
图谱构建器：应有知识图谱 vs 实际知识图谱

三个核心功能：
1. 加载岗位的应有知识图谱
2. 根据答题结果生成实际知识图谱（点亮 vs 未点亮）
3. 输出差值分析（缺失节点、超标节点）
"""

import json
from pathlib import Path
from typing import Dict, List

ROLE_DIR = Path(__file__).parent.parent / "data" / "roles"


class GraphBuilder:
    """知识图谱构建器"""

    def __init__(self, role: str = "insurance-agent"):
        self.role = role
        self.nodes = self._load("nodes.json")
        self.edges = self._load("edges.json")

    def _load(self, filename: str):
        path = ROLE_DIR / self.role / filename
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def build_ideal_graph(self) -> dict:
        """构建应有知识图谱（所有节点半透明灰色，作为背景参照）"""
        return {
            "nodes": [
                {
                    "id": n["id"],
                    "label": n["label"],
                    "layer": n["layer"],
                    "color": "#555555",
                    "weight": n["weight"],
                    "opacity": 0.3
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e["source"],
                    "target": e["target"],
                    "strength": e["strength"],
                    "color": "#333333",
                    "opacity": 0.3
                }
                for e in self.edges
            ]
        }

    def build_actual_graph(self, node_status: Dict[str, dict]) -> dict:
        """根据答题结果构建实际知识图谱"""
        nodes_out = []
        edges_out = []

        for node in self.nodes:
            nid = node["id"]
            status = node_status.get(nid, {})
            confidence = status.get("confidence", 0)
            mastered = status.get("mastered", False)

            # 已掌握 → 原色亮色，未掌握 → 暗色
            if mastered and confidence >= 0.67:
                color = node.get("color", "#4A90E2")
                opacity = 0.8 + confidence * 0.2
                weight = node.get("weight", 3) * (0.8 + confidence * 1.2)
            elif confidence > 0:
                color = "#FFD700"  # 金色 = 部分掌握
                opacity = 0.5 + confidence * 0.3
                weight = node.get("weight", 3) * (0.5 + confidence * 0.5)
            else:
                color = "#333333"
                opacity = 0.2
                weight = node.get("weight", 3) * 0.4

            nodes_out.append({
                "id": node["id"],
                "label": node["label"],
                "layer": node["layer"],
                "color": color,
                "weight": weight,
                "opacity": opacity,
                "confidence": confidence,
                "mastered": mastered
            })

        # 连线：两端都mastered → 亮色；至少一端mastered → 中等；都未 → 暗
        mastered_set = {nid for nid, s in node_status.items() if s.get("mastered")}
        for edge in self.edges:
            src = edge["source"]
            tgt = edge["target"]
            both = src in mastered_set and tgt in mastered_set
            one = src in mastered_set or tgt in mastered_set

            if both:
                edge_color = "#5B9BD5"
                edge_opacity = 0.8
            elif one:
                edge_color = "#5B9BD5"
                edge_opacity = 0.4
            else:
                edge_color = "#333333"
                edge_opacity = 0.15

            edges_out.append({
                "source": src,
                "target": tgt,
                "strength": edge["strength"],
                "label": edge.get("label", ""),
                "color": edge_color,
                "opacity": edge_opacity
            })

        return {"nodes": nodes_out, "edges": edges_out}

    def analyze_gaps(self, node_status: Dict[str, dict]) -> dict:
        """差值分析"""
        gap_nodes = []
        surplus_nodes = []

        for node in self.nodes:
            nid = node["id"]
            status = node_status.get(nid, {})
            confidence = status.get("confidence", 0)

            if confidence < 0.5:
                gap_nodes.append({
                    "id": nid,
                    "label": node["label"],
                    "layer": node["layer"],
                    "confidence": confidence,
                    "priority": "high" if node["layer"] == "foundation" else "medium"
                })
            elif confidence >= 0.8 and node["layer"] == "transcendent":
                surplus_nodes.append({
                    "id": nid,
                    "label": node["label"],
                    "confidence": confidence
                })

        return {
            "total_gaps": len(gap_nodes),
            "critical_gaps": [g for g in gap_nodes if g["priority"] == "high"],
            "gaps_by_layer": {
                "foundation": [g for g in gap_nodes if g["layer"] == "foundation"],
                "advanced": [g for g in gap_nodes if g["layer"] == "advanced"],
                "transcendent": [g for g in gap_nodes if g["layer"] == "transcendent"]
            },
            "surplus_nodes": surplus_nodes
        }

    def recommend_books(self, gaps: dict) -> List[dict]:
        """根据缺口推荐书籍（规则匹配，后续可接入推荐系统）"""
        book_map = {
            "foundation": [
                {"title": "保险原理与实务", "author": "中国保险行业协会", "reason": "覆盖所有保险产品基础。", "url": ""},
                {"title": "保险法", "author": "全国人大", "reason": "掌握保险法律红线。", "url": ""},
            ],
            "advanced": [
                {"title": "理财规划师基础知识", "author": "CHFP教材", "reason": "系统学习家庭财务分析与理财规划。", "url": ""},
                {"title": "保险学（第六版）", "author": "魏华林", "reason": "深入理解保险定价机制与产品设计。", "url": ""},
            ],
            "transcendent": [
                {"title": "家族信托与财富管理", "author": "韩良", "reason": "理解保险金信托与家族传承的法律架构。", "url": ""},
                {"title": "资产配置的艺术", "author": "David Darst", "reason": "掌握资产配置理论与实务。", "url": ""},
            ]
        }

        recommendations = []
        for layer in ["foundation", "advanced", "transcendent"]:
            if gaps["gaps_by_layer"].get(layer):
                recommendations.extend(book_map.get(layer, []))

        return recommendations


if __name__ == "__main__":
    builder = GraphBuilder()
    print(f"应有知识图谱：{len(builder.nodes)} 节点, {len(builder.edges)} 连线")

    # 模拟答题结果
    mock_status = {}
    for node in builder.nodes:
        import random
        mock_status[node["id"]] = {
            "correct": random.randint(0, 2),
            "total": 2,
            "confidence": random.random(),
            "mastered": random.random() > 0.5
        }

    actual = builder.build_actual_graph(mock_status)
    gaps = builder.analyze_gaps(mock_status)
    books = builder.recommend_books(gaps)

    print(f"空缺节点：{gaps['total_gaps']}")
    print(f"推荐书籍：{len(books)} 本")
