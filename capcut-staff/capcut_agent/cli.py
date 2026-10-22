"""CLI — 영상 하나를 캡컷 드래프트로.

    python -m capcut_agent.cli "영상.mp4"
    python -m capcut_agent.cli "영상.mp4" --no-asr           # 무음컷만 (1단 동작)
    python -m capcut_agent.cli "영상.mp4" --dry-run          # 드래프트 없이 분석만
    python -m capcut_agent.cli "영상.mp4" --model tiny       # 빠른 확인용
    python -m capcut_agent.cli "영상.mp4" --print-script     # 대본 전문 출력
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .pipeline import Options, run

US = 1_000_000


def fmt(us: int) -> str:
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


async def _main(args) -> int:
    opts = Options(
        noise_db=args.noise,
        min_silence_s=args.min_silence,
        pad_s=args.pad,
        min_keep_s=args.min_keep,
        asr_enabled=not args.no_asr,
        model=args.model or "",
        use_cache=not args.no_cache,
        filler_enabled=not args.no_filler,
        ng_enabled=not args.no_ng,
        subtitle_enabled=not args.no_subtitle,
        draft_name=args.name or "",
        use_profile=args.profile,
        preset=args.preset,
        reframe=args.reframe,
        emphasis_plan=args.emphasis,
        auto_emphasis=not args.no_emphasis,
        beat_hides_subtitle=not args.keep_sub_under_card,
        forced_align=not args.no_forced_align,
        script_path=args.script,
        sfx=not args.no_sfx,
        auto_zoom=not args.no_zoom,
        beauty=not args.no_beauty,
        filter_4k=not args.no_4k,
        beats=not args.no_beats,
        beat_plan=args.beat_plan,
        no_sub_script=args.no_sub_script,
        cards=args.cards,
        stock=args.stock,
        image_plan=args.images,
        slides=args.slides,
        slide_deck=args.deck,
    )

    result_holder: dict = {}

    async def emit(ev: dict) -> None:
        t = ev.get("type")
        if t == "media":
            print(f"입력    {ev['name']}")
            print(f"        {ev['width']}x{ev['height']}  {ev['fps']}fps  "
                  f"{fmt(ev['duration_us'])}  "
                  f"audio={'있음' if ev['has_audio'] else '없음'}")
            print(f"용도    {ev.get('preset_label', '')}  ·  배속 {ev.get('speed', 1.0)}×")
        elif t == "step":
            if ev.get("state") == "running":
                print(f"  · {ev['label']} …", flush=True)
            elif ev.get("state") == "done":
                print(f"    {ev.get('detail','')}  ({ev.get('elapsed',0)}s)")
            elif ev.get("state") == "skipped":
                print(f"  · {ev['label']} — 건너뜀 ({ev.get('detail','')})")
        elif t == "result":
            result_holder.update(ev)
        elif t == "warn":
            print(f"  ! {ev['message']}")
        elif t == "beats":
            print()
            print(f"── 비트 {ev['count']}개 ──")
            for at, du, label, text in ev["rows"]:
                print(f"   {fmt(at)}  {du/US:.1f}s  "
                      f"{(label + ' / ') if label else '':<11}{text}")
            print()
        elif t == "slides":
            print(f"\n── 장표: {ev['deck']} ──")
            print(f"   빈 자리 {ev['zone']}")
            for label, at_s, src_us, ratio, heard, why in ev["rows"]:
                if src_us < 0:
                    print(f"   ❌ {label:<10} {why}")
                    continue
                print(f"   {fmt(src_us)}  {label:<10} {ratio:>3.0%}  {heard[:24]}")
            print()
        elif t == "error":
            print(f"[에러] {ev['message']}", file=sys.stderr)

    try:
        res = await run(Path(args.video).expanduser(), opts, emit,
                        make_draft=not args.dry_run)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"[에러] {e}", file=sys.stderr)
        return 1

    s = res.stats
    print()
    print(f"원본 {fmt(s['original_us'])} → 결과 {fmt(s['total_us'])} "
          f"({s['removed_pct']}% 제거)")
    print(f"  무음 컷   {fmt(s['silence_removed_us'])}")
    print(f"  잔말/NG   {fmt(s['speech_removed_us'])}  "
          f"(잔말 {s['filler']} · NG {s['ng']})")
    print(f"  세그먼트  {s['segments']}개 / 자막 {s['subtitles']}개")

    if args.print_script and res.transcript_dict:
        print("\n── 대본 ──")
        for seg in res.transcript_dict["segs"]:
            flag = "  " if seg["kind"] == "speech" else "✕ "
            why = f"   [{seg['reason']}]" if seg["reason"] else ""
            print(f"{flag}{fmt(seg['start'])}  {seg['text']}{why}")

    if res.draft:
        print(f"\n드래프트  {res.draft.name}")
        print(f"          {res.draft.folder}")
        print("\n캡컷을 재시작하면 목록에 뜹니다. 열어서 직접 재생해보세요.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="capcut_agent",
                                 description="무음·잔말·NG 컷 + 자막 캡컷 드래프트 생성")
    ap.add_argument("video", help="입력 mp4/mov")
    ap.add_argument("--name", help="드래프트 이름 (기본: 파일명_시각)")
    ap.add_argument("--noise", type=float, default=-32.0, help="무음 임계 dB (기본 -32)")
    ap.add_argument("--min-silence", type=float, default=0.35, help="무음 최소 길이 초")
    ap.add_argument("--pad", type=float, default=0.08, help="발화 앞뒤 여유 초")
    ap.add_argument("--min-keep", type=float, default=0.30, help="살릴 조각 최소 길이 초")
    ap.add_argument("--model", help="whisper 모델 (기본 large-v3-turbo)")
    ap.add_argument("--no-asr", action="store_true", help="대본 추출 안 함")
    ap.add_argument("--no-filler", action="store_true", help="잔말 컷 안 함")
    ap.add_argument("--no-ng", action="store_true", help="NG 컷 안 함")
    ap.add_argument("--no-subtitle", action="store_true", help="자막 안 넣음")
    ap.add_argument("--no-cache", action="store_true", help="대본 캐시 무시")
    ap.add_argument("--preset", default="youtube", choices=["youtube", "vod"],
                    help="용도. youtube=1.2배속(풀링·키·파트0) / vod=1.0배속(유료 본편)")
    ap.add_argument("--profile", action="store_true",
                    help="사용자 실측 편집 스타일 적용 (컷 성향 · 자막 2단 · 배속은 --preset)")
    ap.add_argument("--emphasis", default="",
                    help="맥락으로 정한 강조 계획 JSON 경로")
    ap.add_argument("--no-sfx", action="store_true", help="효과음 안 넣음")
    ap.add_argument("--no-zoom", action="store_true", help="강조 줄 자동 확대 안 함")
    ap.add_argument("--no-beauty", action="store_true", help="얼굴 보정 안 넣음")
    ap.add_argument("--no-4k", action="store_true", help="4K 필터 안 넣음")
    ap.add_argument("--no-beats", action="store_true", help="비트 카드 안 넣음")
    ap.add_argument("--beat-plan", default="",
                    help="비트 계획 JSON (없으면 대본에서 자동 선정)")
    ap.add_argument("--no-emphasis", action="store_true",
                    help="강조 자막(빨강·파랑)을 넣지 않음 (기본: 풀링은 넣음, 강의는 늘 안 넣음)")
    ap.add_argument("--script", default="",
                    help="촬영대본 txt(한 줄 = 자막 하나). 비우면 받아쓰기와 닮은 대본을 알아서 찾음")
    ap.add_argument("--no-forced-align", action="store_true",
                    help="강제 정렬 안 씀 (예전 방식: 소리 크기로 낱말 재배치)")
    ap.add_argument("--keep-sub-under-card", action="store_true",
                    help="카드가 떠 있어도 그 줄 자막을 내리지 않음")
    ap.add_argument("--no-sub-script", action="store_true",
                    help="손으로 끊은 자막 대본을 무시하고 자동으로 끊음")
    ap.add_argument("--cards", action="store_true",
                    help="대본에서 그래픽 카드를 만들어 얹음")
    ap.add_argument("--stock", action="store_true",
                    help="Pexels 사진을 주제에 맞춰 얹음 (.env 에 API 키 필요)")
    ap.add_argument("--images", default="",
                    help="장면별 검색어 계획 JSON 경로")
    ap.add_argument("--slides", action="store_true",
                    help="VOD 강의 — 큐 시트대로 장표를 얹음")
    ap.add_argument("--deck", default="",
                    help="장표 덱 지정 (기본: 대본 첫마디로 자동 판별)")
    ap.add_argument("--reframe", action="store_true",
                    help="4K 원본에서 프레이밍 변주 (풀샷/미디엄/클로즈업)")
    ap.add_argument("--dry-run", action="store_true", help="드래프트는 안 만듦")
    ap.add_argument("--print-script", action="store_true", help="대본 전문 출력")
    args = ap.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
