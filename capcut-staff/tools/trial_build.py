"""규칙 맞추기용 빠른 시험 빌드 — 카드 영상 굽기만 건너뛴다.

    python tools/trial_build.py <원본.mp4> <드래프트 이름>

카드 한 장을 180초 4K 영상으로 굽는 데 1분씩 걸려서, 규칙을 고칠 때마다 전체를
돌리면 한 편에 15분이 넘는다. 비교 채점에는 카드의 **자리**만 필요하므로
굽기를 가짜로 바꾼다. 이렇게 만든 드래프트는 캡컷에서 카드가 빈 칸으로 보이니
대표님께 보여 드리지 않는다 (이름에 '시험_' 을 붙여 두고 채점 뒤 지운다).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent import beats, slides  # noqa: E402
from capcut_agent.pipeline import Options, run  # noqa: E402


def main() -> int:
    video, name = Path(sys.argv[1]), sys.argv[2]
    def _fake(out):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        return Path(out)
    slides.to_clip = lambda image, dur_us, w, h, fps, out: _fake(out)
    beats.render = lambda b, w, h, out: _fake(out)

    async def emit(ev):
        t = ev.get("type")
        if t == "warn":
            print("  !", ev.get("message"), flush=True)
        elif t == "step" and ev.get("state") == "done":
            print("  ·", ev.get("detail", ""), flush=True)

    opts = Options(use_profile=True, preset="youtube", draft_name=name)
    res = asyncio.run(run(video, opts, emit, make_draft=True))
    print("드래프트", getattr(getattr(res, "draft", None), "name", res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
