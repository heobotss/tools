"""ffmpeg silencedetect 로 무음을 찾고, 남길 발화 구간(keep)을 계산한다.

모든 시간은 마이크로초(µs) 정수. float 초를 굴리면 세그먼트를 이어붙일 때
반올림 오차가 누적돼서 캡컷 타임라인에 미세한 틈이 생긴다.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

US = 1_000_000

_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


@dataclass
class Span:
    """[start, end) 구간. 단위 µs."""
    start: int
    end: int

    @property
    def dur(self) -> int:
        return self.end - self.start


@dataclass
class SilenceParams:
    noise_db: float = -32.0      # 이보다 조용하면 무음 취급
    min_silence_s: float = 0.35  # 이보다 짧은 무음은 무시 (숨소리/자연스러운 텀)
    pad_s: float = 0.08          # 발화 앞뒤 여유 — 말 첫음절/끝음절이 잘리는 걸 막는다
    min_keep_s: float = 0.30     # 이보다 짧은 조각은 버린다 (툭툭 끊기는 느낌 방지)
    frame_based: bool = True     # ffmpeg 대신 프레임 세기로 판정 (위 설명 참고)
    bridge_s: float = 0.25       # 이보다 짧은 튐은 정적을 깨지 않는다 (아래 설명)
    voice_margin_db: float = 8.0  # 임계보다 이만큼 위면 진짜 목소리 — 정적을 끊는다
    # 촬영본마다 녹음 크기가 다르다. 고정 임계를 쓰면 작게 녹음한 날 말이 잘린다 —
    # 2026-09-23 촬영본은 말소리(상위 5%)가 -25dB 라 고정 -32dB 와 7dB 밖에 차이가
    # 안 났고(예전 촬영본은 23dB), 말끝이 무음으로 판정돼 111초가 잘려나갔다.
    # 그래서 임계를 **그 영상 말소리 기준 상대값**으로 잡는다.
    auto_noise: bool = True       # 영상 소리 크기로 임계를 자동 산출
    rel_db: float = 24.0          # 말소리(상위 5%)보다 이만큼 아래를 무음으로 본다
    floor_margin_db: float = 6.0  # 잡음 바닥(하위 5%)보다는 이만큼 위에 둔다


def auto_noise_db(db, p: "SilenceParams") -> float:
    """이 영상에 맞는 무음 임계(dB). 말소리 기준 상대값으로 잡는다.

    예전 촬영본(말소리 -8~-10dB)에서는 -31~-34dB 가 나와 고정 -32dB 와 거의 같고,
    대표님이 남기신 구간을 98~99% 그대로 살린다(풀링4·풀링3·키2 실측 2026-09-23).
    작게 녹음한 0923 촬영본(말소리 -25dB)에서는 -49dB 로 내려간다.
    """
    speech = float(np.percentile(db, 95))
    floor = float(np.percentile(db, 5))
    thr = speech - p.rel_db
    return max(min(thr, -20.0), floor + p.floor_margin_db)


def detect_silence_frames(video: str | Path, p: SilenceParams | None = None,
                          smooth: int = 5) -> list[Span]:
    """프레임 단위로 무음을 찾는다 (ffmpeg silencedetect 대신).

    ffmpeg 의 silencedetect 는 구간 안 **모든 샘플**이 기준 아래여야 무음으로
    친다. 그래서 말 사이 정적에 숨소리나 입소리가 한 번만 섞여도 구간이 깨진다.
    실제로 사용자가 잘라낸 정적 118.9초 중 ffmpeg 은 45초만 찾아냈다.

    여기서는 10ms 프레임의 세기를 보고, 짧은 스파이크는 중앙값으로 눌러
    무시한다. 사람이 "여기 조용하네" 라고 느끼는 것과 훨씬 가깝다.
    """
    from .align import features

    p = p or SilenceParams()
    tr = features(video)
    db = tr.db.copy()

    # 한두 프레임짜리 튐(숨소리·입소리)은 무음 판정을 깨면 안 된다
    if smooth > 1 and len(db) > smooth:
        pad = smooth // 2
        padded = np.pad(db, (pad, pad), mode="edge")
        win = np.lib.stride_tricks.sliding_window_view(padded, smooth)
        db = np.median(win, axis=1)

    noise_db = auto_noise_db(db, p) if p.auto_noise else p.noise_db
    quiet = db < noise_db

    # 방 소음이 임계에 걸쳐 있으면 "전 프레임이 **연속으로** 임계 아래" 라는
    # 요구가 계속 깨진다. 실측(2026-08-18 촬영본): 대표님이 지운 정적 구간의
    # 프레임 중 임계 아래는 36~58% 뿐이었고, 그래서 39곳 중 30곳이 무음으로
    # 잡히지도 않았다 — 3.26초짜리 정적이 통째로 살아남았다.
    #
    # 그래서 튐을 두 가지로 나눈다. 임계보다 voice_margin_db 만큼 위로 올라간
    # 곳은 진짜 목소리이니 정적을 끊고, 그보다 약한 튐(숨·옷깃·에어컨)이
    # bridge_s 보다 짧으면 메운다. 브릿지 0.25초에서 회수량이 포화한다
    # (143.4초 → 210.1초, 잘못 자름은 18.7초 → 21.5초).
    bridge = int(p.bridge_s * 100)
    if bridge > 0:
        loud = db > (noise_db + p.voice_margin_db)
        i, n = 0, len(quiet)
        while i < n:
            if quiet[i]:
                i += 1
                continue
            j = i
            while j < n and not quiet[j]:
                j += 1
            if i > 0 and j < n and j - i <= bridge and not loud[i:j].any():
                quiet[i:j] = True
            i = j

    min_len = max(1, int(p.min_silence_s * 100))     # features 는 100fps

    spans: list[Span] = []
    i, n = 0, len(quiet)
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if j - i >= min_len:
            spans.append(Span(i * 10_000, j * 10_000))   # 프레임 → µs
        i = j
    return spans


def detect_silence(video: str | Path, p: SilenceParams | None = None) -> list[Span]:
    """무음 구간 목록을 반환."""
    p = p or SilenceParams()
    video = Path(video).resolve()

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(video),
            "-af", f"silencedetect=noise={p.noise_db}dB:d={p.min_silence_s}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # silencedetect 결과는 stderr 로 나온다. 종료코드는 정상이어도 여기에 찍힌다.
    log = proc.stderr or ""
    if proc.returncode != 0 and "silence_start" not in log:
        raise RuntimeError(f"ffmpeg silencedetect 실패: {log.strip()[-400:]}")

    spans: list[Span] = []
    pending: int | None = None
    for line in log.splitlines():
        m = _START_RE.search(line)
        if m:
            pending = max(0, int(round(float(m.group(1)) * US)))
            continue
        m = _END_RE.search(line)
        if m and pending is not None:
            end = int(round(float(m.group(1)) * US))
            if end > pending:
                spans.append(Span(pending, end))
            pending = None
    if pending is not None:
        # 영상 끝까지 무음으로 끝난 경우 — 길이는 호출부에서 clamp 한다.
        spans.append(Span(pending, 1 << 62))
    return spans


def keeps_from_silence(
    silences: list[Span], total_us: int, p: SilenceParams | None = None
) -> list[Span]:
    """무음의 여집합 = 발화 구간. 패딩·병합·최소길이 필터까지 적용."""
    p = p or SilenceParams()
    pad = int(round(p.pad_s * US))
    min_keep = int(round(p.min_keep_s * US))

    # 여집합 계산
    raw: list[Span] = []
    cursor = 0
    for s in sorted(silences, key=lambda x: x.start):
        start, end = max(0, s.start), min(total_us, s.end)
        if start > cursor:
            raw.append(Span(cursor, start))
        cursor = max(cursor, end)
    if cursor < total_us:
        raw.append(Span(cursor, total_us))

    # 패딩 — 말이 잘리지 않게 앞뒤로 늘린다
    padded = [
        Span(max(0, s.start - pad), min(total_us, s.end + pad))
        for s in raw
    ]

    # 패딩 때문에 겹친 구간 병합
    merged: list[Span] = []
    for s in padded:
        if merged and s.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, s.end)
        else:
            merged.append(Span(s.start, s.end))

    return [s for s in merged if s.dur >= min_keep]


def analyze(video: str | Path, p: SilenceParams | None = None,
            total_us: int | None = None) -> tuple[list[Span], list[Span]]:
    """(무음, 남길구간) 을 한 번에. total_us 를 주면 ffprobe 를 건너뛴다."""
    from .probe import probe

    p = p or SilenceParams()
    if total_us is None:
        total_us = probe(video).duration_us
    sil = (detect_silence_frames(video, p) if p.frame_based
           else detect_silence(video, p))
    return sil, keeps_from_silence(sil, total_us, p)
