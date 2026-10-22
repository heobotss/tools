"""영상 1개를 드래프트로 만드는 전체 흐름.

단계: silence → asr → filler → draft
CLI 와 웹 서버가 같은 코드를 쓴다. 진행 상황은 emit() 콜백으로 흘려보낸다.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from . import asr as asr_mod
from .cuts import KeepPlan, SubtitlePiece, build_keeps, map_subtitles
from .draft import DraftResult, SubtitleStyle, build_draft
from .filler import FillerParams, FillerStats, drop_inner_restarts, drop_retakes, mark
from .probe import MediaInfo, probe
from .cards import find_cards, find_stock, render_cards
from .config import WORK_DIR
from . import slides as slides_mod
from .reframe import find_subject, plan_zooms
from .script import (build_sentences, redistribute_words, snap_to_audio,
                     to_srt, transcript_to_srt, write_srt)
from .silence import SilenceParams, Span, analyze
from .style import (DEFAULT_PRESET, EMPHASIS_FONT_ID, EMPHASIS_FONT_NAME,
                    PRESETS, StyleProfile, capcut_system_font, emphasis_font,
                    get_preset, hex_to_rgb, load_emphasis_plan)

US = 1_000_000


def _no_overlap(clips: list) -> list:
    """오버레이는 트랙 하나뿐이라 겹치면 캡컷이 트랙을 쪼개 버린다.

    겹친 채로 넘기면 캡컷이 임의로 다른 트랙에 얹어서 화면이 뒤엉킨다
    (실측: 비트 카드 하나가 세 번째 트랙으로 밀려났다).
    """
    out = []
    for c in sorted(clips, key=lambda x: x.start):
        if out:
            prev = out[-1]
            over = (prev.start + prev.dur) - c.start
            if over > 0:
                prev.dur -= over
                if prev.dur <= 0:
                    out.pop()
        if c.dur > 0:
            out.append(c)
    return [c for c in out if c.dur > 0]

# 단계당 최소 체류 시간. 캐시가 맞아서 순식간에 끝나면 UI 스텝퍼가
# 깜빡이고 지나가버려서 뭐가 일어났는지 사용자가 못 본다.
MIN_STEP_S = 0.5

STEPS = ("silence", "asr", "script", "filler", "draft")
STEP_LABEL = {
    "silence": "무음 구간 검출",
    "asr": "대본 추출",
    "script": "문장 구성",
    "filler": "잔말·NG 판정",
    "draft": "드래프트 생성",
}

Emit = Callable[[dict], Awaitable[None]]


@dataclass
class Options:
    # 무음
    noise_db: float = -32.0
    auto_noise: bool = True          # 무음 기준을 영상 소리 크기에 맞춰 자동으로 (2026-09-23)
    min_silence_s: float = 0.35
    pad_s: float = 0.08
    min_keep_s: float = 0.30
    # 대본
    asr_enabled: bool = True
    model: str = ""
    language: str = "ko"
    use_cache: bool = True
    # 잔말/NG
    filler_enabled: bool = True
    ng_enabled: bool = True
    ng_similarity: float = 0.78
    # 자막
    subtitle_enabled: bool = True
    subtitle_size: float = 9.0
    subtitle_y: float = -0.72
    subtitle_max_chars: int = 18     # 한 줄에 넣을 최대 글자수. 넘치면 다음 자막으로.
    subtitle_fill_gaps: bool = True  # 자막 사이 빈틈을 없애 계속 떠 있게
    # 사용자 실측 스타일 적용 (배속·컷 성향·자막 2단·확대)
    use_profile: bool = False
    # 용도. 배속이 여기서 갈린다 — 유튜브 1.2× / VOD 유료 본편 1.0×
    preset: str = DEFAULT_PRESET
    speed: float = 1.0
    reframe: bool = False            # 4K 원본에서 프레이밍 변주 (옛 방식)
    # 2026-09-17 대표님 완성본 세 편에서 뽑은 마감 규칙 (finish.py). 유튜브에만.
    auto_zoom: bool = True           # 강조 덩어리·콜아웃 줄을 1.2배로 당긴다
    beauty: bool = True              # 얼굴 보정 (잡티·고르게·작은 얼굴·머리 0.5)
    filter_4k: bool = True           # 전체에 4K 필터
    emphasis_plan: str = ""          # 맥락으로 정한 강조 계획 JSON 경로
    # 강조 자막(빨강·파랑) 자동 선택. 2026-09-16 부터 풀링(유튜브)은 기본으로 켠다 —
    # 대표님이 "색깔 처리 어떻게 했는지 분석해서 적용해 달라"고 하셨다. 확대만 대표님 몫.
    # 강의(VOD)에는 어떤 경우에도 안 넣는다 (아래 lecture 판정에서 끈다).
    auto_emphasis: bool = True
    beat_hides_subtitle: bool = True  # 카드가 떠 있는 동안 그 줄 자막을 내린다
    forced_align: bool = True         # 한국어 정렬 모델로 낱말 시각을 잡는다 (받아져 있을 때)
    script_path: str = ""             # 풀링·키 촬영대본(자막 줄 형식). 비우면 알아서 찾는다
    sfx: bool = True                 # 컷 자리에 전환음 깔기 (의미 있는 소리는 대표님 몫)
    beats: bool = True               # 전체화면 한 줄 비트 카드
    beat_plan: str = ""              # 비트 계획 JSON. 없으면 대본에서 자동 선정
    cards: bool = False              # 대본에서 그래픽 카드를 만들어 얹을지
    stock: bool = False              # Pexels 사진을 주제에 맞춰 얹을지
    # VOD 강의 — 큐 시트대로 장표를 얹는다. 오버레이 트랙을 통째로 쓴다.
    slides: bool = False
    no_sub_script: bool = False   # 자막 대본을 무시하고 자동으로 끊기
    slide_deck: str = ""             # 비우면 대본 첫마디로 자동 판별
    image_plan: str = ""             # 장면별 검색어 계획 JSON 경로
    max_cards: int = 12
    # 사용자가 지정한 보존 구간(초 단위). 여기 걸리면 무슨 일이 있어도 안 자른다.
    protect: list[tuple[float, float]] = field(default_factory=list)
    draft_name: str = ""

    def silence_params(self) -> SilenceParams:
        return SilenceParams(self.noise_db, self.min_silence_s,
                             self.pad_s, self.min_keep_s,
                             auto_noise=self.auto_noise)

    def filler_params(self) -> FillerParams:
        return FillerParams(enable_filler=self.filler_enabled,
                            enable_ng=self.ng_enabled,
                            ng_similarity=self.ng_similarity)

    def subtitle_style(self) -> SubtitleStyle:
        return SubtitleStyle(size=self.subtitle_size,
                             transform_y=self.subtitle_y)

    def subtitle_styles(self, p: StyleProfile) -> dict[str, SubtitleStyle]:
        """일반 / 부정 / 긍정 3종. 실측 check_flag 조합을 그대로 쓴다."""
        common = dict(transform_y=p.subtitle_y, shadow=False, stroke=False)
        normal = SubtitleStyle(font_path=capcut_system_font(), size=p.normal_size,
                               color=p.color_normal, background=True, **common)
        def emph(hexcol: str) -> SubtitleStyle:
            s = SubtitleStyle(font_path=emphasis_font(), size=p.emphasis_size,
                              color=hex_to_rgb(hexcol), transform_y=p.subtitle_y,
                              font_resource_id=EMPHASIS_FONT_ID,
                              font_title=EMPHASIS_FONT_NAME)
            s.stroke, s.shadow, s.background = True, True, False
            return s
        return {"normal": normal, "neg": emph(p.color_neg), "pos": emph(p.color_pos)}


@dataclass
class PipelineResult:
    media: MediaInfo
    plan: KeepPlan
    draft: DraftResult | None
    transcript_dict: dict | None
    stats: dict


async def _step(emit: Emit, name: str, index: int) -> float:
    await emit({"type": "step", "step": name, "index": index,
                "label": STEP_LABEL[name], "state": "running"})
    return time.perf_counter()


async def _done(emit: Emit, name: str, t0: float, detail: str,
                extra: dict | None = None) -> None:
    elapsed = time.perf_counter() - t0
    if elapsed < MIN_STEP_S:
        await asyncio.sleep(MIN_STEP_S - elapsed)
    payload = {"type": "step", "step": name, "state": "done",
               "detail": detail, "elapsed": round(time.perf_counter() - t0, 2)}
    if extra:
        payload.update(extra)
    await emit(payload)


_SCRIPT_SENT_END = __import__("re").compile(r"([.?!]|(요|다|죠|까|냐|네|니다))\s*$")


def _emphasize_script_pieces(subs) -> None:
    """촬영대본 줄로 만든 자막에도 강조 색을 입힌다.

    대본 줄은 문장이 아니라 자막 한 줄이라, 줄을 문장으로 묶어 판정한 뒤
    map_subtitles 와 같은 규칙(문장당 끝 두 줄 · 전체 26%)으로 줄인다.
    """
    from .cuts import EMPH_LINE_RATIO, _cap_emphasis
    from .style import pick_emphasis_learned

    groups, cur = [], []
    for i, p in enumerate(subs):
        cur.append(i)
        if _SCRIPT_SENT_END.search(p.text.strip()):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    kinds = pick_emphasis_learned([" ".join(subs[i].text for i in g) for g in groups])
    for gi, (g, k) in enumerate(zip(groups, kinds)):
        for i in g:
            subs[i].kind = k
            subs[i].seg_id = 20_000 + gi
    _cap_emphasis(subs, EMPH_LINE_RATIO)


def _apply_protect(keeps: list[Span], protect: list[tuple[float, float]],
                   total_us: int, source_keeps: list[Span]) -> list[Span]:
    """사용자가 지킨 구간을 되살린다.

    무음컷 결과(source_keeps)가 아니라 원본 전체에서 되살린다. 사용자가
    "여긴 남겨" 라고 찍었으면 무음이든 잔말이든 그대로 남는 게 맞다.
    """
    if not protect:
        return keeps
    spans = [Span(s.start, s.end) for s in keeps]
    for a, b in protect:
        s = max(0, int(a * US))
        e = min(total_us, int(b * US))
        if e > s:
            spans.append(Span(s, e))
    spans.sort(key=lambda x: x.start)
    merged: list[Span] = []
    for s in spans:
        if merged and s.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, s.end)
        else:
            merged.append(Span(s.start, s.end))
    return merged


async def run(
    video: str | Path,
    opts: Options,
    emit: Emit,
    draft_root: Path | None = None,
    make_draft: bool = True,
) -> PipelineResult:
    video = Path(video)
    media = probe(video)

    # 사용자 실측 스타일을 기본값으로 덮어쓴다 (직접 지정한 값이 있으면 그대로 둔다)
    profile = StyleProfile.for_preset(opts.preset)
    if opts.use_profile:
        d = Options()          # 기본값과 비교해 '건드리지 않은 것' 만 교체
        if opts.noise_db == d.noise_db:
            opts.noise_db = profile.noise_db
        if opts.min_silence_s == d.min_silence_s:
            opts.min_silence_s = profile.min_silence_s
        if opts.pad_s == d.pad_s:
            opts.pad_s = profile.pad_s
        if opts.min_keep_s == d.min_keep_s:
            opts.min_keep_s = profile.min_keep_s
        if opts.subtitle_max_chars == d.subtitle_max_chars:
            opts.subtitle_max_chars = profile.subtitle_max_chars
        if opts.speed == d.speed:
            opts.speed = profile.speed

    await emit({
        "type": "media",
        "name": media.path.name,
        "width": media.width, "height": media.height,
        "fps": round(media.fps, 2),
        "duration_us": media.duration_us,
        "has_audio": media.has_audio,
        "preset": profile.preset,
        "preset_label": get_preset(profile.preset).label,
        "speed": profile.speed if opts.use_profile else opts.speed,
    })

    # ── 1. 무음 ──
    t0 = await _step(emit, "silence", 0)
    loop = asyncio.get_running_loop()
    silences, sil_keeps = await loop.run_in_executor(
        None, lambda: analyze(media.path, opts.silence_params(), media.duration_us))
    sil_kept = sum(k.dur for k in sil_keeps)
    thr = ""
    if opts.silence_params().auto_noise:
        try:                                   # 이 영상에 맞춰 잡은 무음 기준을 보여준다
            from .align import features
            from .silence import auto_noise_db
            thr = f" · 무음 기준 {auto_noise_db(features(media.path).db, opts.silence_params()):.0f}dB"
        except Exception:                      # noqa: BLE001
            thr = ""
    await _done(emit, "silence", t0,
                f"무음 {len(silences)}곳 · 남길 구간 {len(sil_keeps)}개{thr}",
                {"removed_us": media.duration_us - sil_kept})

    # ── 2. 대본 ──
    tr = None
    cached = False
    if opts.asr_enabled and media.has_audio:
        t0 = await _step(emit, "asr", 1)
        model = opts.model or asr_mod.DEFAULT_MODEL
        tr, cached = await asr_mod.transcribe(
            media.path, model=model, language=opts.language,
            use_cache=opts.use_cache, duration_us=media.duration_us)
        chars = sum(len(s.text) for s in tr.segs)
        await _done(emit, "asr", t0,
                    f"{len(tr.segs)}문장 · {chars}자" + (" · 캐시" if cached else ""),
                    {"cached": cached, "model": model})
    else:
        await emit({"type": "step", "step": "asr", "state": "skipped",
                    "label": STEP_LABEL["asr"],
                    "detail": "오디오 없음" if not media.has_audio else "건너뜀"})

    # ── 3. 문장 구성 ──
    #
    # 자막을 만들기 전에 대본을 문장 단위로 세운다. whisper 세그먼트는 호흡
    # 기준이라 문장이 갈라지거나 붙어 있는데, 그 상태로 자막을 끊으면 문장
    # 한복판에서 끊긴다. NG 판정도 문장끼리 비교해야 맞다.
    srt_text = ""
    if tr is not None:
        t0 = await _step(emit, "script", 2)
        before = len(tr.segs)
        tr = build_sentences(tr)
        # whisper 시각을 실제 소리에 맞춰 보정한다. 안 하면 자막이 말보다
        # 먼저 뜨거나 늦게 뜬다 (실측: 문장의 67% 가 0.1초 이상 어긋남).
        shift = await loop.run_in_executor(
            None, lambda: snap_to_audio(tr, media.path))
        # 문장을 맞춘 뒤 낱말도 실제 소리 위에 다시 배치한다.
        # 문장 시작 오차가 22% → 7% 로 떨어진다.
        #
        # 한국어 정렬 모델이 받아져 있으면 **강제 정렬**로 낱말마다 실제 발음 구간을
        # 찾는다 (2026-09-16). 소리 크기만 보는 재배치는 "어느 글자의 소리인가"를
        # 몰라서, 컷 경계 옆 낱말이 엉뚱한 클립에 붙었다 — forced_align.py 참고.
        from . import forced_align
        how = "소리 크기"
        if opts.forced_align and forced_align.available():
            try:
                moved = await loop.run_in_executor(
                    None, lambda: forced_align.align(tr, media.path))
                how = "강제 정렬"
            except Exception as e:                     # 모델이 깨져도 편집은 계속한다
                await emit({"type": "warn", "message": f"강제 정렬 실패 — 예전 방식으로: {e}"})
                moved = await loop.run_in_executor(
                    None, lambda: redistribute_words(tr, media.path))
        else:
            moved = await loop.run_in_executor(
                None, lambda: redistribute_words(tr, media.path))
        srt_text = transcript_to_srt(tr)
        await _done(emit, "script", t0,
                    f"{before}조각 → {len(tr.segs)}문장 · "
                    f"싱크보정 {shift:.2f}s · 낱말 재배치 {moved}개({how})",
                    {"sentences": len(tr.segs), "sync_shift": round(shift, 3),
                     "words_moved": moved})
    else:
        await emit({"type": "step", "step": "script", "state": "skipped",
                    "label": STEP_LABEL["script"], "detail": "대본 없음"})

    # ── 4. 잔말/NG ──
    fstats = FillerStats()
    if tr is not None and (opts.filler_enabled or opts.ng_enabled):
        t0 = await _step(emit, "filler", 3)
        fstats = mark(tr, opts.filler_params())
        # 한 문장 안으로 들어가 버린 되풀이 — build_sentences 가 whisper 조각을
        # 붙이면서 "왜 그런지랑 / 왜 그런지랑 / 왜 그런지랑 그 시기에…" 처럼
        # 문장 안에 실패한 시도가 갇힌다. 문장끼리 비교하는 mark 는 못 본다.
        inner = drop_inner_restarts(tr)
        # 소리로만 드러나는 말더듬 (whisper 가 지운 반복)
        weak_retakes = []
        gap_retakes = 0
        if opts.ng_enabled:
            weak_retakes = await loop.run_in_executor(
                None, lambda: drop_retakes(tr, media.path))
            # 강제 정렬이 찾은 낱말 사이 헛시작 (forced_align._gap_retakes)
            for s in tr.segs:
                for a, b in getattr(s, "fa_retakes", []):
                    s.media_holes.append((a, b))
                    gap_retakes += 1
        detail = f"잔말 {fstats.filler} · NG {fstats.ng}"
        if inner:
            detail += f" · 문장내 되풀이 {inner}"
        if gap_retakes:
            detail += f" · 헛시작 {gap_retakes}"
        if getattr(fstats, "tail_retakes", 0):
            detail += f" · 꼬리 되풀이 {fstats.tail_retakes}"
        if fstats.trimmed:
            detail += f" · 가장자리 {fstats.trimmed}"
        if weak_retakes:
            detail += f" · 의심 {len(weak_retakes)}"
        await _done(emit, "filler", t0, detail,
                    {"filler": fstats.filler, "ng": fstats.ng,
                     "trimmed": fstats.trimmed, "inner_restarts": inner,
                     "weak_retakes": [[a, b, round(c, 2)] for a, b, c in weak_retakes]})
    else:
        await emit({"type": "step", "step": "filler", "state": "skipped",
                    "label": STEP_LABEL["filler"], "detail": "대본 없음"})

    # ── 컷 통합 ──
    plan = build_keeps(sil_keeps, tr, media.duration_us, opts.min_keep_s,
                       silences=silences)
    plan.keeps = _apply_protect(plan.keeps, opts.protect, media.duration_us, sil_keeps)

    # 강의(VOD)인가. 여기서 갈리는 게 둘이다 — **효과음**과 **비트 카드**.
    #
    # 둘 다 유튜브 풀링 콘텐츠용이다. 강의는 끝까지 앉아서 이해하는 물건이라
    # 휙·훅 소리가 끼면 산만하고, 까만 카드가 끼면 흐름이 끊긴다.
    # (2026-08-20 대표님 지시)
    lecture = opts.slides or opts.preset == "vod"

    # 전환음 — 크게 튀는 컷 자리에만, 성기게. 강의에는 안 넣는다.
    sfx_clips = []
    if opts.sfx and not lecture:
        from .sfx import plan as plan_sfx
        out_total = int(round(sum(k.dur for k in plan.keeps) / (profile.speed if opts.use_profile else opts.speed)))
        sfx_clips = plan_sfx(plan.keeps,
                             profile.speed if opts.use_profile else opts.speed,
                             out_total)

    subs: list[SubtitlePiece] = []

    # 대표님이 손으로 끊어 둔 자막 대본이 있으면 **그 줄바꿈을 그대로 쓴다.**
    # 말맛과 강조는 사람이 정하는 것이라, 프로그램이 다시 쪼개면 안 된다.
    sub_lines = None
    sub_dropped: list[str] = []
    sub_changed: list[tuple[str, str]] = []
    if tr is not None and opts.subtitle_enabled and not opts.no_sub_script:
        _deck = (slides_mod.get_deck(opts.slide_deck) if opts.slide_deck
                 else slides_mod.deck_for(tr))
        _sp = getattr(_deck, "sub_script", None) if _deck else None
        if _sp and Path(_sp).exists():
            from . import subscript as subscript_mod
            sub_lines = subscript_mod.load_lines(_sp)
            subs, sub_dropped, sub_changed, _keeps2 = subscript_mod.build_pieces(
                sub_lines, tr, plan.keeps,
                profile.speed if opts.use_profile else opts.speed,
                video=media.path)
            if _keeps2:                     # 자막 줄 안쪽 짧은 쉼을 되살렸다
                plan.keeps = _keeps2
            await emit({"type": "step", "label": "자막 대본", "state": "done",
                        "detail": f"{Path(_sp).name} · {len(subs)}줄 사용"
                                  + (f" · 컷으로 빠진 줄 {len(sub_dropped)}"
                                     if sub_dropped else "")
                                  + (f" · 말이 달라진 줄 {len(sub_changed)}"
                                     if sub_changed else "")})

    # 풀링·키: 한 줄 = 자막 하나로 쓴 촬영대본이 있으면 그 줄바꿈을 쓴다 (2026-09-16).
    # 대표님이 프롬프터에서 읽은 그 줄이 그대로 자막이 된다.
    if (tr is not None and opts.subtitle_enabled and sub_lines is None
            and not lecture and not opts.no_sub_script):
        from . import subscript as subscript_mod
        found = None
        if opts.script_path and Path(opts.script_path).exists():
            found = (Path(opts.script_path), 1.0, True)
        else:
            found = await loop.run_in_executor(None, lambda: subscript_mod.find_script(tr))
        if found and found[2]:
            _sp = found[0]
            sub_lines = subscript_mod.load_lines(_sp)
            subs, sub_dropped, sub_changed, _keeps2 = subscript_mod.build_pieces(
                sub_lines, tr, plan.keeps,
                profile.speed if opts.use_profile else opts.speed,
                video=media.path)
            if _keeps2:                     # 자막 줄 안쪽 짧은 쉼을 되살렸다
                plan.keeps = _keeps2
            if opts.auto_emphasis:
                _emphasize_script_pieces(subs)
            await emit({"type": "step", "label": "자막 대본", "state": "done",
                        "detail": f"{Path(_sp).name} (대본 닮음 {found[1]:.0%}) · {len(subs)}줄 사용"
                                  + (f" · 말이 달라진 줄 {len(sub_changed)}"
                                     if sub_changed else "")})
        elif found:
            await emit({"type": "warn", "message":
                        f"촬영대본 {found[0].name} 을 찾았지만 문장 줄 형식이라 안 씀 — "
                        f"tools/make_subtitle_script.py 로 자막 줄로 나누면 그 줄대로 자막이 나옵니다"})

    if tr is not None and opts.subtitle_enabled and sub_lines is None:
        subs = map_subtitles(plan.keeps, tr,
                             max_chars=opts.subtitle_max_chars,
                             fill_gaps=opts.subtitle_fill_gaps,
                             emphasis_plan=(load_emphasis_plan(opts.emphasis_plan)
                                            if opts.emphasis_plan else None),
                             auto_emphasis=False)
        # 자막 경계를 컷에 맞춘다 (2026-10-05) — 촬영대본 경로에만 있던 보정을
        # 받아쓰기 경로에도 넣는다. 안 하면 자막이 컷을 가로지르는 비율이 10% 까지 간다.
        if subs:
            from .cuts import protect_inner_gaps, snap_pieces_to_cuts
            k2 = protect_inner_gaps(subs, plan.keeps)
            if k2:
                plan.keeps = k2
                subs = map_subtitles(plan.keeps, tr,
                                     max_chars=opts.subtitle_max_chars,
                                     fill_gaps=opts.subtitle_fill_gaps,
                                     emphasis_plan=(load_emphasis_plan(opts.emphasis_plan)
                                                    if opts.emphasis_plan else None),
                                     auto_emphasis=False)
            n_snap = snap_pieces_to_cuts(subs, plan.keeps)
            if n_snap:
                await emit({"type": "warn",
                            "message": f"자막 경계 {n_snap}줄을 컷 자리에 맞췄습니다"
                                       + (f" · 줄 안쪽 짧은 쉼 되살림" if k2 else "")})

        # 강조 색은 **자막 줄을 실제 문장으로 다시 묶어서** 고른다 (2026-09-17).
        # map_subtitles 안에서 고르면 받아쓰기가 묶은 긴 덩어리(문장 여러 개) 단위라
        # 덩어리마다 끝 두 줄만 칠해져 풀링4 에서 13% 에 그쳤다 (대표님 25%).
        # 대표님은 실제 문장마다 칠하신다 — 촬영대본 줄과 같은 길로 간다.
        if subs and opts.auto_emphasis and not lecture and not opts.emphasis_plan:
            _emphasize_script_pieces(subs)

    # ── 비트 카드 ──
    #
    # 자막 조각과 **같은 좌표계**에서 자리를 잡는다. 자막 시작에 정확히 붙어야
    # 하는데(대표님 손질본 9/9 실측), 다른 데서 계산하면 반올림이 어긋난다.
    beat_clips: list = []
    picked: list = []
    # 까만 전체화면 비트 카드는 **유튜브 풀링 콘텐츠용**이다.
    #
    # 강의(VOD)에는 넣지 않는다. 장표가 그 자리를 쓰기 때문에 같은 오버레이
    # 트랙에서 겹치고(실측 점유 105%), 캡컷이 겹친 걸 별도 트랙으로 밀어내면서
    # 화면이 뒤엉킨다. 무엇보다 강의 중간에 까만 화면이 끼면 흐름이 끊긴다.
    if opts.beats and subs and not lecture:
        from . import beats as beats_mod
        from .draft import CardClip

        sp = profile.speed if opts.use_profile else opts.speed
        tl = [(int(round(p.start / sp)), int(round(p.dur / sp)), p.text)
              for p in subs]
        out_total = int(round(sum(k.dur for k in plan.keeps) / sp))
        bplan = (json.loads(Path(opts.beat_plan).read_text(encoding="utf-8"))
                 if opts.beat_plan else beats_mod.pick(tl, out_total))
        picked, missing = beats_mod.resolve(bplan, tl, out_total)
        for m in missing:
            await emit({"type": "warn", "message": f"비트 앵커 못 찾음: {m}"})

        bdir = WORK_DIR / "beats" / (opts.draft_name or media.path.stem)
        for i, b in enumerate(picked):
            png = beats_mod.render(b, media.width, media.height,
                                   bdir / f"beat{i:02d}.png")
            # 장표와 같은 규칙으로 굽는다 — 넉넉히 만들고 가운데를 쓴다.
            # 딱 맞춰 구우면 캡컷에서 길이를 못 늘린다 (장표에서 겪은 그 문제).
            clip_us = int(slides_mod.CLIP_LEN_S * US)
            pad_us = int(slides_mod.CLIP_PAD_S * US)
            mp4 = slides_mod.to_clip(png, clip_us, media.width, media.height,
                                     media.fps, bdir / f"beat{i:02d}.mp4")
            src0 = min(pad_us, max(0, clip_us - b.dur_us))
            beat_clips.append(CardClip(start=b.at_us, dur=b.dur_us, path=mp4,
                                       source_dur=clip_us, source_start=src0,
                                       fullscreen=True))
        # 카드 밑 자막 정리 — 두 영상에서 대표님이 손으로 하신 것.
        #   · 카드가 덮는 줄은 **색을 빼고** (카드가 이미 강조다)
        #   · 차례 카드·나열 카드가 아니면 그 줄 자막을 **내린다**
        #     (풀링3·0916 에서 12줄을 직접 지우셨다 — beats.hides_subtitle 참고)
        if picked:
            drop: set[int] = set()
            for b in picked:
                b_end = b.at_us + b.dur_us
                hide = opts.beat_hides_subtitle and beats_mod.hides_subtitle(b)
                for k, (ps, pd, _t) in enumerate(tl):
                    if ps >= b.at_us - 20_000 and ps + pd <= b_end + 20_000:
                        subs[k].kind = "normal"
                        if hide:
                            drop.add(k)
            if drop:
                subs = [p for k, p in enumerate(subs) if k not in drop]
                await emit({"type": "warn",
                            "message": f"카드 밑 자막 {len(drop)}줄 내림 (카드가 그 말을 보여줌)"})

        if picked:
            (bdir / "plan.json").write_text(
                json.dumps(bplan, ensure_ascii=False, indent=1), encoding="utf-8")
            await emit({"type": "beats", "count": len(picked),
                        "rows": [[b.at_us, b.dur_us, b.label, b.text]
                                 for b in picked]})

    # ── 5. 드래프트 ──
    draft = None
    if make_draft:
        t0 = await _step(emit, "draft", 4)
        name = opts.draft_name or f"{video.stem}_{time.strftime('%m%d_%H%M')}"

        zooms = zoom_offs = None
        draft_keeps = plan.keeps
        if opts.auto_zoom and subs and not lecture and not opts.reframe:
            from . import finish
            _sp = profile.speed if opts.use_profile else opts.speed
            _lines = [finish.Line(p.start, p.dur, p.text, p.kind) for p in subs]
            _cover = [(int(b.at_us * _sp), int((b.at_us + b.dur_us) * _sp)) for b in picked]
            _pre_total = sum(k.dur for k in plan.keeps)
            zspans = finish.plan_zoom(_lines, _pre_total, _cover)
            draft_keeps, zooms = finish.split_keeps(plan.keeps, zspans)
            if opts.sfx:
                sfx_clips = finish.plan_sfx(zspans, plan.keeps, _sp, lines=_lines, total_out_us=
                                            int(round(_pre_total / _sp)))
            await emit({"type": "warn", "message":
                        f"마감: 확대 {len(zspans)}곳 · 클립 {len(plan.keeps)}→{len(draft_keeps)} · "
                        f"효과음 {len(sfx_clips)}개"})
        if opts.reframe:
            subj = await loop.run_in_executor(
                None, lambda: find_subject(media.path, media.width, media.height))
            zooms, zoom_offs = plan_zooms(len(plan.keeps), subj,
                                          profile.framings, profile.zoom_ratio)

        # ── 장표 오버레이 (VOD 강의) ──
        #
        # 오버레이 트랙은 하나뿐이고 그 안에서 세그먼트가 겹치면 캡컷이 못 연다.
        # 장표는 다음 큐까지 계속 떠 있어야 해서 트랙을 통째로 쓴다 —
        # 그래서 장표를 켜면 카드·사진은 넣지 않는다.
        card_clips = None
        if opts.slides and tr is not None:
            deck = (slides_mod.get_deck(opts.slide_deck) if opts.slide_deck
                    else slides_mod.deck_for(tr))
            if deck is None:
                await emit({"type": "warn", "message":
                            "장표: 어느 강의인지 대본으로 못 알아봤습니다"})
            else:
                cues, sents = deck.load()
                resolved = slides_mod.resolve(cues, sents, tr)
                # 말하는 구간만 재게 한다 (앞뒤 녹화 오가는 장면 제외)
                speak0 = tr.segs[0].start if tr.segs else 0
                speak1 = tr.segs[-1].end if tr.segs else media.duration_us
                zone = await loop.run_in_executor(
                    None, lambda: slides_mod.measure_safe_zone(
                        media.path, media.duration_us,
                        start_us=speak0, end_us=speak1))
                # 컷 경계를 두 좌표계로 센다.
                #
                # 자막(SubtitlePiece)은 **배속 걸기 전** 좌표를 들고 있고
                # (draft.build_draft 가 나중에 speed 로 나눈다), 장표 배치는
                # **배속 후** 타임라인에서 한다. 한 가지로 뭉뚱그리면 1.2배속
                # 프리셋에서 통째로 어긋난다.
                raw, post = 0, 0
                cut_pre, cut_post = [0], [0]
                for k in plan.keeps:
                    raw += k.dur
                    post += max(1, int(round(k.dur / opts.speed)))
                    cut_pre.append(raw)
                    cut_post.append(post)

                # 자막을 먼저 컷에 맞춘다. 영상·자막·장표가 같은 자리에서
                # 바뀌어야 이음매가 깔끔하다 (완성본 3편에서 확인한 기준).
                if subs:
                    n, avg = slides_mod.align_subtitle_pieces(subs, cut_pre)
                    if n:
                        await emit({"type": "warn", "message":
                                    f"자막 {n}개를 컷에 맞췄습니다 (평균 {avg:.3f}초)"})

                sub_post = []
                for pc in subs:
                    sub_post += [int(round(pc.start / opts.speed)),
                                 int(round((pc.start + pc.dur) / opts.speed))]

                plans, warns = slides_mod.plan_slides(
                    resolved, deck.slide_dir, plan.keeps, opts.speed, zone, media,
                    snap=slides_mod.make_snapper(cut_post, sub_post))
                card_clips = await loop.run_in_executor(
                    None, lambda: slides_mod.build_clips(
                        plans, media, WORK_DIR / "slides" / deck.key))
                for w in warns:
                    await emit({"type": "warn", "message": f"장표: {w}"})
                await emit({
                    "type": "slides",
                    "deck": deck.label,
                    "zone": f"{zone.x0:.0%}~{zone.x1:.0%} ({zone.detail})",
                    "rows": [[r.cue.label, r.cue.at_s, r.src_us, r.ratio,
                              r.heard, r.why] for r in resolved
                             if not r.cue.blank],
                })
            if opts.cards or opts.stock:
                await emit({"type": "warn", "message":
                            "장표를 켜서 카드·사진은 건너뜁니다 (오버레이 트랙 1개)"})

        elif (opts.cards or opts.stock) and tr is not None:
            card_clips = []
            if opts.cards:
                found = find_cards(tr.segs, profile, max_cards=opts.max_cards)
                card_clips += await loop.run_in_executor(
                    None,
                    lambda: render_cards(found, plan.keeps, media, opts.speed,
                                         WORK_DIR / "cards" / video.stem))
            if opts.stock:
                card_clips += await loop.run_in_executor(
                    None,
                    lambda: find_stock(tr.segs, plan.keeps, media, opts.speed,
                                       WORK_DIR / "stock",
                                       plan_path=opts.image_plan))
            # 카드와 사진이 겹치면 캡컷에서 로드가 깨진다
            card_clips.sort(key=lambda x: x.start)
            for i in range(len(card_clips) - 1):
                over = (card_clips[i].start + card_clips[i].dur) - card_clips[i + 1].start
                if over > 0:
                    card_clips[i].dur -= over
            card_clips = [x for x in card_clips if x.dur > 0]

        styles = opts.subtitle_styles(profile) if opts.use_profile else None
        draft = await loop.run_in_executor(
            None,
            lambda: build_draft(media, draft_keeps, name, draft_root=draft_root,
                                sfx=sfx_clips,
                                beauty=opts.beauty and not lecture,
                                filter_4k=opts.filter_4k and not lecture,
                                subtitles=subs if subs else None,
                                style=opts.subtitle_style(), styles=styles,
                                speed=opts.speed, zooms=zooms,
                                zoom_offsets=zoom_offs,
                                cards=_no_overlap((card_clips or []) + beat_clips)
                                or None))
        # 대본과 최종 자막을 SRT 로 드래프트 폴더에 같이 남긴다.
        # 자막을 손보고 싶을 때 이걸 열어서 고치는 게 제일 빠르다.
        try:
            if srt_text:
                write_srt(draft.folder / "대본.srt", srt_text)
                # 자막 대본에서 빠진 줄을 적어 둔다. 그 대목 음성이 컷으로
                # 사라졌다는 뜻인데, 잘못 잘린 건 아닌지 눈으로 봐야 한다.
                if sub_dropped:
                    _msg = [
                        "이 줄들은 해당 음성이 컷으로 사라져서 자막에 안 들어갔습니다.",
                        "잘못 잘린 것 같으면 알려주세요.",
                        "",
                    ] + list(sub_dropped)
                    (draft.folder / "빠진 자막.txt").write_text(
                        chr(10).join(_msg), encoding="utf-8")
                if sub_changed:
                    _c = ["촬영하며 말이 달라진 줄입니다. 자막은 **실제로 하신 말**로 넣었습니다.",
                          "", ]
                    for _a, _b in sub_changed:
                        _c.append("대본: " + _a)
                        _c.append("실제: " + _b)
                        _c.append("")
                    (draft.folder / "달라진 자막.txt").write_text(
                        chr(10).join(_c), encoding="utf-8")
            if subs:
                write_srt(draft.folder / "자막.srt",
                          to_srt([(p.start, p.start + p.dur, p.text) for p in subs]))
        except OSError:
            pass  # SRT 저장 실패가 드래프트를 못 쓰게 만들진 않는다

        # ── 장표를 드래프트 기준으로 한 번 더 맞춘다 ──
        #
        # 여기까지의 장표 자리는 `plan.keeps` 로 계산한 것이고, 실제로 캡컷에
        # 적히는 타임라인은 build_draft 가 만든다. 이 둘이 미세하게 달라질 수
        # 있는데, 실측에서 2강이 통째로 0.2초씩 밀려 나왔다 (셋 다 일치 3/65).
        # 원인을 하나로 특정하기보다 **결과를 정답으로 삼는다** — 다 만든 뒤
        # 그 드래프트의 메인 트랙을 읽어 장표를 다시 깐다. 그러면 어긋날 수가 없다.
        # (같은 방식으로 다시 깔았더니 3/65 → 51/65 로 올라왔다)
        if opts.slides:
            try:
                from tools.relay_slides import run as _relay
                await _relay(draft.name, "", opts.slide_deck, inplace=True,
                             force=True, align_subs=True)
            except Exception as e:                      # noqa: BLE001
                await emit({"type": "warn",
                            "message": f"장표 재정렬 건너뜀: {e}"})

        # ── 자막 경계 ↔ 컷 경계 자체 검사 (2026-09-24 대표님 지시) ──
        #
        # "자막 자른 것과 영상 자른 것이 똑같이 맞아떨어져야 한다. 절대 실수하면 안 된다."
        # 대표님 완성본 실측: 자막이 컷을 가로지르는 비율 0.8~4.6%. 5% 를 넘으면 알린다.
        if subs:
            _b, _acc = [], 0
            for _k in draft_keeps:
                _acc += _k.dur
                _b.append(_acc)
            # 가로지르는 줄을 둘로 나눈다. 길게(1초 이상) 쉬고 이어 말한 자리는
            # 자르는 게 맞고 대표님 완성본에도 있다. 짧은 쉼을 잘라 생긴 것만 고칠 거리다.
            _gapof = {}
            for _k1, _k2, _x in zip(draft_keeps, draft_keeps[1:], _b):
                _gapof[_x] = _k2.start - _k1.end
            _cross = _fix = 0
            for _p in subs:
                _hit = [_x for _x in _b if _p.start < _x < _p.start + _p.dur]
                if not _hit:
                    continue
                _cross += 1
                if any(_gapof.get(_x, 0) < 1_000_000 for _x in _hit):
                    _fix += 1
            _ratio = _cross / max(1, len(subs))
            _msg = (f"자막·컷 경계: 가로지르는 줄 {_cross}개({_ratio:.0%}) — "
                    f"그중 짧은 쉼을 잘라 생긴 것 {_fix}개")
            if _ratio > 0.05 and _fix > 0.02 * len(subs):
                await emit({"type": "warn", "message": "⚠ " + _msg +
                            " · 기준(5%)을 넘습니다. tools/check_sync.py 로 확인하세요"})
            else:
                await emit({"type": "warn", "message": _msg + " · 기준 안"})

        await _done(emit, "draft", t0,
                    f"세그먼트 {draft.segments}개 · 자막 {draft.subtitles}개",
                    {"folder": str(draft.folder)})

    # 배속을 걸면 타임라인이 그만큼 짧아진다. 결과 길이는 배속까지 반영해야
    # 사용자가 캡컷에서 보는 숫자와 맞는다.
    total = int(round(plan.total_us / opts.speed))
    stats = {
        "original_us": media.duration_us,
        "cut_us": plan.total_us,
        "speed": opts.speed,
        "total_us": total,
        "removed_us": media.duration_us - total,
        "removed_pct": round((media.duration_us - total) / media.duration_us * 100, 1)
        if media.duration_us else 0.0,
        "segments": len(plan.keeps),
        "subtitles": len(subs),
        "silence_removed_us": plan.silence_removed_us,
        "speech_removed_us": plan.speech_removed_us,
        "filler": fstats.filler,
        "ng": fstats.ng,
        "trimmed": fstats.trimmed,
        "asr_cached": cached,
    }

    await emit({
        "type": "result",
        "stats": stats,
        "draft": {"name": draft.name, "folder": str(draft.folder)} if draft else None,
        "keeps": [[k.start, k.end] for k in plan.keeps],
        "subtitles": [[p.start, p.dur, p.text] for p in subs],
        "srt": transcript_to_srt(tr) if tr else "",
        "transcript": tr.to_dict() if tr else None,
    })

    return PipelineResult(media=media, plan=plan, draft=draft,
                          transcript_dict=tr.to_dict() if tr else None, stats=stats)
