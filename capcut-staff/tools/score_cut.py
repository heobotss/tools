"""파이프라인을 통째로 돌려 대표님 손질본과 대조한다."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from diff_edit import US, f, load_user, load_words, merge, subtract, words_in  # noqa

from capcut_agent.pipeline import Options, run


async def main() -> int:
    video, draft, asr = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    tag = sys.argv[4] if len(sys.argv) > 4 else "결과"

    async def emit(ev):
        if ev.get("type") == "step" and ev.get("state") == "done":
            print("   ·", ev.get("detail", ""), flush=True)

    res = await run(video, Options(use_profile=True, preset="youtube"),
                    emit, make_draft=False)
    A = merge([[k.start, k.end] for k in res.plan.keeps])
    B, _ = load_user(draft)
    words = load_words(asr)

    over = [s for s in subtract(A, B) if s[1] - s[0] >= 120_000]
    under = [s for s in subtract(B, A) if s[1] - s[0] >= 120_000]
    ov = sum(b - a for a, b in over) / US
    un = sum(b - a for a, b in under) / US

    print(f"\n═══ {tag} ═══")
    print(f"  결과 길이   {f(res.stats['total_us'])}   (대표님 06:11.66)")
    print(f"  ① 덜 잘랐다  {len(over):>3}곳 {ov:>6.1f}초")
    print(f"  ② 잘못 잘랐다 {len(under):>3}곳 {un:>6.1f}초")
    print(f"\n  ② 상세:")
    for a, b in sorted(under, key=lambda s: s[0] - s[1])[:10]:
        print(f"    {f(a)} {(b-a)/US:5.2f}s  {' '.join(words_in(words, a, b))[:56]}")
    Path(f"score_{tag}.json").write_text(json.dumps(
        {"over": over, "under": under, "A": A, "B": B,
         "over_s": ov, "under_s": un}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
