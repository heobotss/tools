"""자막이 클립 경계에 정확히 붙었는지 검사한다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

US = 1_000_000


def main() -> int:
    d = json.loads((Path(sys.argv[1]) / "draft_content.json").read_text(encoding="utf-8"))
    vt = max((t for t in d["tracks"] if t["type"] == "video" and t.get("segments")),
             key=lambda t: len(t["segments"]))
    clips = sorted([(s["target_timerange"]["start"],
                     s["target_timerange"]["start"] + s["target_timerange"]["duration"])
                    for s in vt["segments"]])
    tt = max((t for t in d["tracks"] if t["type"] == "text" and t.get("segments")),
             key=lambda t: len(t["segments"]))
    subs = sorted([(s["target_timerange"]["start"],
                    s["target_timerange"]["start"] + s["target_timerange"]["duration"])
                   for s in tt["segments"]])

    cross = sum(1 for a, b in subs for ca, cb in clips if a < cb < b)
    gaps = sum(1 for i in range(len(subs) - 1) if subs[i][1] != subs[i + 1][0])
    starts = np.array([a for a, _ in subs])
    ends = np.array([b for _, b in subs])
    ds = np.array([min(abs(starts - ca)) for ca, _ in clips]) / 1000
    de = np.array([min(abs(ends - cb)) for _, cb in clips]) / 1000

    print(f"클립 {len(clips)} · 자막 {len(subs)}")
    print(f"  클립 경계를 가로지르는 자막   {cross}개")
    print(f"  자막 사이 빈틈                {gaps}곳")
    print(f"  클립 시작↔자막 시작  최대 {ds.max():.3f}ms  (0ms 초과 {int((ds>0).sum())}개)")
    print(f"  클립 끝  ↔자막 끝    최대 {de.max():.3f}ms  (0ms 초과 {int((de>0).sum())}개)")
    print(f"  타임라인 총길이 {d['duration']/US:.3f}s · 클립 끝 {clips[-1][1]/US:.3f}s"
          f" · 자막 끝 {subs[-1][1]/US:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
