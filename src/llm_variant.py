"""
LLM 变体生成器 — DeepSeek API 实时生成等价变体题

设计原则：
  1. 零依赖：仅用标准库 urllib 调用 OpenAI 兼容接口
  2. 成本可控：一次 API 调用生成 3 个变体，缓存复用（同一原题只调一次）
  3. 安全降级：API 失败/超时 → 返回 None → 调用方回退规则引擎，测试不中断
  4. 密钥安全：仅从环境变量 DEEPSEEK_API_KEY 读取，绝不硬编码

缓存结构：data/cache/llm_variants/{content_hash}.json
  {"created_at": ..., "variants": [{question, options, correct}, ...]}
"""

import json
import os
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "llm_variants"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"   # V3 系列，性价比最优
TIMEOUT = 8               # 秒，超时即降级
VARIANTS_PER_CALL = 3     # 一次调用生成的变体数
CACHE_TTL_HOURS = 24 * 30  # 缓存有效期（30天：变体池复用，而非一次性）

SYSTEM_PROMPT = """你是保险行业的测评命题专家，精通心理测量学的试题克隆（Item Cloning）技术。
你的任务是将一道保险代理人考题改写为多个等价变体题。

改写规则（必须全部遵守）：
1. 考查的知识点和正确答案的逻辑完全不变
2. 变换表面特征：客户姓名、年龄、性别、职业、收入、城市、场景细节
3. 变换表述方式：句式重组、同义替换、主动被动转换
4. 选项措辞可以调整，但正确选项必须保持实质正确，干扰项必须保持实质错误
5. 选项数量不变，correct 字段指向改写后正确选项的下标（从0开始）
6. 变体之间要有明显差异（不同客户画像、不同场景）

输出严格 JSON（不要输出任何其他文字）：
{"variants": [{"question": "题干", "options": ["A选项", "B选项", "C选项", "D选项"], "correct": 0}, ...]}"""


def _api_key() -> str:
    """从环境变量读取 API Key（本地开发可用 .env 文件）"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _sanitize_json(text: str) -> str:
    """清洗 LLM 输出中的非法 JSON 片段：去掉尾逗号、注释、多余空白"""
    import re as _re
    t = text.strip()
    # 去掉 // 与 /* */ 注释
    t = _re.sub(r"/\*.*?\*/", "", t, flags=_re.S)
    t = _re.sub(r"//[^\n]*", "", t)
    # 去掉对象/数组末尾的逗号: ,} -> }  ,] -> ]
    t = _re.sub(r",\s*([}\]])", r"\1", t)
    return t


def _content_hash(question: dict) -> str:
    """原题内容哈希，作为缓存键"""
    raw = question.get("question", "") + "|" + "|".join(question.get("options", []))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_cache(qhash: str):
    fp = CACHE_DIR / f"{qhash}.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        created = datetime.fromisoformat(data.get("created_at", "2000-01-01"))
        age_hours = (datetime.now() - created).total_seconds() / 3600
        if age_hours > CACHE_TTL_HOURS:
            return None
        variants = data.get("variants", [])
        return variants if variants else None
    except Exception:
        return None


def _save_cache(qhash: str, variants: list):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fp = CACHE_DIR / f"{qhash}.json"
        fp.write_text(json.dumps({
            "created_at": datetime.now().isoformat(),
            "variants": variants
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[llm_variant] cache save error: {e}")


def _call_deepseek(original: dict) -> list:
    """调用 DeepSeek API 生成变体，返回变体列表；失败返回 None"""
    key = _api_key()
    if not key:
        return None

    user_prompt = f"""请将以下原题改写为 {VARIANTS_PER_CALL} 个等价变体：

原题：
{json.dumps({"question": original.get("question", ""), "options": original.get("options", []), "correct": original.get("correct", 0)}, ensure_ascii=False, indent=2)}

要求输出 {VARIANTS_PER_CALL} 个变体，严格 JSON 格式。"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.9,   # 高温度保证变体多样性
        "max_tokens": 2400
    }

    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 容错：LLM 常输出非法尾逗号/注释，清洗后重试一次
            cleaned = _sanitize_json(content)
            data = json.loads(cleaned)
        variants = data.get("variants", [])
        # 校验结构完整性
        valid = []
        for v in variants:
            if (isinstance(v.get("question"), str) and v["question"].strip()
                    and isinstance(v.get("options"), list) and len(v["options"]) >= 2
                    and isinstance(v.get("correct"), int)
                    and 0 <= v["correct"] < len(v["options"])):
                valid.append({
                    "question": v["question"].strip(),
                    "options": [str(o) for o in v["options"]],
                    "correct": v["correct"]
                })
        return valid if valid else None
    except urllib.error.HTTPError as e:
        print(f"[llm_variant] API HTTP {e.code}: {e.read()[:200]}")
        return None
    except Exception as e:
        print(f"[llm_variant] API error: {type(e).__name__}: {e}")
        return None


def generate_llm_variants(original: dict, count: int = 1) -> list:
    """
    为一道原题生成 count 个 LLM 变体。
    优先用缓存；缓存不足时调用 API 补充并写缓存。
    完全失败时返回空列表（调用方负责降级到规则引擎）。

    返回: [{question, options, correct}, ...] 长度 <= count
    """
    qhash = _content_hash(original)
    cached = _load_cache(qhash) or []

    if len(cached) < count:
        fresh = _call_deepseek(original)
        if fresh:
            # 合并去重（按题干文本）
            seen = {v["question"] for v in cached}
            for v in fresh:
                if v["question"] not in seen:
                    cached.append(v)
                    seen.add(v["question"])
            _save_cache(qhash, cached)

    return cached[:count]


def llm_available() -> bool:
    """快速检查 LLM 是否可用（有 Key 即视为可用，不预发请求）"""
    return bool(_api_key())


# ── 测试入口 ──
if __name__ == "__main__":
    test_q = {
        "node_id": "L1-01",
        "type": "knowledge",
        "difficulty": 1,
        "question": "终身寿险与定期寿险最本质的区别是什么？",
        "options": ["终身寿险有现金价值而定期寿险通常没有", "终身寿险保费比定期寿险便宜", "定期寿险可以保障终身", "终身寿险只赔意外身故"],
        "correct": 0
    }
    print(f"LLM available: {llm_available()}")
    vs = generate_llm_variants(test_q, count=2)
    print(f"variants: {len(vs)}")
    for i, v in enumerate(vs):
        print(f"\n[{i+1}] {v['question']}")
        for j, o in enumerate(v['options']):
            mark = " ✓" if j == v["correct"] else ""
            print(f"    {chr(65+j)}. {o}{mark}")
