"""대본의 낱말이 편집 결과에서 사라지지 않았는지 확인한다.

    python tools/check_words.py 영상.mp4 [--emphasis 계획.json]

말이 통째로 빠지면 앞뒤 맥락이 어긋난다. 실제로 접속부사 '그래서' 가
잔말로 분류돼 사라지면서 문장이 "시간 내서…" 로 시작한 적이 있다.
소리로만 판단하면 약하게 발음된 낱말(-36dB)과 룸톤(-36dB)을 못 가른다.

여기서는 **일부러 버린 것(잔말·NG) 말고 사라진 낱말이 있는지** 를 본다.
하나라도 있으면 실패다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np  # noqa: E402

from capcut_agent.pipeline import Options, run  # noqa: E402

US = 1_000_000


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--emphasis", default="")
    a = ap.parse_args()

    cap: dict = {}

    async def emit(ev: dict) -> None:
        if ev["type"] == "result":
            cap.update(ev)

    opts = Options(use_profile=True, emphasis_plan=a.emphasis)
    await run(Path(a.video), opts, emit, make_draft=False)

    keeps = cap["keeps"]
    tr = cap.get("transcript") or {"segs": []}

    # 소리가 있는 프레임만 본다.
    #
    # whisper 의 낱말 구간에는 뒤따르는 쉼이 딸려 있다 — '지금' 한 낱말이
    # 1.14초로 잡히는데 실제 발화는 0.28초뿐인 식이다. 그 구간 전체를
    # 분모로 삼으면, 들리는 부분을 다 살려놓고도 "25% 만 남았다" 고 나온다.
    # **들리는 부분이 남았는가** 만 따진다.
    from capcut_agent.align import features
    trk = features(Path(a.video))
    speech = trk.db > -38.0

    def coverage(st: int, en: int) -> float:
        f0, f1 = st // 10_000, max(st // 10_000 + 1, en // 10_000)
        f1 = min(f1, len(speech))
        if f0 >= f1:
            return 1.0
        voiced = np.flatnonzero(speech[f0:f1]) + f0
        if voiced.size == 0:
            return 1.0                       # 원래 소리가 없던 구간
        kept_mask = np.zeros(len(speech), bool)
        for k0, k1 in keeps:
            kept_mask[k0 // 10_000:k1 // 10_000] = True
        return float(kept_mask[voiced].mean())

    ALIVE = 0.5     # 절반 넘게 남아 있으면 들린다

    lost, kept, dropped_on_purpose = [], 0, 0
    for seg in tr["segs"]:
        if seg["kind"] != "speech":
            dropped_on_purpose += len(seg.get("words", []))
            continue
        # media_holes = 말더듬의 실패한 시도. 영상만 잘리고 자막 글자는
        # 살아남은 테이크로 그대로 나가므로 손실이 아니다.
        holes = (seg.get("holes") or []) + (seg.get("media_holes") or [])
        for st, en, tx in seg.get("words", []):
            if any(h0 <= st < h1 for h0, h1 in holes):
                dropped_on_purpose += 1
                continue
            if coverage(st, en) >= ALIVE:
                kept += 1
            else:
                lost.append((st, tx, seg["text"][:40]))

    total = kept + len(lost)
    print(f"낱말 {total}개 · 살아남음 {kept} · 의도적 제거 {dropped_on_purpose}")
    print(f"영상 시작 {keeps[0][0] / US:.2f}s · 끝 {keeps[-1][1] / US:.2f}s")

    first_word = min((st for seg in tr["segs"] if seg["kind"] == "speech"
                      for st, _, _ in seg.get("words", [])
                      if not any(h0 <= st < h1
                                 for h0, h1 in (seg.get("holes") or []))),
                     default=0)
    lead = (first_word - keeps[0][0]) / US
    print(f"첫 낱말 전 여백 {lead:.2f}s"
          + ("  ← 너무 김" if lead > 0.6 else ""))

    if lost:
        print(f"\n[실패] 사라진 낱말 {len(lost)}개:")
        for st, tx, ctx in lost[:20]:
            print(f"   {st / US:7.2f}s  {tx!r}   ← \"{ctx}…\"")
        return 1
    if lead > 0.6:
        print("\n[실패] 말이 시작되기 전 여백이 깁니다.")
        return 1
    print("\n사라진 낱말 없음. 앞 여백도 정상.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
