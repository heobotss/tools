"""완성본 안에 남은 '정적'을 **결과 타임라인 시각**으로 뽑는다.

소스 시각으로 말해봐야 대표님이 확인할 수가 없다. 캡컷에서 스크럽할 수 있는
자리로 바꿔서 보여줘야 "여기 아직 비네" 를 서로 같은 걸 보고 말할 수 있다.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent.align import features
from capcut_agent.pipeline import Options, run

US = 1_000_000
MIN_HOLE_S = 0.5          # 이보다 길게 조용하면 사람이 "비었다" 고 느낀다


def f(us: float) -> str:
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


async def measure(video: Path, bridge: float, db: np.ndarray, speed: float):
    import capcut_agent.pipeline as P
    base = P.Options.silence_params

    def patched(self):
        p = base(self)
        p.bridge_s = bridge
        return p

    P.Options.silence_params = patched
    try:
        res = await run(video, Options(use_profile=True, preset="youtube"),
                        lambda ev: asyncio.sleep(0), make_draft=False)
    finally:
        P.Options.silence_params = base

    # "연속으로 임계 아래" 로 재면 원래 탐지기와 똑같은 함정에 빠진다 — 방 소음이
    # 임계에 걸쳐 있으면 0곳이 나온다. 사람이 "비었다" 고 느끼는 건 **말이 없는
    # 시간**이므로, 낱말 사이 빈틈을 재고 그 안에 진짜 소리가 있는지만 확인한다.
    words = [w for sg in res.transcript_dict["segs"] for w in sg["words"]]
    words.sort()
    holes, out_t = [], 0
    for k in res.plan.keeps:
        pts = [k.start]
        for ws, we, _ in words:
            if we > k.start and ws < k.end:
                pts += [max(k.start, ws), min(k.end, we)]
        pts.append(k.end)
        for i in range(0, len(pts) - 1, 2):
            a, b = pts[i], pts[i + 1]
            if b - a < MIN_HOLE_S * US:
                continue
            fr = db[int(a / 10_000):int(b / 10_000)]
            if len(fr) and (fr > -25.0).mean() > 0.15:
                continue                      # 실제로는 소리가 있다 — 정적 아님
            holes.append(((out_t + (a - k.start)) / speed, (b - a) / speed))
        out_t += k.end - k.start
    return res, holes


async def main() -> int:
    video = Path(sys.argv[1])
    db = features(str(video)).db
    for bridge, tag in ((0.0, "개선 전"), (0.25, "개선 후")):
        res, holes = await measure(video, bridge, db, 1.2)
        tot = sum(d for _, d in holes) / US
        print(f"\n═══ {tag} — 결과 {f(res.stats['total_us'])} ═══")
        print(f"  0.5초 넘는 정적 {len(holes)}곳 · 합계 {tot:.1f}초 "
              f"(완성본의 {tot / (res.stats['total_us'] / US) * 100:.1f}%)")
        big = sorted(holes, key=lambda h: -h[1])[:10]
        print("  긴 것부터 (완성본 시각):")
        for at, d in big:
            print(f"    {f(at)}   {d/US:.2f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
