"""ffprobe 로 미디어 정보를 읽는다. 시간 단위는 전부 마이크로초(µs)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

US = 1_000_000


@dataclass
class MediaInfo:
    path: Path
    duration_us: int
    width: int
    height: int
    fps: float
    has_audio: bool
    size_bytes: int

    @property
    def duration_s(self) -> float:
        return self.duration_us / US


def _parse_fps(rate: str) -> float:
    """'30000/1001' 같은 유리수 문자열을 float 로."""
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 30.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 30.0


def probe(path: str | Path) -> MediaInfo:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"영상 파일이 없습니다: {path}")

    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe 실패: {out.stderr.strip()[:400]}")

    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError(f"비디오 트랙이 없습니다: {path.name}")

    # 길이는 format 쪽이 가장 신뢰도가 높다. 없으면 비디오 스트림으로 fallback.
    dur = data.get("format", {}).get("duration") or video.get("duration")
    if dur is None:
        raise RuntimeError(f"길이를 읽을 수 없습니다: {path.name}")

    # 세로 영상은 rotate 메타로 표기되는 경우가 있어 회전을 반영한다.
    w, h = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    tag_rot = (video.get("tags") or {}).get("rotate")
    if tag_rot:
        rotation = int(tag_rot)
    if abs(rotation) % 180 == 90:
        w, h = h, w

    return MediaInfo(
        path=path,
        duration_us=int(round(float(dur) * US)),
        width=w,
        height=h,
        fps=_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30"),
        has_audio=audio is not None,
        size_bytes=path.stat().st_size,
    )
