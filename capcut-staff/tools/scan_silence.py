"""무음 탐지 후보를 dB 배열 위에서 빠르게 훑는다.

정답은 대표님이 캡컷에서 손질한 draft 의 source_timerange (= B).
B 밖은 "잘라야 했던 곳", B 안은 "남겨야 했던 곳"이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

US = 1_000_000
FPS = 100


def runs(mask: np.ndarray, min_len: int):
    """True 가 min_len 이상 이어지는 구간."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        if j - i >= min_len:
            out.append((i, j))
        i = j
    return out


def detect(db: np.ndarray, thr: float, min_len: int, bridge: int,
           voice_thr: float) -> np.ndarray:
    """조용한 프레임 마스크. 짧은 튐은 정적을 깨지 않는다.

    방 소음이 임계에 걸쳐 있으면 '전 프레임이 연속으로 임계 아래' 요구가
    계속 깨진다. 그래서 (1) 진짜 목소리 세기(voice_thr) 위로 올라간 곳만
    정적을 끊고, (2) 그보다 약한 튐이 bridge 프레임보다 짧으면 메운다.
    """
    quiet = db < thr
    if bridge > 0:
        loud = db > voice_thr
        for a, b in runs(~quiet, 1):
            if b - a <= bridge and not loud[a:b].any():
                quiet[a:b] = True
    return quiet


def spans_to_mask(spans, n):
    m = np.zeros(n, dtype=bool)
    for a, b in spans:
        m[int(a / 10_000):int(b / 10_000)] = True
    return m


def main() -> int:
    db = np.load(sys.argv[1])
    diff = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    n = len(db)
    keep_gt = spans_to_mask(diff["B"], n)        # 대표님이 남긴 곳
    cut_gt = ~keep_gt                             # 대표님이 지운 곳

    print(f"정답: 남김 {keep_gt.sum()/FPS:.1f}s · 지움 {cut_gt.sum()/FPS:.1f}s\n")
    print(f"{'임계':>6} {'최소':>5} {'브릿지':>6} │ {'맞게 자름':>9} {'잘못 자름':>9}  {'회수율':>6}")
    print("─" * 60)
    best = []
    for thr in (-38, -36, -34, -32, -30, -28):
        for min_s in (0.20, 0.28):
            for bridge in (0, 8, 15, 25):
                q = detect(db, thr, int(min_s * FPS), bridge, thr + 8)
                sil = np.zeros(n, dtype=bool)
                for a, b in runs(q, int(min_s * FPS)):
                    sil[a:b] = True
                good = (sil & cut_gt).sum() / FPS
                bad = (sil & keep_gt).sum() / FPS
                rec = good / (cut_gt.sum() / FPS) * 100
                best.append((good - 3 * bad, thr, min_s, bridge, good, bad, rec))
    best.sort(reverse=True)
    seen = set()
    for sc, thr, min_s, bridge, good, bad, rec in best:
        k = (thr, min_s, bridge)
        if k in seen:
            continue
        seen.add(k)
        print(f"{thr:>6} {min_s:>5.2f} {bridge:>6} │ {good:>8.1f}s {bad:>8.1f}s  {rec:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
