"""두 영상이 같은 촬영본인지 정렬 엔진과 **무관하게** 확인한다.

    python tools/check_same_source.py 원본.mp4 편집본.mp4

편집본 앞부분을 따로 받아적어서, 그 말이 원본 대본에 실제로 있는지 본다.
정렬이 이상할 때 "엔진 버그인가 / 애초에 다른 촬영본인가" 를 가르는 용도다.
정렬 결과를 전혀 쓰지 않으므로 서로를 검증할 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent import asr as asr_mod  # noqa: E402
from capcut_agent.transcript import normalize_nospace  # noqa: E402


def clip(src: Path, start: float, dur: float, out: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(start), "-i", str(src), "-t", str(dur),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out),
    ], check=True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("edited")
    ap.add_argument("--at", type=float, default=20.0, help="편집본에서 볼 시작 지점(초)")
    ap.add_argument("--dur", type=float, default=45.0, help="볼 길이(초)")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="samesrc_"))
    piece = tmp / "piece.wav"
    print(f"편집본 {a.at:.0f}s 부터 {a.dur:.0f}s 받아적는 중…")
    clip(Path(a.edited), a.at, a.dur, piece)
    tr_edit, _ = await asr_mod.transcribe(piece)
    edit_text = " ".join(s.text for s in tr_edit.segs).strip()
    print(f"\n편집본에서 들린 말:\n  {edit_text[:400]}\n")

    print("원본 대본 불러오는 중… (캐시가 있으면 즉시)")
    tr_orig, cached = await asr_mod.transcribe(Path(a.original))
    print(f"  원본 {len(tr_orig.segs)}조각" + (" (캐시)" if cached else ""))

    hay = normalize_nospace(" ".join(s.text for s in tr_orig.segs))
    needle = normalize_nospace(edit_text)
    if not needle:
        print("[에러] 편집본에서 말을 못 알아들었습니다.", file=sys.stderr)
        return 1

    m = SequenceMatcher(None, needle, hay).find_longest_match(0, len(needle), 0, len(hay))
    cover = m.size / len(needle)

    print(f"\n원본 대본과 겹치는 정도: {cover * 100:.1f}%")
    print(f"가장 긴 일치: {m.size}자")
    if m.size > 10:
        print(f"  일치 부분: {hay[m.b:m.b + min(m.size, 120)]}")

    print()
    if cover >= 0.5:
        print("→ 같은 촬영본입니다. 정렬이 이상하면 엔진 문제입니다.")
        return 0
    if cover >= 0.15:
        print("→ 일부만 겹칩니다. 편집본에 다른 촬영분이나 재녹음이 섞였을 수 있습니다.")
        return 0
    print("→ 거의 안 겹칩니다. 두 파일은 다른 촬영본으로 보입니다.")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
