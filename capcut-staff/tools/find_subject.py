"""고정 카메라 영상에서 인물이 있는 자리를 찾는다.

    python tools/find_subject.py 영상.mp4

카메라가 고정이면 **움직이는 곳이 곧 사람**이다. 프레임을 여러 장 뽑아
픽셀별 변화량을 재면 배경은 잠잠하고 인물만 들뜬다. 얼굴 인식 모델이 필요 없다.

4K 원본을 1080p 로 낼 때는 절반까지 잘라내도 화질이 그대로다. 이 자리를 알면
같은 촬영본에서 풀샷·미디엄·클로즈업을 만들어낼 수 있다 — 두 번째 카메라인 셈.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 320, 180          # 분석용 축소 크기


def grab(video: Path, n: int, dur: float, tmp: Path) -> np.ndarray:
    """영상 전체에 고르게 퍼진 프레임 n 장을 작게 뽑는다."""
    tmp.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        out = tmp / f"f{i:03d}.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={W}:{H}", str(out)],
            check=True)
        frames.append(np.asarray(Image.open(out).convert("L"), dtype=np.float32))
    return np.stack(frames)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--out", default="subject.json")
    a = ap.parse_args()

    video = Path(a.video)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip())
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip().split(",")
    vw, vh = int(probe[0]), int(probe[1])

    tmp = Path(__file__).parent.parent / ".work" / "subject"
    print(f"프레임 {a.frames}장 추출 중… ({dur:.0f}s 영상)")
    fr = grab(video, a.frames, dur, tmp)

    # 시간축 표준편차 = 움직임. 배경은 0 에 가깝고 사람은 크다.
    motion = fr.std(axis=0)
    motion -= motion.min()
    if motion.max() > 0:
        motion /= motion.max()

    # 상위 움직임 영역의 경계 상자
    thr = float(np.percentile(motion, 88))
    ys, xs = np.where(motion >= thr)
    if len(xs) < 20:
        print("[에러] 움직임을 못 찾았습니다. 카메라가 움직이는 영상일 수 있습니다.",
              file=sys.stderr)
        return 1

    # 가장자리 잡음을 걷어내려고 5~95 백분위만 쓴다
    x0, x1 = np.percentile(xs, [5, 95])
    y0, y1 = np.percentile(ys, [5, 95])
    cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H

    print(f"\n원본 {vw}×{vh}")
    print(f"인물 중심  x={cx:.3f}  y={cy:.3f}   (0=왼쪽/위, 1=오른쪽/아래)")
    print(f"인물 범위  x {x0/W:.2f}~{x1/W:.2f}   y {y0/H:.2f}~{y1/H:.2f}")

    # 세로로 얼마나 잘라도 1080p 가 유지되는지
    max_zoom = min(vw / 1920, vh / 1080)
    print(f"\n1080p 화질을 지키며 확대 가능한 최대 배율: {max_zoom:.2f}배")

    # 프레이밍 제안 — 확대할수록 얼굴 쪽으로 붙는다
    plans = []
    for name, zoom, bias in (("풀샷", 1.00, 0.00), ("미디엄", 1.35, 0.18),
                             ("클로즈업", 1.80, 0.34)):
        z = min(zoom, max_zoom)
        # 위쪽(얼굴)으로 당긴다. 확대할수록 더.
        ty = cy - bias
        cw, ch = 1.0 / z, 1.0 / z
        ox = min(max(cx - cw / 2, 0.0), 1.0 - cw)
        oy = min(max(ty - ch / 2, 0.0), 1.0 - ch)
        plans.append({"name": name, "zoom": round(z, 3),
                      "crop_x": round(ox, 4), "crop_y": round(oy, 4),
                      "crop_w": round(cw, 4), "crop_h": round(ch, 4)})
        print(f"  {name:<8} {z:.2f}배  크롭 x={ox:.3f} y={oy:.3f} "
              f"w={cw:.3f} h={ch:.3f}")

    # 사람이 눈으로 확인할 수 있게 미리보기 저장
    mid = dur / 2
    prev_dir = Path(__file__).parent.parent / "samples" / "reframe_preview"
    prev_dir.mkdir(parents=True, exist_ok=True)
    for p in plans:
        out = prev_dir / f"{p['name']}.jpg"
        vf = (f"crop=iw*{p['crop_w']}:ih*{p['crop_h']}:"
              f"iw*{p['crop_x']}:ih*{p['crop_y']},scale=960:-2")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{mid:.2f}",
                        "-i", str(video), "-frames:v", "1", "-vf", vf, str(out)],
                       check=True)
    print(f"\n미리보기 저장: {prev_dir}")

    Path(a.out).write_text(json.dumps({
        "video": str(video), "width": vw, "height": vh,
        "center": {"x": cx, "y": cy}, "max_zoom": max_zoom, "plans": plans,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
