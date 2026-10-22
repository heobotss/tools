"""마감 — 확대와 효과음을 대표님 완성본 규칙대로 놓는다.

2026-09-17, 대표님이 끝까지 다듬은 완성본 세 편(풀링4 · 풀링3 · 키컨텐츠 2 완성)을
클립·자막·소리 단위로 뜯어서 나온 규칙이다 (tools/dump_draft.py, .work/learn/).

## 확대

  · 배율은 **1.2, 가운데** (x=y=0). 세 편 확대 클립 189개 중 176개가 1.2 였다.
  · 확대 구간은 **자막 줄 경계에 0ms 로 붙는다** — 세 편 모두 시작·끝 사분위가 0/0.
    클립을 자막 경계에서 쪼개서 그 줄들만 당긴다.
  · 무엇을 당기나:
      - **강조 색 덩어리** (연달아 칠한 같은 색 줄들). 덩어리의 약 80%가 확대됐다.
        확대가 빠진 덩어리는 대부분 바로 앞뒤 2초 안에 다른 확대가 있던 자리 —
        확대끼리 붙지 않게 쉬어 간다.
      - 색이 없어도 당기는 줄 (편당 15개 안팎): "집중해서 들어주세요" 같은 재집중
        콜아웃, "~까지 들으시면" 약속, "두 가지입니다·결국 하나" 같은 구조 선언,
        남의 말을 옮기는 따옴표 대사, 나열.
  · **나열은 계단식** — 같은 꼴로 끝나는 줄이 셋 이상 이어지면 1.1 → 1.2 → 1.3.
    ("센터 직원은 ~끝이고 / PT샵은 ~끝이고 / 대형 센터는 ~끝입니다")
  · 확대 사이 간격 중앙 5.4초, 확대 구간 길이 중앙 3초, 전체 시간의 31~37%.

## 효과음

  · 편당 52~69개, **1분에 7~9개**. 간격 중앙 7초, 가장 좁게는 1초 안팎.
  · **자막 줄이 시작하는 순간에** 놓는다 (소리별 줄 시작 대비 중앙 0ms).
  · 자리: 확대 시작의 60~66% · 원본에서 크게(2.5초 안팎) 잘려나간 컷 · 카드는 드묾.
  · 소리는 **말의 뜻**으로 고른다:
        휙·훅       화제 전환 컷            Clav Grv   확대하며 핵심을 칠 때(빨강 많음)
        땡          질문·짚기·색 없는 확대   Bell 02    좋은 쪽 통찰(파랑)
        Click      숫자·"딱 하나"·가지 수    오답        "틀렸습니다"
        유리 깨짐   "끝입니다·줄어듭니다"    Tears      안타까움·그만둠
        알림 땡     "보고 왔어요·글 봤어요"   Wow        "올라가셨네요"
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .sfx import SfxClip, load_palette
from .silence import Span

US = 1_000_000

ZOOM = 1.2
STEP = 0.1
STEP_MAX = 1.4
ZOOM_GAP_US = 1_000_000       # 확대와 확대 사이에 최소한 둘 원래 화면
SPLIT_MIN_US = 120_000        # 이보다 짧은 조각이 생기면 쪼개지 않고 클립 경계에 붙인다
WHITE_PER_MIN = 2.2           # 색 없는 확대 구간 수 (편당 14~16개 / 7~8분)

SFX_PER_MIN = 8.0
SFX_GAP_US = 1_500_000        # 효과음끼리 최소 간격
SFX_OPENING_US = 5_000_000    # 첫 훅 동안은 비운다
JUMP_US = 2_500_000           # 이만큼 넘게 잘려나간 컷이면 전환음 후보
JUMP_APART_US = 3_000_000     # 전환음은 다른 효과음과 이만큼 떨어뜨린다
ZOOM_HIT_RATIO = 0.65         # 확대 시작에 소리를 얹는 비율


@dataclass
class Line:
    start: int      # 배속 전 타임라인 좌표 (SubtitlePiece 와 같음)
    dur: int
    text: str
    kind: str       # normal / neg / pos

    @property
    def end(self) -> int:
        return self.start + self.dur


@dataclass
class ZoomSpan:
    start: int
    end: int
    scales: list[tuple[int, int, float]]   # (시작, 끝, 배율) — 계단식이면 줄마다
    lines: list[Line]
    why: str


# ── 색 없이도 당기는 줄 ──────────────────────────────────────────────
_WHITE_RULES: list[tuple[str, float, re.Pattern]] = [
    ("재집중", 5.0, re.compile(r"집중해서|제일 중요한|잘 들어")),
    ("약속", 4.0, re.compile(r"들으시면|듣고 가셔|들으셔야|알고 가세요|챙기세요|이것만")),
    ("구조", 3.5, re.compile(r"(두|세|네) ?가지|딱 하나|결국 하나|하나(예|에)요|하나입니다|세 번째|두 번째")),
    ("대사", 3.0, re.compile(r"우리 선생님|선생님이세요|보고 왔어요|글 봤어요|거기 어디")),
    ("지목", 2.5, re.compile(r"분들은요|선생님들은요|초보 트레이너")),
    ("뒤집기", 2.0, re.compile(r"사실 |아니라|같은 (일|얘기)")),
    ("질문", 1.5, re.compile(r"\?$")),
]


def white_score(text: str) -> tuple[float, str]:
    best = (0.0, "")
    for why, w, rx in _WHITE_RULES:
        if rx.search(text.strip()) and w > best[0]:
            best = (w, why)
    return best


def _ending(text: str) -> str:
    t = re.sub(r"[^가-힣?]", "", text)
    return t[-2:] if len(t) >= 2 else t


def _is_list(lines: list[Line]) -> bool:
    """같은 꼴로 끝나는 줄이 셋 이상 — 나열.

    "선생님 이름, / 회원님이 뭐가 얼마나 좋아졌는지, / 그리고 실제로 운동하는 사진"
    처럼 쉼표로 이어지는 나열, "~있고 / ~있고 / ~알려줬어요", "~나? / ~나? / ~나?".
    마지막 줄은 끝맺음이라 꼴이 달라도 된다.
    """
    if len(lines) < 3:
        return False
    heads = lines[:-1]
    if all(x.text.strip().endswith(",") for x in heads):
        return True
    ends = [_ending(x.text) for x in heads]
    last1 = [e[-1:] for e in ends]
    return len(set(ends)) == 1 or (len(set(last1)) == 1 and last1[0] in "고?며서면")


def _groups(lines: list[Line]) -> list[list[Line]]:
    """연달아 칠한 같은 색 줄을 덩어리로."""
    out: list[list[Line]] = []
    for ln in lines:
        if ln.kind == "normal":
            continue
        if out and out[-1][-1].kind == ln.kind and abs(out[-1][-1].end - ln.start) <= 100_000:
            out[-1].append(ln)
        else:
            out.append([ln])
    return out


def plan_zoom(lines: list[Line], total_pre_us: int,
              covered: list[tuple[int, int]] | None = None) -> list[ZoomSpan]:
    """확대할 구간을 고른다. covered = 카드가 덮는 구간(배속 전 좌표) — 거기선 안 당긴다."""
    covered = covered or []

    def under_card(a: int, b: int) -> bool:
        return any(min(b, y) - max(a, x) > 0.5 * (b - a) for x, y in covered)

    cands: list[tuple[float, list[Line], str]] = []
    for g in _groups(lines):
        cands.append((10.0 + len(g) * 0.1, g, "강조"))

    # 나열 — 색과 상관없이 통째로 계단식 확대 후보
    i = 0
    while i < len(lines) - 2:
        j = i + 1
        while j < len(lines) and abs(lines[j - 1].end - lines[j].start) <= 100_000 and j - i < 5:
            j += 1
        best = None
        for k in range(j, i + 2, -1):
            if _is_list(lines[i:k]):
                best = k
                break
        if best:
            cands.append((12.0, lines[i:best], "나열"))
            i = best
        else:
            i += 1

    # 색 없는 줄: 점수 있는 줄을 이웃과 묶는다 (같은 규칙에 걸린 이웃 줄, 나열)
    i = 0
    while i < len(lines):
        ln = lines[i]
        sc, why = white_score(ln.text)
        if ln.kind != "normal" or sc <= 0:
            i += 1
            continue
        g = [ln]
        j = i + 1
        while j < len(lines) and lines[j].kind == "normal" and abs(lines[j - 1].end - lines[j].start) <= 100_000:
            s2, _ = white_score(lines[j].text)
            if s2 <= 0 and not _is_list(g + [lines[j]]):
                break
            g.append(lines[j])
            j += 1
            if len(g) >= 4:
                break
        cands.append((sc, g, why))
        i = j

    budget_white = int(total_pre_us / US / 60 * WHITE_PER_MIN) + 1
    cands.sort(key=lambda c: -c[0])
    chosen: list[ZoomSpan] = []
    n_white = 0
    for sc, g, why in cands:
        a, b = g[0].start, g[-1].end
        if under_card(a, b):
            continue
        if any(a < z.end + ZOOM_GAP_US and z.start - ZOOM_GAP_US < b for z in chosen):
            continue
        if why not in ("강조", "나열"):
            if n_white >= budget_white:
                continue
            n_white += 1
        if _is_list(g):
            scales = [(x.start, x.end, round(min(STEP_MAX, 1.1 + STEP * k), 2))
                      for k, x in enumerate(g)]
        else:
            scales = [(a, b, ZOOM)]
        chosen.append(ZoomSpan(a, b, scales, g, why))
    chosen.sort(key=lambda z: z.start)
    return chosen


def split_keeps(keeps: list[Span], zooms: list[ZoomSpan]) -> tuple[list[Span], list[float]]:
    """확대 경계에서 클립을 쪼개고 조각마다 배율을 돌려준다.

    경계는 배속 전 타임라인 좌표(자막과 같은 좌표)다. 클립 안에 떨어지면 원본 좌표로
    옮겨 쪼갠다. 클립 경계에서 SPLIT_MIN_US 안쪽이면 쪼개지 않고 그 경계를 쓴다.
    """
    bounds: list[tuple[int, int, float]] = [s for z in zooms for s in z.scales]
    cuts = sorted({p for a, b, _ in bounds for p in (a, b)})
    out: list[Span] = []
    acc = 0
    for k in keeps:
        k0, k1 = acc, acc + k.dur
        # 경계끼리도 SPLIT_MIN_US 는 떨어져야 한다. 안 그러면 확대 경계 두 개가
        # 가까이 붙은 자리에서 6ms 짜리 먼지 클립이 생긴다 (2026-09-23 실측).
        inner: list[int] = []
        for c in cuts:
            if not (k0 + SPLIT_MIN_US <= c <= k1 - SPLIT_MIN_US):
                continue
            if inner and c - inner[-1] < SPLIT_MIN_US:
                continue
            inner.append(c)
        pts = [k0] + inner + [k1]
        for a, b in zip(pts, pts[1:]):
            out.append(Span(k.start + (a - k0), k.start + (b - k0)))
        acc = k1
    scales: list[float] = []
    acc = 0
    for sp in out:
        mid = acc + sp.dur / 2
        z = 1.0
        for a, b, s in bounds:
            if a <= mid < b:
                z = s
                break
        scales.append(z)
        acc += sp.dur
    return out, scales


# ── 효과음 ─────────────────────────────────────────────────────────
_SFX_RULES: list[tuple[re.Pattern, str]] = [
    # 2026-09-25 대표님 손질에서 배운 것: 차례를 짚는 줄("첫 번째,", "좋은 센터의 조건
    # 두 번째")에는 **Bell 02** 를 쓰신다. 내가 Clav Grv·훅 을 넣은 5곳을 전부 Bell 로
    # 바꾸거나 새로 넣으셨다.
    (re.compile(r"(첫|두|세|네|다섯)\s*번째|첫째|둘째|셋째"), "Bell 02"),
    (re.compile(r"틀렸|틀리|틀린"), "오답"),
    (re.compile(r"끝이고|끝입니다|끝이에요|퇴사|무너|줄어듭|줄어요"), "유리 깨지는 소리"),
    (re.compile(r"안타깝|그만두|헤매|눈물|서럽"), "Tears of tears(52634)"),
    (re.compile(r"보고 왔어요|글 봤어요|연락|좋은 소식|문의가|알림"), "팔로우 알림 “땡”"),
    (re.compile(r"올라가셨|늘었|와\.\.|대박"), "Wow"),
    (re.compile(r"맞아요|맞습니다|정답"), "You Are Right_01"),
    (re.compile(r"\d|딱 하나|(두|세|네) ?가지|몇 킬로|몇 퍼센트"), "Click_Mouse_Click_02(864360)"),
    (re.compile(r"\?$|는지[,.]?$"), "땡"),   # 질문·"어떤 …는지," 나열 (09-25 대표님이 두 곳에 넣으심)
]


_ROTATE = {
    # 완성본 세 편 실측 (자리 × 색 → 가장 많이 쓴 소리)
    ("zoom", "neg"): ["Clav Grv"],                                  # 12/21
    ("zoom", "pos"): ["Bell 02", "휙", "Click_Mouse_Click_02(864360)"],  # 휙7 Bell5 Click4
    ("zoom", "normal"): ["Clav Grv", "땡", "훅"],                    # 8 · 7 · 6
    ("cut", "normal"): ["훅", "휙"],                                 # 18 · 17 / 55
    # 2026-09-25 검증: 완성본 5편에서 Clav Grv 49개 중 24개(49%)가 빨강 줄에 붙었다.
    # 효과음이 붙은 줄 전체의 빨강 비율은 20% — 기준선의 2.5배다. 대표님도
    # "Clav Grv 는 보통 부정적인 걸 강조할 때 쓴다"고 하셨다. 부정 줄은 이 소리로 간다.
    ("cut", "neg"): ["Clav Grv"],
    ("cut", "pos"): ["Click_Mouse_Click_02(864360)", "땡"],
}


def pick_sound(text: str, kind: str, pal: dict, where: str = "zoom", turn: int = 0) -> str:
    """뜻이 분명한 말은 그 소리로, 아니면 자리·색별로 대표님이 가장 많이 쓴 소리를 돌려 쓴다."""
    for rx, name in _SFX_RULES:
        if rx.search(text.strip()) and name in pal:
            if name == "땡" and where == "cut":
                continue
            return name
    opts = [n for n in _ROTATE.get((where, kind), _ROTATE[("cut", "normal")]) if n in pal]
    return opts[turn % len(opts)] if opts else next(iter(pal))


def plan_sfx(zooms: list[ZoomSpan], keeps: list[Span], speed: float,
             total_out_us: int, palette: dict | None = None,
             lines: list[Line] | None = None) -> list[SfxClip]:
    """확대 시작과 크게 잘린 컷에 효과음을 놓는다. keeps 는 **쪼개기 전** 클립.

    완성본에서 확대 시작에 소리를 넣은 곳은 앞 효과음과 7.2초, 안 넣은 곳은 4.4초
    떨어져 있었다 — 색보다 **간격**이 기준이다. 확대 없는 컷은 원본에서 2.5초 넘게
    잘린 곳의 1/3 에 넣었다. 그래서 후보를 모아 간격을 벌려 가며 분당 8개가 되게 고른다.
    """
    pal = palette if palette is not None else load_palette()
    if not pal:
        return []
    cands: list[tuple[int, float, str, str, str]] = []   # (위치, 우선, 자리, 글, 색)
    for z in zooms:
        at = int(round(z.start / speed))
        first = z.lines[0] if z.lines else Line(z.start, 0, "", "normal")
        cands.append((at, 2.0, "zoom", first.text, first.kind))
    zstarts = {int(round(z.start / speed)) for z in zooms}
    acc = 0
    for a, b in zip(keeps, keeps[1:]):
        acc += a.dur
        gap = b.start - a.end
        if gap < JUMP_US:
            continue
        at = int(round(acc / speed))
        if any(abs(at - z) < 300_000 for z in zstarts):
            continue
        text, kind = "", "normal"
        if lines:
            ln = next((x for x in lines if abs(x.start - acc) <= 150_000), None)
            if ln:
                text, kind = ln.text, ln.kind
        cands.append((at, 1.0 + min(gap, 8_000_000) / 16_000_000, "cut", text, kind))
    cands = [c for c in cands if SFX_OPENING_US <= c[0] < total_out_us]
    target = int(total_out_us / US / 60 * SFX_PER_MIN)

    def choose(gap_us: int):
        got: list[tuple[int, float, str, str, str]] = []
        for c in sorted(cands, key=lambda c: -c[1]):
            if all(abs(c[0] - g[0]) >= gap_us for g in got):
                got.append(c)
        return got

    lo, hi = SFX_GAP_US, 12_000_000
    best = choose(lo)
    while hi - lo > 200_000:                 # 간격을 넓혀 가며 목표 개수에 맞춘다
        mid = (lo + hi) // 2
        got = choose(mid)
        if len(got) >= target:
            best, lo = got, mid
        else:
            hi = mid
    best.sort()
    turns: dict[tuple[str, str], int] = {}
    out = []
    for at, _, where, text, kind in best:
        key = (where, kind)
        name = pick_sound(text, kind, pal, where, turns.get(key, 0))
        turns[key] = turns.get(key, 0) + 1
        out.append(SfxClip(at_us=at, name=name, dur_us=int(pal[name]["duration"])))
    return out
