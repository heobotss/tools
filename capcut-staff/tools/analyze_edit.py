"""편집본을 원본과 대조해 **어떻게 편집했는지** 알아낸다.

    python tools/analyze_edit.py 원본.mp4 편집본.mp4
    python tools/analyze_edit.py 원본.mp4 편집본.mp4 --asr    # 대본까지 겹쳐 본다

컷 목록만으로도 알 수 있는 것:
  · 얼마나 잘라내는가 (압축률, 컷 횟수, 평균 클립 길이)
  · 얼마나 바짝 붙이는가 (남긴 무음 길이 → pad / min_silence 역산)
  · 어떤 길이의 것을 잘라내는가 (짧은 숨 vs 통문장)

--asr 을 주면 대본을 겹쳐서 **무엇을** 잘라냈는지까지 본다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np  # noqa: E402

from capcut_agent import asr as asr_mod  # noqa: E402
from capcut_agent.align import align, features  # noqa: E402
from capcut_agent.script import build_sentences  # noqa: E402
from capcut_agent.silence import SilenceParams, analyze  # noqa: E402

US = 1_000_000


def fmt(us: float) -> str:
    s = us / US
    m, s = divmod(s, 60)
    return f"{int(m):02d}:{s:05.2f}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("edited")
    ap.add_argument("--asr", action="store_true", help="대본까지 겹쳐서 분석")
    ap.add_argument("--out", default="", help="결과 JSON 저장 경로")
    a = ap.parse_args()

    orig_p, edit_p = Path(a.original), Path(a.edited)
    for p in (orig_p, edit_p):
        if not p.exists():
            print(f"[에러] 파일이 없습니다: {p}", file=sys.stderr)
            return 1

    print("오디오 특징 추출 중… (원본)")
    fo = features(orig_p)
    print("오디오 특징 추출 중… (편집본)")
    fe = features(edit_p)
    print(f"정렬 중… ({len(fo)} × {len(fe)} 프레임)")
    res = align(fo, fe)

    kept = sum(m.dur for m in res.matches)
    gaps = res.gaps()

    print("\n" + "=" * 66)
    print(f"원본     {fmt(res.orig_us)}")
    print(f"편집본   {fmt(res.edit_us)}   "
          f"({(res.orig_us - res.edit_us) / res.orig_us * 100:.1f}% 제거)")
    print(f"복원     {fmt(kept)} / 클립 {len(res.matches)}개 / 컷 {len(gaps)}곳")
    if res.unmatched_us > US:
        print(f"미매칭   {fmt(res.unmatched_us)}  ← 원본에 없는 소리 "
              f"(배경음악·재녹음·삽입 클립일 수 있음)")
    print("=" * 66)

    if not res.matches:
        print("정렬에 실패했습니다. 두 파일이 같은 촬영본이 맞는지 확인하세요.")
        return 1

    clip = np.array([m.dur / US for m in res.matches])
    cut = np.array([(b - a) / US for a, b in gaps]) if gaps else np.array([0.0])

    print("\n── 클립 길이 (남긴 조각) ──")
    print(f"   평균 {clip.mean():5.2f}s   중앙 {np.median(clip):5.2f}s   "
          f"최소 {clip.min():5.2f}s   최대 {clip.max():5.2f}s")
    print("── 컷 길이 (잘라낸 조각) ──")
    print(f"   평균 {cut.mean():5.2f}s   중앙 {np.median(cut):5.2f}s   "
          f"최소 {cut.min():5.2f}s   최대 {cut.max():5.2f}s")
    print(f"   1초 미만 {int((cut < 1).sum())}곳 · 1~3초 "
          f"{int(((cut >= 1) & (cut < 3)).sum())}곳 · "
          f"3초 이상 {int((cut >= 3).sum())}곳")

    # ── 편집자의 무음 기준 역산 ──
    # 잘라낸 구간이 실제로 무음이었는지, 남긴 무음은 얼마나 긴지 본다.
    print("\n── 무음 처리 성향 ──")
    sil, sil_keeps = analyze(orig_p, SilenceParams(), total_us=res.orig_us)
    sil_ranges = [(s.start, s.end) for s in sil]

    def overlap(a0, a1, ranges):
        return sum(max(0, min(a1, b1) - max(a0, b0)) for b0, b1 in ranges)

    cut_sil = sum(overlap(a, b, sil_ranges) for a, b in gaps)
    cut_total = sum(b - a for a, b in gaps)
    kept_sil = sum(overlap(m.orig_start, m.orig_end, sil_ranges) for m in res.matches)
    print(f"   잘라낸 것 중 무음  {cut_sil / max(cut_total,1) * 100:5.1f}%  "
          f"({fmt(cut_sil)} / {fmt(cut_total)})")
    print(f"   남긴 것 중 무음    {kept_sil / max(kept,1) * 100:5.1f}%  "
          f"({fmt(kept_sil)} / {fmt(kept)})")
    r = kept_sil / max(kept, 1)
    tone = "거의 안 남김" if r < 0.08 else ("어느 정도 남김" if r < 0.18 else "넉넉히 남김")
    print(f"   → 말 사이 여백을 {tone}")

    # ── 대본 겹치기 ──
    script_rows = []
    if a.asr:
        print("\n대본 추출 중… (몇 분 걸립니다)")
        tr, cached = await asr_mod.transcribe(orig_p, duration_us=res.orig_us)
        tr = build_sentences(tr)
        print(f"   {len(tr.segs)}문장" + (" (캐시)" if cached else ""))

        print("\n── 잘라낸 말 ──")
        shown = 0
        for seg in tr.segs:
            ov = overlap(seg.start, seg.end, gaps)
            ratio = ov / max(seg.dur, 1)
            if ratio >= 0.6 and seg.text.strip():
                script_rows.append({"start": seg.start, "end": seg.end,
                                    "text": seg.text, "removed": round(ratio, 2)})
                if shown < 40:
                    print(f"   {fmt(seg.start)}  {seg.text[:58]}")
                    shown += 1
        if len(script_rows) > 40:
            print(f"   … 외 {len(script_rows) - 40}문장")
        print(f"\n   통째로 잘린 문장 {len(script_rows)}개 / 전체 {len(tr.segs)}개")

    if a.out:
        Path(a.out).write_text(json.dumps({
            "original": str(orig_p), "edited": str(edit_p),
            "orig_us": res.orig_us, "edit_us": res.edit_us,
            "keeps": [[m.orig_start, m.orig_end] for m in res.matches],
            "gaps": [[x, y] for x, y in gaps],
            "clip_stats": {"mean": clip.mean(), "median": float(np.median(clip)),
                           "min": clip.min(), "max": clip.max()},
            "cut_stats": {"mean": cut.mean(), "median": float(np.median(cut)),
                          "min": cut.min(), "max": cut.max()},
            "kept_silence_ratio": kept_sil / max(kept, 1),
            "removed_sentences": script_rows,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {a.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
