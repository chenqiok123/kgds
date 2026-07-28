"""
变体生成器：利用 LLM 能力将原始试题转换为等价变体。

原理：Item Cloning（试题克隆）——保持测试的知识点不变，改变表面特征。
这是心理测量学中构建平行题库的标准方法。

每次测试生成 1/3 的变体题，使每份试卷独一无二，同时保持测量等价性。
"""

import json
import random
import re
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

# LLM 变体（DeepSeek API），不可用时自动降级规则引擎
try:
    from llm_variant import generate_llm_variants, llm_available
except ImportError:
    try:
        from .llm_variant import generate_llm_variants, llm_available
    except ImportError:
        generate_llm_variants = None
        def llm_available(): return False

ROLE_DIR = Path(__file__).parent.parent / "data" / "roles"

# LLM 变体策略：按层级决定使用 LLM 的概率
# L1 基础层：纯规则引擎（概念题变体规则已够用）
# L2 提升层：30% LLM（场景题受益于真实化改写）
# L3 升华层：100% LLM（高阶场景题需要 AI 级的真实感）
LLM_LAYER_PROB = {"L1": 0.0, "L2": 0.3, "L3": 1.0}


# ── 变体模板库 ──────────────────────────────────────────
# 每个模板包含：原始模式 → 变体替换集
# 这些是经过 LLM 深度思考后固化的高频变换规则

NAME_POOL_MALE = ["张先生", "李先生", "王先生", "赵先生", "陈先生", "周先生", "吴先生", "郑先生"]
NAME_POOL_FEMALE = ["张女士", "李女士", "王女士", "赵女士", "陈女士", "周女士", "林女士", "黄女士"]
AGE_RANGES = [(26, 35), (30, 40), (32, 42), (28, 38), (35, 45), (40, 50), (45, 55)]
LOAN_AMOUNTS = [60, 80, 100, 120, 150, 180, 200, 250]
YEAR_RANGES = [(10, 15), (15, 20), (20, 25), (25, 30)]
CHILD_AGES = [1, 2, 3, 5, 8, 10, 12]
INCOME_LEVELS = [15, 20, 30, 50, 80, 100, 200]
PREMIUM_BUDGETS = [5000, 8000, 10000, 15000, 20000, 30000]
CITY_NAMES = ["北京", "上海", "深圳", "广州", "杭州", "成都", "武汉", "南京"]

# 场景变换模式
SCENARIO_TRANSFORMS = {
    "房贷": ["房贷", "按揭贷款", "购房贷款", "住房贷款", "公积金+商贷"],
    "孩子": ["孩子", "子女", "小孩", "宝贝", "独生子"],
    "退休": ["退休", "养老", "晚年生活", "金色年华", "银发阶段"],
    "创业": ["创业", "开办公司", "自主经营", "下海经商"],
    "遗产": ["遗产", "家产", "财富传承", "祖业"],
    "生病": ["生病", "患病", "确诊", "查出疾病", "健康出问题"],
}

# 产品名称变换
PRODUCT_ALIASES = {
    "定期寿险": ["定期寿险", "定寿", "消费型寿险", "纯保障寿险"],
    "终身寿险": ["终身寿险", "终身寿", "储蓄型寿险", "增额终身寿险"],
    "重疾险": ["重疾险", "重大疾病保险", "大病保险", "重疾保障"],
    "百万医疗": ["百万医疗险", "百万医疗", "高额医疗险", "大额医疗险"],
    "意外险": ["意外险", "意外伤害险", "意外保障", "综合意外险"],
    "年金险": ["年金险", "养老年金", "年金保险", "退休年金"],
    "万能险": ["万能险", "万能账户", "万能型保险"],
    "投连险": ["投连险", "投资连结保险", "投连"],
}

# 模糊化变换：将具体数字替换为区间或近似值
FUZZY_NUMBERS = {
    100: ["100万左右", "约100万", "100万上下", "近百万"],
    200: ["200万左右", "约200万", "近200万"],
    500: ["500万左右", "约500万", "近500万", "五百万上下"],
    1000: ["1000万左右", "约1000万", "近千万"],
    30: ["30岁出头", "30来岁", "30岁左右"],
    50: ["50岁左右", "年过半百", "50来岁"],
    20: ["20年", "20个年头", "20年时间"],
}


def _random_age():
    lo, hi = random.choice(AGE_RANGES)
    return str(random.randint(lo, hi))


def _random_name(gender_hint: Optional[str] = None):
    if gender_hint in ("男", "male"):
        return random.choice(NAME_POOL_MALE)
    if gender_hint in ("女", "female"):
        return random.choice(NAME_POOL_FEMALE)
    return random.choice(NAME_POOL_MALE + NAME_POOL_FEMALE)


def _random_loan():
    """生成房贷金额 + 年限组合"""
    amount = random.choice(LOAN_AMOUNTS)
    years = random.choice([10, 15, 20, 25, 30])
    return amount, years


def _fuzz_number(text: str) -> str:
    """将文本中的精确数字进行模糊化变换"""
    for num, variants in FUZZY_NUMBERS.items():
        for fmt in (str(num), f"{num}万", f"{num}岁", f"{num}年"):
            if fmt in text:
                return text.replace(fmt, random.choice(variants), 1)
    return text


def _rotate_options(options: List[str], correct_index: int) -> tuple:
    """
    旋转选项顺序 + 随机替换干扰项措辞。
    保持正确选项内容不变，但可能改变其在列表中的位置。
    """
    opts = list(options)
    correct_text = opts[correct_index]

    # 对每个干扰项做措辞微调
    for i in range(len(opts)):
        if i == correct_index:
            continue
        # 10% 概率完全替换干扰项, 30% 概率微调措辞
        r = random.random()
        if r < 0.15 and len(opts[i]) < 30:
            # 生成新干扰项（基于常识反转）
            if "正确" in opts[i]:
                opts[i] = opts[i].replace("正确", random.choice(["合理", "可行", "最佳选择"]))
            elif "错误" in opts[i]:
                opts[i] = opts[i].replace("错误", random.choice(["不合适", "有风险", "不推荐"]))
            elif "不需要" in opts[i]:
                opts[i] = opts[i].replace("不需要", random.choice(["可以不考虑", "不是必须", "可以省略"]))
        elif r < 0.4:
            opts[i] = _fuzz_number(opts[i])

    # 打乱顺序
    indices = list(range(len(opts)))
    random.shuffle(indices)
    new_opts = [opts[i] for i in indices]
    new_correct = indices.index(correct_index)

    return new_opts, new_correct


def generate_variant(original: dict) -> dict:
    """
    对一道原始试题生成等价变体。

    核心原则：
    - 保持 node_id 和 type 不变（测的是同一个知识点）
    - 改变表面特征：人名、年龄、金额、场景细节
    - 改变选项措辞和排列顺序
    - 保持 difficulty 不变
    """
    variant = {
        "node_id": original["node_id"],
        "type": original["type"],
        "difficulty": original["difficulty"],
        "is_variant": True,
        "original_question": original.get("question", ""),
    }

    q = original.get("question", "")
    opts = list(original.get("options", []))
    correct = original.get("correct", 0)

    # ── 层次1：替换人名 ──
    for name in NAME_POOL_MALE + NAME_POOL_FEMALE:
        if name in q:
            q = q.replace(name, _random_name(
                "男" if name in NAME_POOL_MALE else "女"))
            break

    # ── 层次2：替换年龄 ──
    for age_pat in [r'\d+岁', r'\d+周岁']:
        match = re.search(age_pat, q)
        if match:
            q = q[:match.start()] + _random_age() + "岁" + q[match.end():]
            break

    # ── 层次3：替换金额（房贷、保额、收入等） ──
    money_pats = [
        (r'(\d+)万', lambda m: str(int(m.group(1)) + random.choice([-20, -10, 10, 20, 30])) + "万"),
        (r'(\d+)元', lambda m: str(int(m.group(1)) + random.choice([-500, 0, 500, 1000])) + "元"),
    ]
    for pat, repl in money_pats:
        q = re.sub(pat, repl, q, count=1)

    # ── 层次4：场景词变换 ──
    for key, variants in SCENARIO_TRANSFORMS.items():
        if key in q:
            q = q.replace(key, random.choice(variants), 1)
            break

    # ── 层次5：产品名称别名 ──
    for prod, aliases in PRODUCT_ALIASES.items():
        if prod in q:
            alt = random.choice([a for a in aliases if a != prod])
            q = q.replace(prod, alt, 1)
            break

    # ── 层次6：模糊化数字 ──
    q = _fuzz_number(q)

    # ── 变换选项 ──
    new_opts, new_correct = _rotate_options(opts, correct)

    variant["question"] = q
    variant["options"] = new_opts
    variant["correct"] = new_correct

    return variant


def _node_layer(node_id: str) -> str:
    """从 node_id 前缀推断层级：L1/L2/L3"""
    if node_id and len(node_id) >= 2:
        return node_id[:2].upper()
    return "L1"


def _make_llm_variant(original: dict) -> Optional[dict]:
    """用 LLM 生成一个变体；失败返回 None（调用方降级）"""
    if not generate_llm_variants:
        return None
    variants = generate_llm_variants(original, count=1)
    if not variants:
        return None
    v = variants[0]
    return {
        "node_id": original["node_id"],
        "type": original["type"],
        "difficulty": original["difficulty"],
        "is_variant": True,
        "variant_source": "llm",
        "original_question": original.get("question", ""),
        "question": v["question"],
        "options": v["options"],
        "correct": v["correct"],
    }


def generate_test_with_variants(
    role: str = "insurance-agent",
    variant_ratio: float = 1/3,
    seed: Optional[int] = None,
    node_filter: Optional[set] = None,
    use_llm: bool = True
) -> List[dict]:
    """
    生成一次测试的试题集，其中 variant_ratio 比例的题为变体。

    Args:
        role: 岗位标识
        variant_ratio: 变体比例，默认 1/3
        seed: 随机种子（用于复现）
        node_filter: 可选，仅包含这些 node_id 的试题
        use_llm: 是否启用 LLM 变体（按 LLM_LAYER_PROB 分层概率），默认开
    """
    if seed is not None:
        random.seed(seed)

    tests_path = ROLE_DIR / role / "tests.json"
    if not tests_path.exists():
        return []

    originals = json.loads(tests_path.read_text(encoding="utf-8"))

    # 按 node_id 过滤
    if node_filter:
        originals = [q for q in originals if q.get("node_id") in node_filter]
    total = len(originals)
    variant_count = max(1, int(total * variant_ratio))

    # 随机选择 variant_count 道题做变体
    indices = list(range(total))
    random.shuffle(indices)
    variant_indices = set(indices[:variant_count])

    # 预检 LLM 可用性（无 Key 直接全规则，不浪费每题的超时等待）
    llm_ok = use_llm and llm_available()

    result = []
    for i, q in enumerate(originals):
        if i in variant_indices:
            qv = None
            # 按层级概率决定是否尝试 LLM
            if llm_ok:
                layer = _node_layer(q.get("node_id", ""))
                if random.random() < LLM_LAYER_PROB.get(layer, 0.0):
                    qv = _make_llm_variant(q)
            # LLM 未启用或失败 → 规则引擎兜底
            if qv is None:
                qv = generate_variant(q)
                qv["variant_source"] = "rule"
            qv["id"] = f"v_{i}"
            result.append(qv)
        else:
            q_copy = dict(q)
            q_copy["is_variant"] = False
            q_copy["id"] = f"q_{i}"
            result.append(q_copy)

    random.shuffle(result)
    return result


# ── 测试入口 ──
if __name__ == "__main__":
    # 测试变体生成
    random.seed(42)
    tests = generate_test_with_variants()
    variant_count = sum(1 for t in tests if t.get("is_variant"))
    print(f"总题数: {len(tests)}, 变体题: {variant_count}")

    print("\n── 原始题示例 ──")
    orig = [t for t in tests if not t.get("is_variant")][:2]
    for t in orig:
        print(f"  [{t['node_id']}] {t['question'][:60]}...")

    print("\n── 变体题示例 ──")
    variants = [t for t in tests if t.get("is_variant")][:2]
    for t in variants:
        print(f"  [{t['node_id']}] 原始: {t.get('original_question','')[:40]}...")
        print(f"        变体: {t['question'][:60]}...")
        print()
