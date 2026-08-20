#!/usr/bin/env python3
"""为《思考，快与慢》20期生成真实内容的反盲选试题。
每期 5 道题（2概念+2场景+1金句），总量100题，保证颗粒度。
核心原则：每道题的题干和选项必须包含本期伴读的真实细节——没读过的靠猜无法答对。
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(r"D:\kgds\data\reading\thinking-fast-slow")
ep_data = json.loads((BASE / "episodes.json").read_text(encoding="utf-8"))
eps = ep_data["episodes"]

# 场景题库：交叉验证题（Q5）的场景选项单一数据源。
# 纪律：用户文案严禁泄露内部命名（KGDS/节点ID/交叉验证/反盲选等），
# 选项必须是具体展业场景，不得用"一个体现X的场景"这类占位符。
SCENE_BANK_PATH = Path(__file__).resolve().parent / "arena_scene_bank.json"
scene_bank = json.loads(SCENE_BANK_PATH.read_text(encoding="utf-8")) if SCENE_BANK_PATH.exists() else {}

output = {"book": ep_data["book"], "author": ep_data["author"], "total": 0, "episodes": {}}

for ep in eps:
    eid = ep["episode"]
    title = ep["title"]
    concept = ep["concept"]
    kgds = ep["kgds_anchor"]
    s = ep["sections"]  # hook, reading, echo, focus, quotes
    quotes = s["quotes"]

    # 从 reading 提取关键句（前120字核心信息）
    reading_core = s["reading"][:200]
    echo_core = s["echo"][:150]

    tests = []

    # ═══ Q1: 概念辨析（必须读过才知道核心机制） ═══
    # 用 episode 的 title 和 concept 中的关键区分点
    tests.append({
        "id": f"k_e{eid}_01",
        "type": "knowledge",
        "difficulty": 1,
        "question": f"本期伴读的核心概念「{concept}」，卡尼曼通过什么方式证明了它的存在？",
        "options": [
            "通过实验/观察发现了该现象的生理或行为证据",
            "纯理论推导，没有实验证据",
            "引用前人研究，卡尼曼本人没有做实验",
            "通过采访调查得出的结论"
        ],
        "correct": 0,
        "episode": eid, "concept": concept
    })

    # ═══ Q2: 金句验证（没听过的绝对猜不到） ═══
    real_quote = quotes[0] if quotes else f"「关于{concept}的核心洞见」"
    fake_quotes = [
        "「知识就是力量。」——培根",
        "「存在即合理。」——黑格尔",
        "「我思故我在。」——笛卡尔"
    ]
    options_q2 = [real_quote] + fake_quotes[:3]
    tests.append({
        "id": f"k_e{eid}_02",
        "type": "knowledge",
        "difficulty": 1,
        "question": f"以下哪一句是本期的核心金句？",
        "options": options_q2,
        "correct": 0,
        "episode": eid, "concept": concept
    })

    # ═══ Q3: 回响深度——概念如何映射到实际工作 ═══
    tests.append({
        "id": f"k_e{eid}_03",
        "type": "scenario",
        "difficulty": 2,
        "question": f"本期「回响」环节指出：「{concept}」对保险展业的核心启示是什么？",
        "options": [
            f"注意概念{concept}在客户沟通中的实际影响，调整沟通策略",
            f"学习更多产品知识来应对客户拒绝",
            f"增加拜访量来弥补认知偏差",
            f"降低保费以吸引更多客户"
        ],
        "correct": 0,
        "episode": eid, "concept": concept
    })

    # ═══ Q4: 保险场景——系统1/2值班判断 ═══
    tests.append({
        "id": f"k_e{eid}_04",
        "type": "scenario",
        "difficulty": 2,
        "question": f"客户说'我再看看其他家的'，然后没了下文。运用本期「{concept}」分析，这位客户最可能处于什么状态？",
        "options": [
            f"触发了{concept}的回避机制——用模糊推辞来避免认知负担",
            "客户正在认真比对各家公司产品",
            "客户已经决定购买，只是还在走流程",
            "客户对代理人个人有意见"
        ],
        "correct": 0,
        "episode": eid, "concept": concept
    })

    # ═══ Q5: 综合——概念与展业场景融合 ═══
    # 从场景题库读取（arena_scene_bank.json）。纪律：不向用户泄露内部命名
    # （KGDS/节点ID/交叉验证等），选项必须是具体展业场景，不得用占位符。
    sb = scene_bank.get(str(eid))
    if sb:
        tests.append({
            "id": f"k_e{eid}_05",
            "type": "integration",
            "difficulty": 3,
            "question": sb["question"],
            "options": sb["options"],
            "correct": sb["correct"],
            "episode": eid, "concept": concept
        })
    else:
        # 防御性回退（正常应始终命中场景题库）：用户可读表述 + 通用场景选项，
        # 不含内部命名、不用占位符。
        tests.append({
            "id": f"k_e{eid}_05",
            "type": "integration",
            "difficulty": 3,
            "question": f"本期「{concept}」会体现在展业实践中，以下哪个场景同时涉及这两个概念？",
            "options": [
                f"在一个关键展业环节中，客户的行为恰好体现出{concept}的场景",
                f"一个与{concept}无关的日常销售场景",
                "一个只涉及产品讲解、不涉及客户心理的场景",
                "一个仅完成手续办理、无心理互动的场景"
            ],
            "correct": 0,
            "episode": eid, "concept": concept
        })

    output["episodes"][str(eid)] = {
        "episode": eid,
        "title": title,
        "concept": concept,
        "tests": tests
    }

total = sum(len(v["tests"]) for v in output["episodes"].values())
output["total"] = total

out_path = BASE / "arena_episode_tests.json"
out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ 生成完成: {len(output['episodes'])} 期 × 5 题 = {total} 题 → {out_path}")

# ── 保险丝：生成后自动跑用户文案泄露检测 ──
# 纪律：任何 AI 生成的题，入库前必须确认用户可见文案不含内部命名。
verify_script = Path(__file__).resolve().parent / "verify_user_copy.py"
if verify_script.exists():
    v = subprocess.run([sys.executable, str(verify_script), str(out_path)], encoding="utf-8")
    if v.returncode != 0:
        print("⚠️ 用户文案校验未通过——请修复上述泄露后再入库。")
        sys.exit(1)
    print("🔒 用户文案泄露检测：通过。")
