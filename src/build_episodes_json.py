"""
将20期伴读markdown脚本转为前端可用JSON。
输出: D:\kgds\data\reading\thinking-fast-slow\episodes.json
每期结构: {episode, title, concept, sections: {hook, reading, echo, focus, quotes[]}, kgds_anchor}
"""
import re
import json
from pathlib import Path

SRC_DIR = Path(r"D:\kgds\data\books\thinking-fast-slow")
OUT_DIR = Path(r"D:\kgds\data\reading\thinking-fast-slow")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SECTION_MAP = {
    "【开场钩子】": "hook",
    "【书中精读】": "reading",
    "【回声】": "echo",
    "【敲重点】": "focus",
    "【金句海报】": "quotes",
}

def parse_episode(text: str, ep_num: int):
    """解析单期markdown"""
    # 标题
    m = re.search(rf"## 第{ep_num:02d}期[：:](.+)", text)
    title = m.group(1).strip() if m else f"第{ep_num:02d}期"

    # yaml块
    yaml_m = re.search(r"```yaml\n(.*?)```", text, re.S)
    concept, kgds_anchor = "", ""
    if yaml_m:
        y = yaml_m.group(1)
        c = re.search(r"concept:\s*(.+)", y)
        k = re.search(r"kgds_anchor:\s*(.+)", y)
        concept = c.group(1).strip() if c else ""
        kgds_anchor = k.group(1).strip() if k else ""

    # 各段落
    sections = {}
    parts = re.split(r"(【[^】]+】)", text)
    cur_key = None
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in SECTION_MAP:
            cur_key = SECTION_MAP[p]
            sections.setdefault(cur_key, "")
        elif cur_key:
            sections[cur_key] = sections.get(cur_key, "") + ("\n" if sections.get(cur_key) else "") + p

    # quotes 拆成列表
    if "quotes" in sections:
        qs = []
        for line in sections["quotes"].splitlines():
            line = line.strip()
            m = re.match(r"^\d+[.、]\s*(.+)", line)
            if m:
                qs.append(m.group(1).strip())
        sections["quotes"] = qs

    return {
        "episode": ep_num,
        "title": title,
        "concept": concept,
        "kgds_anchor": kgds_anchor,
        "sections": sections,
    }

def main():
    episodes = {}
    for f in sorted(SRC_DIR.glob("episodes_*.md")):
        text = f.read_text(encoding="utf-8")
        # 按期号切分
        splits = re.split(r"(?=## 第\d{2}期[：:])", text)
        for s in splits:
            m = re.match(r"## 第(\d{2})期[：:]", s)
            if not m:
                continue
            ep = int(m.group(1))
            if ep in episodes:
                continue
            episodes[ep] = parse_episode(s, ep)

    result = {
        "book": "思考，快与慢",
        "author": "丹尼尔·卡尼曼",
        "total_episodes": len(episodes),
        "episodes": [episodes[i] for i in sorted(episodes.keys())],
    }

    out = OUT_DIR / "episodes.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(episodes)} episodes -> {out}")
    for e in result["episodes"]:
        secs = list(e["sections"].keys())
        print(f"  ep{e['episode']:02d} | {e['title'][:30]} | sections: {secs} | quotes: {len(e['sections'].get('quotes', []))}")

if __name__ == "__main__":
    main()
