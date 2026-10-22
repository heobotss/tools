"""장표가 말과 맞는지 수치로 확인한다.

    python tools/verify_slides.py "영상.mp4"
    python tools/verify_slides.py --all

각 장표마다 이렇게 나온다.

    큐시트   실제      차이   라벨      정렬  그 시각에 실제로 하는 말
    2:40   5:09.06  +149.1  순환      100%  유치가곧유지고유지가곧유치다처음들으면…

**「그 시각에 실제로 하는 말」이 근거 문장과 같으면 맞은 것이다.** 숫자만 보면
안 된다 — 정렬이 엉뚱한 데 붙어도 숫자는 그럴듯하게 나온다.

큐 시트의 `시점`은 원고 낭독 추정치라 실제와 크게 다르다 (실측 +12초~+334초).
차이 열은 "손으로 맞췄으면 이만큼 어긋났을 것"을 보여주는 것이지 오차가 아니다.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capcut_agent import asr as asr_mod
from capcut_agent import slides as sl
from capcut_agent.probe import probe

US = 1_000_000


def fmt(us: float) -> str:
    s = us / US
    return f"{int(s) // 60}:{s % 60:05.2f}"


async def check(video: Path, deck_key: str = "") -> int:
    media = probe(video)
    tr, cached = await asr_mod.transcribe(video, language="ko",
                                          duration_us=media.duration_us)

    deck = sl.get_deck(deck_key) if deck_key else sl.deck_for(tr)
    if deck is None:
        print(f"[{video.name}] 어느 강의인지 못 알아봤습니다. --deck 로 지정하세요.")
        return 1

    cues, sents = deck.load()
    resolved = sl.resolve(cues, sents, tr)

    print(f"\n{'=' * 86}")
    print(f"{video.name}   →   {deck.label}"
          f"   ({media.duration_us / US / 60:.1f}분"
          f"{' · 대본 캐시' if cached else ''})")
    print(f"장표 {deck.slide_dir.name} · 큐 {len(cues)}줄 · 원고 {len(sents)}문장")
    print("-" * 86)
    print(f"{'큐시트':>6} {'실제':>9} {'차이':>8}  {'라벨':<10} {'정렬':>4}  "
          f"그 시각에 실제로 하는 말")
    print("-" * 86)

    bad = 0
    prev = -1.0
    diffs = []
    for r in resolved:
        c = r.cue
        cue_t = f"{int(c.at_s) // 60}:{int(c.at_s) % 60:02d}"
        if c.blank:
            print(f"{cue_t:>6} {'—':>9} {'':>8}  {c.label or '—':<10} {'':>4}  "
                  f"(여백 — 얼굴만)")
            continue
        if r.src_us < 0:
            bad += 1
            print(f"{cue_t:>6} {'?':>9} {'?':>8}  {c.label:<10} {'':>4}  "
                  f"❌ {r.why} — 근거「{c.anchor}」")
            continue
        t = r.src_us / US
        diffs.append(t - c.at_s)
        order = "" if t > prev else "   ← 순서 뒤집힘!"
        if t <= prev:
            bad += 1
        prev = t
        mark = "★" if c.focus else " "
        print(f"{cue_t:>6} {fmt(r.src_us):>9} {t - c.at_s:>+8.1f}  "
              f"{c.label:<10} {r.ratio:>3.0%}  {mark}{r.heard}{order}")

    print("-" * 86)
    if diffs:
        diffs.sort()
        mid = diffs[len(diffs) // 2]
        print(f"큐 시트 시점 대비 실제: 중앙 {mid:+.1f}초 · "
              f"{min(diffs):+.1f} ~ {max(diffs):+.1f}초"
              f"   (손으로 맞췄으면 이만큼 어긋났을 자리)")
    ok = sum(1 for r in resolved if not r.cue.blank and r.src_us >= 0)
    live = sum(1 for r in resolved if not r.cue.blank)
    print(f"근거 해결 {ok}/{live}" + ("  ✅" if bad == 0 else f"  ⚠ 확인 필요 {bad}건"))
    return bad


async def main() -> int:
    ap = argparse.ArgumentParser(description="장표 타이밍 검증")
    ap.add_argument("video", nargs="?", help="촬영본")
    ap.add_argument("--deck", default="", help="덱 키 (기본: 대본으로 자동 판별)")
    ap.add_argument("--all", action="store_true",
                    help="0주차 촬영본 폴더 전체")
    args = ap.parse_args()

    if args.all:
        root = Path(r"G:\내 드라이브\BPT\편집할 영상\vod 0주차 원본")
        vids = sorted(p for p in root.glob("*.mp4"))
    elif args.video:
        vids = [Path(args.video)]
    else:
        ap.error("영상 경로나 --all 이 필요합니다")

    bad = 0
    for v in vids:
        bad += await check(v, args.deck)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
