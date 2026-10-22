"""테스트 영상의 정답(truth.json)과 파이프라인 결과를 대조해 채점한다.

    python tools/score.py samples/test_korean.mp4

정답 대사의 시간 구간이 결과 타임라인에서 얼마나 살아남았는지로 판정한다.
  · keep   인데 많이 잘렸다   → 과잉 컷 (제일 나쁨. 말이 사라진다)
  · filler/ng 인데 많이 남았다 → 놓침
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent.pipeline import Options, run  # noqa: E402

US = 1_000_000
KEEP_OK = 0.80   # keep 대사는 80% 이상 남아야 통과
DROP_OK = 0.50   # filler/ng 대사는 50% 미만만 남아야 통과


def overlap(a0: float, a1: float, keeps: list[list[int]]) -> float:
    """대사 구간이 결과에 남은 비율."""
    s, e = a0 * US, a1 * US
    if e <= s:
        return 0.0
    kept = sum(max(0, min(e, k1) - max(s, k0)) for k0, k1 in keeps)
    return kept / (e - s)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default="")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    video = Path(a.video)
    truth_path = video.with_suffix("").with_suffix(".truth.json")
    if not truth_path.exists():
        truth_path = video.parent / (video.stem + ".truth.json")
    if not truth_path.exists():
        print(f"[에러] 정답 파일이 없습니다: {truth_path}", file=sys.stderr)
        return 1
    truth = json.loads(truth_path.read_text(encoding="utf-8"))

    captured: dict = {}

    async def emit(ev: dict) -> None:
        if ev["type"] == "result":
            captured.update(ev)

    opts = Options(model=a.model or "", use_cache=not a.no_cache)
    await run(video, opts, emit, make_draft=False)

    keeps = captured.get("keeps", [])
    tr = captured.get("transcript") or {"segs": []}

    print(f"\n{'정답':<6} {'남은비율':>8}  {'판정':<6} 대사")
    print("─" * 78)
    passed = failed = 0
    over_cut = missed = 0

    for ln in truth["lines"]:
        ratio = overlap(ln["start"], ln["end"], keeps)
        label = ln["label"]
        if label == "keep":
            ok = ratio >= KEEP_OK
            if not ok:
                over_cut += 1
        else:
            ok = ratio < DROP_OK
            if not ok:
                missed += 1
        passed += ok
        failed += not ok
        mark = "통과" if ok else ("과잉컷" if label == "keep" else "놓침")
        print(f"{label:<6} {ratio * 100:>7.1f}%  {mark:<6} {ln['text'][:44]}")

    print("─" * 78)
    total = len(truth["lines"])
    print(f"통과 {passed}/{total}   과잉컷 {over_cut}   놓침 {missed}")

    s = captured["stats"]
    print(f"\n원본 {s['original_us'] / US:.2f}s → 결과 {s['total_us'] / US:.2f}s "
          f"({s['removed_pct']}% 제거)")
    print(f"세그먼트 {s['segments']} · 자막 {s['subtitles']} · "
          f"잔말 {s['filler']} · NG {s['ng']} · 가장자리 {s.get('trimmed', 0)}")

    # ── 자막 검사 ──
    subs = captured.get("subtitles") or []
    total_us = s["total_us"]
    sub_fail = []

    if subs:
        maxc = 18  # Options.subtitle_max_chars 기본값
        over = [t for _, _, t in subs if len(t) > maxc]
        if over:
            sub_fail.append(f"한 줄 초과 {len(over)}개 (최장 {max(len(t) for t in over)}자)")
        if any("\n" in t for _, _, t in subs):
            sub_fail.append("자막에 줄바꿈이 들어감")

        gaps, cursor = 0, 0
        for st, du, _ in subs:
            if st > cursor:
                gaps += 1
            cursor = st + du
        if gaps:
            sub_fail.append(f"자막 빈틈 {gaps}곳")
        if cursor < total_us:
            sub_fail.append(f"끝부분 {(total_us - cursor) / US:.2f}s 자막 없음")

        # 자막 전문이 대본과 같은가 — 단어가 소리 없이 사라지면 여기서 걸린다
        flat_sub = "".join("".join(t.split()) for _, _, t in subs)
        flat_script = "".join("".join(
            (seg.get("eff_text") or seg["text"]).split())
            for seg in tr["segs"] if seg["kind"] == "speech")
        if flat_sub != flat_script:
            sub_fail.append("자막 전문이 대본과 다름 (단어 유실/중복)")

        print(f"\n자막 {len(subs)}개 · 최장 {max(len(t) for _, _, t in subs)}자 · "
              f"커버리지 {cursor / total_us * 100:.1f}%")
        if sub_fail:
            for m in sub_fail:
                print(f"  [실패] {m}")
        else:
            print("  한 줄 유지 · 빈틈 없음 · 대본과 일치")

    print("\n── 최종 자막 ──")
    for st, du, t in subs:
        print(f"  {st / US:6.2f} → {(st + du) / US:6.2f}  {len(t):>2}자  {t}")

    return 0 if (failed == 0 and not sub_fail) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
