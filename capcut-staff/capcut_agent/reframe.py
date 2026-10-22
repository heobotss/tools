"""같은 촬영본에서 프레이밍을 바꿔 '두 번째 카메라'를 만든다.

4K(3840×2160) 로 찍고 1080p 로 내보내면 **절반까지 잘라내도 화질이 그대로**다.
풀샷·미디엄·클로즈업을 같은 클립에서 뽑아낼 수 있다는 뜻이다.
앉아서 말하는 고정 화면이 계속 이어지는 문제를 소재 없이 푸는 방법.

인물 위치는 얼굴 인식 없이 찾는다 — 카메라가 고정이면 **움직이는 곳이 사람**이다.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .style import FramingPlan

_W, _H = 320, 180


@dataclass
class Subject:
    cx: float          # 인물 중심 (0~1)
    cy: float
    max_zoom: float    # 출력 해상도를 지키며 확대 가능한 한계


def find_subject(video: str | Path, out_w: int = 1920, out_h: int = 1080,
                 frames: int = 16) -> Subject:
    """고정 카메라 영상에서 인물 위치를 찾는다."""
    video = Path(video)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip() or 0)
    vw, vh = _dims(video)

    tmp = Path(tempfile.mkdtemp(prefix="subj_"))
    stack = []
    for i in range(frames):
        t = dur * (i + 0.5) / max(frames, 1)
        p = tmp / f"{i:03d}.pgm"
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={_W}:{_H},format=gray",
             "-f", "rawvideo", str(p)], capture_output=True)
        if r.returncode == 0 and p.exists() and p.stat().st_size >= _W * _H:
            stack.append(np.frombuffer(p.read_bytes()[:_W * _H],
                                       dtype=np.uint8).reshape(_H, _W))
    max_zoom = max(1.0, min(vw / out_w, vh / out_h))
    if len(stack) < 3:
        return Subject(0.5, 0.5, max_zoom)      # 못 찾으면 화면 중앙

    motion = np.stack(stack).astype(np.float32).std(axis=0)
    thr = float(np.percentile(motion, 88))
    ys, xs = np.where(motion >= thr)
    if len(xs) < 20:
        return Subject(0.5, 0.5, max_zoom)

    x0, x1 = np.percentile(xs, [5, 95])
    y0, y1 = np.percentile(ys, [5, 95])
    return Subject(float((x0 + x1) / 2 / _W), float((y0 + y1) / 2 / _H), max_zoom)


def _dims(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip().split(",")
    return int(out[0]), int(out[1])


def plan_zooms(
    n_clips: int,
    subject: Subject,
    framings: list[FramingPlan],
    ratio: float = 0.38,
    min_run: int = 2,
) -> tuple[list[float], list[float]]:
    """클립마다 확대배율과 세로 오프셋을 배정한다.

    같은 프레이밍이 너무 자주 바뀌면 정신없고, 안 바뀌면 고정 화면 그대로다.
    기본은 풀샷으로 두고 일정 비율만 당긴다 (실측: 101개 중 38개).
    min_run: 한 번 당기면 최소 몇 클립은 유지할지 — 한 컷씩 튀는 걸 막는다.
    """
    zooms = [1.0] * n_clips
    offs = [0.0] * n_clips
    if n_clips == 0 or len(framings) < 2:
        return zooms, offs

    # 오프닝은 무조건 풀샷으로 연다. 첫 화면부터 얼굴이 꽉 찬 클로즈업이면
    # 상황 파악이 안 돼서 보는 사람이 당황한다. 확대는 몇 컷 지난 뒤부터.
    opening = min(3, n_clips)

    tighter = [f for f in framings if f.zoom > 1.0] or framings[-1:]
    want = int(round(n_clips * ratio))
    if want <= 0:
        return zooms, offs

    # 확대 구간을 고르게 흩뿌린다. 몰리면 그 부분만 답답해진다.
    step = max(min_run * 2, n_clips // max(1, want // min_run + 1))
    i, k = opening, 0
    placed = 0
    while i < n_clips and placed < want:
        f = tighter[k % len(tighter)]
        run = min(min_run, n_clips - i, want - placed)
        for j in range(run):
            zooms[i + j] = f.zoom
            # 당길수록 얼굴 쪽(위)으로 올린다. 캡컷 transform.y 는 + 가 위쪽.
            offs[i + j] = f.offset_y
        placed += run
        i += run + step
        k += 1
    return zooms, offs
