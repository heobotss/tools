"""오디오 정렬 엔진 검증.

    python tools/test_align.py

원본에서 **내가 정한 구간만** 뽑아 이어붙인 가짜 편집본을 만들고,
정렬 엔진이 그 구간을 되찾아내는지 본다. 정답을 알고 있으니 채점이 된다.

실제 편집본과 비슷한 조건을 만들려고 해상도·코덱·샘플레이트·볼륨을 전부 바꾼다.
(사용자 편집본이 4K HEVC 48kHz → 1080p H.264 44.1kHz 였다)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent.align import align_files  # noqa: E402

US = 1_000_000
SRC = Path(__file__).parent.parent / "samples" / "test_korean.mp4"

# 편집본에 남길 구간 (초). 이게 정답이다.
KEEP = [(0.8, 5.9), (7.4, 10.4), (14.5, 18.2), (28.6, 33.4), (33.9, 36.0)]

# 컷 지점이 무음 한복판이면 경계는 **원리적으로 특정할 수 없다.**
# 원본이 10.3~11.5s 무음이고 편집본이 10.4 에서 점프했다면, 그 구간 어디를
# 경계로 잡아도 소리는 완전히 같다. 오차가 아니라 구분 불가능한 등가 구간이다.
# 실제 편집자도 무음에서 자르므로 이 모호성은 늘 있다. 0.25s 면 충분하다.
TOL = 0.25


def build_fake_edit(src: Path, keep, out: Path) -> None:
    """구간만 뽑아 이어붙이고, 편집본처럼 다른 규격으로 재인코딩한다."""
    parts = []
    filt = []
    for i, (a, b) in enumerate(keep):
        filt.append(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS,scale=480:854[v{i}]")
        filt.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,volume=1.6[a{i}]")
        parts.append(f"[v{i}][a{i}]")
    filt.append("".join(parts) + f"concat=n={len(keep)}:v=1:a=1[v][a]")

    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-b:a", "128k", str(out),
    ], check=True)


def main() -> int:
    if not SRC.exists():
        print(f"[에러] 테스트 원본이 없습니다: {SRC}", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="align_"))
    fake = tmp / "fake_edit.mp4"
    print("가짜 편집본 만드는 중… (해상도·코덱·샘플레이트·볼륨 전부 변경)")
    build_fake_edit(SRC, KEEP, fake)

    print("정렬 중…")
    res = align_files(SRC, fake)

    print(f"\n원본 {res.orig_us / US:.2f}s  편집본 {res.edit_us / US:.2f}s  "
          f"찾은 구간 {len(res.matches)}개  못 찾음 {res.unmatched_us / US:.2f}s\n")

    print(f"{'정답 구간':<20} {'찾은 구간':<20} {'오차(초)':>14}  판정")
    print("-" * 72)
    ok_n = 0
    for i, (a, b) in enumerate(KEEP):
        if i < len(res.matches):
            m = res.matches[i]
            ga, gb = m.orig_start / US, m.orig_end / US
            ea, eb = abs(ga - a), abs(gb - b)
            good = ea <= TOL and eb <= TOL
            ok_n += good
            print(f"{a:6.2f} → {b:6.2f}      {ga:6.2f} → {gb:6.2f}      "
                  f"{ea:5.2f} / {eb:5.2f}   {'통과' if good else '실패'}")
        else:
            print(f"{a:6.2f} → {b:6.2f}      {'(못 찾음)':<20} {'':>14}  실패")

    extra = len(res.matches) - len(KEEP)
    worst = max((max(abs(res.matches[i].orig_start / US - a),
                     abs(res.matches[i].orig_end / US - b))
                 for i, (a, b) in enumerate(KEEP) if i < len(res.matches)),
                default=99.0)
    print("-" * 72)
    print(f"통과 {ok_n}/{len(KEEP)}   구간 수 {len(res.matches)}/{len(KEEP)}   "
          f"최대 오차 {worst:.2f}s   못 찾음 {res.unmatched_us / US:.2f}s"
          + (f"   여분 {extra}개" if extra > 0 else ""))

    if extra > 0:
        print("\n여분으로 잡힌 구간:")
        for m in res.matches[len(KEEP):]:
            print(f"   {m.orig_start / US:6.2f} → {m.orig_end / US:6.2f}  "
                  f"(점수 {m.score:.2f})")

    print("\n원본에서 잘려나간 구간:")
    for a, b in res.gaps():
        print(f"   {a / US:6.2f} → {b / US:6.2f}   ({(b - a) / US:.2f}s)")

    return 0 if (ok_n == len(KEEP) and extra <= 0) else 2


if __name__ == "__main__":
    raise SystemExit(main())
