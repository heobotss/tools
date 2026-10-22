"""강조 색 자동 선정을 **대표님이 손으로 칠한 영상**에 대고 채점한다.

정답지: 대표님 완성본의 자막 줄 + 색 (tools/fixtures/emph_*.json 으로 떠 둔다).
문장 단위로 style.score → 선정 → cuts._cap_emphasis(문장당 끝 2줄 · 줄 26%)
까지 실제 코드 그대로 돌려서, 어느 줄이 칠해졌는지를 대표님 것과 비교한다.

    python tools/test_emphasis.py            채점
    python tools/test_emphasis.py --show 20  놓친 줄·잘못 칠한 줄 보기
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from capcut_agent import cuts  # noqa: E402
from capcut_agent.cuts import SubtitlePiece  # noqa: E402
from capcut_agent.style import pick_emphasis  # noqa: E402

FIX = HERE / "fixtures"
DRAFTS = Path.home() / "AppData/Local/CapCut/User Data/Projects/com.lveditor.draft"
SOURCES = {"풀링3": "풀링3", "0818": "0818_풀링_비트"}
_END = re.compile(r"([.?!]|(요|다|죠|까|냐|네|니다))\s*$")


def from_draft(name):
    d = json.loads((DRAFTS / name / "draft_content.json").read_text(encoding="utf-8"))
    tm = {m["id"]: m for m in d["materials"]["texts"]}
    tt = max((t for t in d["tracks"] if t["type"] == "text" and t.get("segments")),
             key=lambda t: len(t["segments"]))
    rows = []
    for s in sorted(tt["segments"], key=lambda x: x["target_timerange"]["start"]):
        try:
            c = json.loads(tm[s["material_id"]]["content"])
            col = tuple(round(x, 2) for x in c["styles"][0]["fill"]["content"]["solid"]["color"])
            kind = "normal" if col == (1.0, 1.0, 1.0) else ("neg" if col[0] > 0.8 else "pos")
            rows.append([c["text"].strip(), kind])
        except Exception:
            pass
    return [r for r in rows if r[0]]


def load(key):
    p = FIX / f"emph_{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    rows = from_draft(SOURCES[key])
    FIX.mkdir(exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def run(rows):
    """실제 파이프라인과 같은 순서로 색을 정한다."""
    sents, cur = [], []
    for i, (t, _) in enumerate(rows):
        cur.append(i)
        if _END.search(t):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    texts = [" ".join(rows[i][0] for i in s) for s in sents]
    kinds = pick_emphasis(texts, ratio=cuts.EMPH_SENT_RATIO)
    pieces = []
    for si, (s, k) in enumerate(zip(sents, kinds)):
        for i in s:
            pieces.append(SubtitlePiece(start=i, dur=1, text=rows[i][0], words=[],
                                        kind=k, seg_id=si))
    pieces = cuts._cap_emphasis(pieces, cuts.EMPH_LINE_RATIO)
    return [p.kind for p in pieces]


def score(key, show=0):
    rows = load(key)
    got = run(rows)
    want = [k for _, k in rows]
    tp = sum(1 for g, w in zip(got, want) if g != "normal" and w != "normal")
    fp = sum(1 for g, w in zip(got, want) if g != "normal" and w == "normal")
    fn = sum(1 for g, w in zip(got, want) if g == "normal" and w != "normal")
    same_kind = sum(1 for g, w in zip(got, want) if g != "normal" and g == w)
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    out = {"줄": len(rows), "대표님색": sum(1 for w in want if w != "normal"),
           "자동색": sum(1 for g in got if g != "normal"), "정밀도": p, "재현율": r,
           "F1": f1, "빨파일치": same_kind / max(1, tp)}
    if show:
        print(f"  ── {key}: 놓친 줄 ──")
        for (t, w), g in zip(rows, got):
            if w != "normal" and g == "normal":
                print(f"     [{w}] {t}")
                show -= 1
                if show <= 0:
                    break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=0)
    a = ap.parse_args()
    for key in SOURCES:
        s = score(key, a.show)
        print(f"{key:<5} 줄 {s['줄']} · 대표님 색 {s['대표님색']} · 자동 색 {s['자동색']} · "
              f"정밀도 {s['정밀도']*100:4.1f}% 재현율 {s['재현율']*100:4.1f}% F1 {s['F1']*100:4.1f} · "
              f"맞힌 줄의 빨/파 일치 {s['빨파일치']*100:4.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
