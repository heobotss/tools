"""소리로 재테이크(말더듬)를 찾는다.

대본으로는 못 잡는다. whisper 는 같은 말을 두 번 하면 **한 번만 받아적는다.**
실제 사례: "아무리 좋은… (1.3초 멈춤) …아무리 좋은 물을 부어도" 를
"아무리 좋은 물을 부어도" 로 정리해버렸다. 대본에 흔적이 없으니
문장 비교로는 영영 못 찾는다.

소리에는 남아 있다. 신호는 **긴 쉼을 사이에 둔 닮은 소리** 다.
말을 이어가는 사람은 그렇게 쉬지 않고, 쉬었다면 다시 시작한 것이다.

    184.98~185.56  A "아무리"      ← 실패한 시도
    185.73~186.25  B "좋은"
    [1.3초 공백]
    187.61~188.15  C "아무리"      ← 다시 시작
    A vs C = 0.848  (다른 낱말끼리는 0.70 언저리)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .align import Track, features

US = 1_000_000
FPS = 100


@dataclass
class Retake:
    """실패한 시도 구간. 단위 µs."""
    start: int
    end: int
    score: float          # 다시 말한 것과 얼마나 닮았는지
    pause_s: float

    @property
    def dur(self) -> int:
        return self.end - self.start


def _bursts(tr: Track, thr_db: float = -30.0, min_len: int = 12) -> list[tuple[int, int]]:
    """말이 이어지는 덩어리 (프레임 단위)."""
    loud = tr.db > thr_db
    out, i, n = [], 0, len(loud)
    while i < n:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < n and loud[j]:
            j += 1
        if j - i >= min_len:
            out.append((i, j))
        i = j
    return out


def _shape_sim(F: np.ndarray, a: tuple[int, int], b: tuple[int, int]) -> float:
    """두 구간의 소리 모양 유사도. 길이가 달라도 비교되도록 늘려 맞춘다."""
    A, B = F[a[0]:a[1]], F[b[0]:b[1]]
    n = min(len(A), len(B))
    if n < 5:
        return 0.0
    A = A[np.linspace(0, len(A) - 1, n).astype(int)]
    B = B[np.linspace(0, len(B) - 1, n).astype(int)]
    den = np.linalg.norm(A) * np.linalg.norm(B) + 1e-9
    return float((A * B).sum() / den)


def find_retakes(
    video: str | Path,
    min_pause_s: float = 0.7,
    similarity: float = 0.80,
    max_span_s: float = 4.0,
    track: Track | None = None,
) -> list[Retake]:
    """실패한 시도 구간을 찾는다.

    min_pause_s: 이보다 긴 쉼 뒤에 다시 시작한 것으로 본다.
    similarity:  쉼 앞뒤 소리가 이만큼 닮았으면 같은 말을 다시 한 것.
                 (다른 낱말끼리는 0.70 언저리라 0.80 이면 충분히 갈린다)
    max_span_s:  이보다 긴 구간은 재테이크로 보지 않는다 — 통째로 날리면 위험하다.
    """
    tr = track or features(video)
    F = tr.feat
    bursts = _bursts(tr)
    if len(bursts) < 3:
        return []

    min_pause = int(min_pause_s * FPS)
    max_span = int(max_span_s * FPS)
    out: list[Retake] = []

    for i in range(len(bursts) - 1):
        gap = bursts[i + 1][0] - bursts[i][1]
        if gap < min_pause:
            continue
        after = bursts[i + 1]

        # 쉼 앞쪽 덩어리들을 하나씩 거슬러 올라가며 비교한다.
        # 보통은 바로 앞 한두 덩어리가 실패한 시도다.
        best = None
        for k in range(i, max(-1, i - 3), -1):
            s = _shape_sim(F, bursts[k], after)
            if s >= similarity:
                best = (k, s)
        if best is None:
            continue

        k, s = best
        # 말더듬은 낱말 하나가 아니라 **구절**을 되돌아간다.
        # ("아무리 좋은…" 을 다시 시작하면 '좋은' 만이 아니라 '아무리' 부터다)
        # 쉼 앞의 이어진 발화를 통째로 잡는다.
        while k > 0 and bursts[k][0] - bursts[k - 1][1] < min_pause:
            k -= 1
        start, end = bursts[k][0], bursts[i][1]
        if end - start > max_span:
            continue
        out.append(Retake(start=start * 10_000, end=end * 10_000,
                          score=s, pause_s=gap / FPS))

    # 겹치면 합친다
    out.sort(key=lambda r: r.start)
    merged: list[Retake] = []
    for r in out:
        if merged and r.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, r.end)
            merged[-1].score = max(merged[-1].score, r.score)
        else:
            merged.append(r)
    return merged
