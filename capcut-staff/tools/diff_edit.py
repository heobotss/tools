"""에이전트 결과 vs 대표님 손질본을 **소스 시간축**에서 가른다.

대표님이 캡컷에서 고친 draft_content.json 의 source_timerange 가 곧
"실제로 남겼어야 할 구간"의 정답이다. 에이전트 keeps 와 차집합을 내면
어디를 덜 잘랐고(A-B) 어디를 잘못 잘랐는지(B-A) 그대로 나온다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

US = 1_000_000


def f(us: float) -> str:
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def merge(spans, gap=0):
    spans = sorted(spans)
    out = []
    for a, b in spans:
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract(A, B):
    """A - B (둘 다 정렬·병합된 상태)."""
    out, bi = [], 0
    for a, b in A:
        cur = a
        while bi < len(B) and B[bi][1] <= cur:
            bi += 1
        j = bi
        while j < len(B) and B[j][0] < b:
            if B[j][0] > cur:
                out.append([cur, min(B[j][0], b)])
            cur = max(cur, B[j][1])
            if cur >= b:
                break
            j += 1
        if cur < b:
            out.append([cur, b])
    return [s for s in out if s[1] > s[0]]


def load_user(draft_dir: Path):
    d = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    vids = [t for t in d["tracks"] if t["type"] == "video" and t.get("segments")]
    vt = max(vids, key=lambda t: len(t["segments"]))
    spans = [[s["source_timerange"]["start"],
              s["source_timerange"]["start"] + s["source_timerange"]["duration"]]
             for s in vt["segments"]]
    return merge(spans), d["duration"]


def load_words(asr: Path):
    d = json.loads(asr.read_text(encoding="utf-8"))
    return [(w[0], w[1], w[2]) for s in d["segs"] for w in s["words"]]


def words_in(words, a, b, cover=0.5):
    out = []
    for ws, we, t in words:
        if we <= a or ws >= b:
            continue
        ov = min(we, b) - max(ws, a)
        if ov >= (we - ws) * cover:
            out.append(t)
    return out


def main() -> int:
    draft = Path(sys.argv[1])
    agent = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    words = load_words(Path(sys.argv[3]))

    A = merge([list(k) for k in agent["keeps"]])
    B, user_dur = load_user(draft)

    a_tot = sum(b - a for a, b in A)
    b_tot = sum(b - a for a, b in B)
    print(f"에이전트 남김  {f(a_tot)}  ({len(A)}구간)")
    print(f"대표님 남김    {f(b_tot)}  ({len(B)}구간)")
    print(f"차이           {(b_tot - a_tot) / US:+.1f}초\n")

    over = subtract(A, B)      # 에이전트가 남겼는데 대표님이 뺀 것 = 덜 잘랐다
    under = subtract(B, A)     # 대표님이 남겼는데 에이전트가 뺀 것 = 잘못 잘랐다

    for label, spans, tag in (("① 덜 잘랐다 (대표님이 추가로 제거)", over, "over"),
                              ("② 잘못 잘랐다 (대표님이 되살림)", under, "under")):
        spans = [s for s in spans if s[1] - s[0] >= 120_000]   # 0.12s 이상만
        tot = sum(b - a for a, b in spans)
        print(f"── {label} — {len(spans)}곳 / {tot/US:.1f}초 ──")
        rows = sorted(spans, key=lambda s: s[0] - s[1])
        for a, b in rows[:25]:
            w = words_in(words, a, b)
            kind = "무음" if not w else "발화"
            print(f"  {f(a)}~{f(b)}  {(b-a)/US:5.2f}s  [{kind}] {' '.join(w)[:60]}")
        if len(rows) > 25:
            print(f"  … 외 {len(rows)-25}곳")
        print()

    Path("edit_diff.json").write_text(json.dumps(
        {"over": over, "under": under, "A": A, "B": B}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
