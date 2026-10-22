"""자막 줄 끊기를 **대표님이 손으로 끊은 자막**에 대고 채점한다.

test_korean.py 는 내가 적은 41개 자리를 지키는지 본다. 이건 그보다 넓다 —
대표님 완성본의 문장을 통째로 korean.chunk 에 다시 넣어 보고, 대표님이 끊은
자리와 얼마나 같은지 센다. 규칙을 고칠 때마다 이 숫자가 올라가야 한다.

정답지는 tools/fixtures/breaks_*.json (드래프트가 지워져도 남도록 떠 둔다).
없으면 캡컷 드래프트에서 새로 뜬다.

    python tools/test_breaks.py              채점
    python tools/test_breaks.py --max 26     한 줄 최대 글자수를 바꿔 본다
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from capcut_agent import korean  # noqa: E402

FIX = HERE / "fixtures"
DRAFTS = Path.home() / "AppData/Local/CapCut/User Data/Projects/com.lveditor.draft"
SOURCES = {"풀링3": "풀링3", "0916": "20260916_152258_0916_1613"}

_END = re.compile(r"([.?!]|(요|다|죠|까|냐|네|니다))\s*$")


def lines_from_draft(name: str) -> list[str]:
    d = json.loads((DRAFTS / name / "draft_content.json").read_text(encoding="utf-8"))
    tm = {m["id"]: m for m in d["materials"]["texts"]}
    tt = max((t for t in d["tracks"] if t["type"] == "text" and t.get("segments")),
             key=lambda t: len(t["segments"]))
    out = []
    for s in sorted(tt["segments"], key=lambda x: x["target_timerange"]["start"]):
        try:
            out.append(json.loads(tm[s["material_id"]]["content"])["text"].strip())
        except Exception:
            pass
    return [x for x in out if x]


def load(key: str) -> list[str]:
    p = FIX / f"breaks_{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    rows = lines_from_draft(SOURCES[key])
    FIX.mkdir(exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    return rows


def sentences(lines: list[str]) -> list[list[str]]:
    out, cur = [], []
    for ln in lines:
        cur.append(ln)
        if _END.search(ln):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def bounds(groups: list[list[str]]) -> set[int]:
    """줄 끝이 몇 번째 어절 뒤인가 (마지막 줄 끝은 뺀다)."""
    pos, out = 0, set()
    for g in groups[:-1]:
        pos += len(" ".join(g).split())
        out.add(pos)
    return out


def score(key: str, max_chars: int, show: int):
    lines = load(key)
    tp = fp = fn = exact = 0
    sents = sentences(lines)
    misses = []
    for s in sents:
        words = " ".join(s).split()
        if len(words) < 2:
            continue
        want = bounds([[x] for x in s])
        got_groups = korean.chunk(words, max_chars)
        got = set()
        pos = 0
        for g in got_groups[:-1]:
            pos += len(g)
            got.add(pos)
        tp += len(want & got)
        fp += len(got - want)
        fn += len(want - got)
        if want == got:
            exact += 1
        elif len(misses) < show:
            def render(bs):
                return " ┃ ".join(" ".join(words[a:b]) for a, b in
                                  zip([0] + sorted(bs), sorted(bs) + [len(words)]))
            misses.append((render(want), render(got)))
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    return {"문장": len(sents), "완전일치": exact, "정밀도": p, "재현율": r, "F1": f1,
            "misses": misses}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=23)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    for key in SOURCES:
        s = score(key, args.max, args.show)
        print(f"{key:<5} 문장 {s['문장']:>3} · 완전일치 {s['완전일치']:>3} "
              f"({s['완전일치']/s['문장']*100:4.1f}%) · 경계 정밀도 {s['정밀도']*100:4.1f}% "
              f"재현율 {s['재현율']*100:4.1f}% · F1 {s['F1']*100:4.1f}")
        for want, got in s["misses"]:
            print(f"    대표님  {want}")
            print(f"    규칙    {got}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
