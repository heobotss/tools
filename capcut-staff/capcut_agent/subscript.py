"""대표님이 손으로 끊어 둔 자막 대본을 그대로 쓴다.

## 왜 필요한가

지금까지는 프로그램이 한국어 끊어읽기 규칙(`korean.py`)으로 자막을 알아서
쪼갰다. 문법적으로는 맞게 끊지만, **말맛과 강조는 사람이 정하는 것**이라
대표님이 캡컷에서 다시 손보셔야 했다. "네가 마음대로 문장들을 쪼개서
편집하기 힘들다"는 게 그 얘기다.

그래서 촬영 대본을 **한 줄 = 자막 한 개**로 적어 두면, 그 줄바꿈을 그대로 쓴다.
프로그램은 "언제 뜨고 언제 사라지는가"만 계산한다.

## 어떻게 시각을 찾는가

장표를 거는 방식과 똑같다 — **대본 전문과 촬영본 전문을 통째로 정렬**한다.
원고를 거의 그대로 읽으시기 때문에 이 정렬은 99~100% 붙는다(실측).
줄마다 첫 글자·끝 글자가 대본 어디에 붙었는지 보고, 그 낱말의 시각을 가져온다.

    파일 한 줄  →  대본에서 그 대목  →  낱말 타임스탬프  →  타임라인 좌표

## 파일 형식

    회원은 블로그에서 발견하고, 인스타에서 검증하고, 리뷰에서 결정 합니다
                                    ← 빈 줄은 문단 구분. 자막에는 영향 없다
    그 첫 칸 얘기부터 시작할 텐데요.
    시작하기 전에 하나 뒤집고 갈게요.

`**굵게**` 표시는 지금은 글자만 걷어낸다 (강조 자막 색은 따로 정한다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

US = 1_000_000
_KEEP = re.compile(r"[^가-힣0-9a-zA-Z]")


def norm(s: str) -> str:
    return _KEEP.sub("", s)


# ── 풀링·키 촬영대본 찾기 ─────────────────────────────────────────
#
# 2026-09-16 부터 풀링·키 촬영대본은 **한 줄 = 자막 하나**로 쓴다
# (tools/make_subtitle_script.py). 강의처럼 덱에 묶여 있지 않으니, 받아쓰기와
# 글이 가장 많이 겹치는 대본을 찾아서 쓴다.
SCRIPT_ROOTS = (
    Path(r"G:\내 드라이브\BPT\90_콘텐츠_풀링키"),
    Path(__file__).parent.parent / "대본",
)
FIND_MIN_RATIO = 0.55
SUBTITLE_LINE_SHARE = 0.8     # 이만큼의 줄이 한 줄 한도 안이어야 "자막 줄 대본" 으로 본다


def find_script(tr, roots=SCRIPT_ROOTS):
    """받아쓰기와 가장 닮은 촬영대본 → (경로, 닮은 정도, 자막 줄 형식인가)."""
    asr = norm("".join(s.text for s in tr.segs))[:1500]
    if len(asr) < 100:
        return None
    best = None
    for root in roots:
        try:
            if not root.exists():
                continue
            cands = list(root.glob("*_촬영대본.txt")) + list(root.glob("*/*_촬영대본.txt"))
        except OSError:
            continue
        for p in cands:
            try:
                txt = norm(p.read_text(encoding="utf-8-sig"))[:1800]
            except OSError:
                continue
            sm = SequenceMatcher(None, asr, txt, autojunk=False)
            if sm.quick_ratio() < FIND_MIN_RATIO:
                continue
            r = sm.ratio()
            if best is None or r > best[1]:
                best = (p, r)
    if best is None or best[1] < FIND_MIN_RATIO:
        return None
    lines = load_lines(best[0])
    share = sum(1 for ln in lines if len(ln) <= SCRIPT_MAX_CHARS) / max(1, len(lines))
    return best[0], best[1], share >= SUBTITLE_LINE_SHARE and _looks_split(lines)


def _looks_split(lines: list[str]) -> bool:
    """문장 줄(45자 안팎) 대본이 아니라 자막 줄로 나눈 대본인가 — 줄 길이 중앙값으로 본다."""
    if not lines:
        return False
    ls = sorted(len(x) for x in lines)
    return ls[len(ls) // 2] <= 22


def load_lines(path: str | Path) -> list[str]:
    """자막 대본 → 줄 목록. 빈 줄과 표시 기호는 걷어낸다."""
    text = Path(path).read_text(encoding="utf-8-sig")
    out: list[str] = []
    for raw in text.splitlines():
        t = raw.strip().replace("**", "").strip()
        if not t:
            continue
        if norm(t):
            out.append(t)
    return out


@dataclass
class Timed:
    """자막 한 줄과 그 원본 시각."""
    text: str
    start: int          # 원본(촬영본) 기준
    end: int
    ratio: float        # 그 줄이 대본과 얼마나 붙었나


def _asr_index(tr):
    """정규화 대본 전문 + 글자마다의 (시작, 끝) 시각."""
    chars: list[str] = []
    times: list[tuple[int, int]] = []
    for seg in tr.segs:
        words = getattr(seg, "words", None) or []
        if words:
            for w in words:
                n = norm(w.text)
                if not n:
                    continue
                chars.append(n)
                times.extend([(w.start, w.end)] * len(n))
        else:
            n = norm(seg.text)
            if not n:
                continue
            chars.append(n)
            dur = seg.end - seg.start
            step = dur / max(1, len(n))
            for k in range(len(n)):
                times.append((int(seg.start + step * k),
                              int(seg.start + step * (k + 1))))
    return "".join(chars), times


def align(lines: list[str], tr) -> list[Timed]:
    """대본 줄 → 원본 시각. 전문 대 전문으로 한 번에 맞춘다."""
    spans: list[tuple[int, int]] = []
    acc, parts = 0, []
    for ln in lines:
        n = norm(ln)
        spans.append((acc, acc + len(n)))
        acc += len(n)
        parts.append(n)
    script = "".join(parts)

    asr_text, times = _asr_index(tr)
    sm = SequenceMatcher(None, script, asr_text, autojunk=False)
    cmap: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            cmap[a + k] = b + k

    out: list[Timed] = []
    for ln, (s0, s1) in zip(lines, spans):
        hit = [cmap[k] for k in range(s0, s1) if k in cmap]
        if not hit:
            out.append(Timed(ln, -1, -1, 0.0))
            continue
        lo, hi = min(hit), max(hit)
        out.append(Timed(ln, times[lo][0], times[hi][1],
                         len(hit) / max(1, s1 - s0)))
    return out


# 대본 줄과 실제 발화가 이만큼 다르면 "말이 바뀌었다"고 본다.
#
# 대표님도 "촬영하면서 말하는 게 약간 달라지기도 한다"고 하셨다. 그때는
# **실제로 하신 말**을 자막으로 내야 한다 — 자막은 영상의 기록이지
# 대본의 복사본이 아니다. 다만 조금 다른 정도(어미·조사)면 대본 쪽이
# 맞춤법·띄어쓰기가 깔끔하므로 그대로 둔다.
DEVIATED = 0.82

# 살아있는 말인데 대본 어느 줄에도 안 잡힌 구간이 이만큼이면 자막을 채운다.
# (촬영하며 덧붙이신 말 — 이것도 영상에 있으니 자막이 있어야 한다)
UNCOVERED_MIN_US = 700_000
MIN_PIECE_US = 250_000       # 이보다 짧게 떴다 사라지는 자막은 버린다

# 자막 경계를 **컷 경계에 붙이는** 허용 오차.
#
# 컷은 말 앞뒤로 pad(0.08초) 여유를 두고 자르는데 자막은 말이 끝나는 순간 끝나서,
# 자막이 컷을 80ms 씩 넘나들었다 — 2026-09-23 촬영본에서 270줄 중 133줄(49%)이
# 컷을 가로질렀다. 대표님 완성본 4편은 1~5% 뿐이고, 자막 시작의 62~65% 가 컷과
# 정확히 같은 자리다. 이 오차 안이면 컷 자리로 당겨 붙인다.
SNAP_US = 300_000

# 자막 한 줄 **안쪽**에 든 짧은 쉼은 자르지 않는다.
#
# 0.13초짜리 쉼을 자르면 그 줄이 컷을 가로질러, 화면은 튀는데 자막은 그대로 남는다.
# 대표님이 지적하신 "자막 길이와 컷이 안 맞는다"의 나머지 절반이 이것이었다.
# 길게(1초 이상) 쉬고 이어 말한 자리는 그대로 자른다 — 대표님 완성본도 그렇다.
PROTECT_GAP_US = 400_000

# 대본 한 줄이 이보다 길면 그때만 쪼갠다.
#
# 자동 생성 자막의 한도(23자)를 그대로 쓰면 대표님이 적어 둔 165줄 중 49줄이
# 갈라진다 — "내가 끊어 놓은 대로" 라는 요청과 어긋난다.
# 대표님이 **실제로 화면에 올리신 최대치**로 잡았다:
#   파트0 1편 26자 · 2편 29자 · 오리엔테이션 29자
# 이 값이면 갈라지는 줄이 49 → 13개로 준다.
SCRIPT_MAX_CHARS = 29


def _word_bounds(times, lo: int, hi: int) -> tuple[int, int]:
    """맞은 구간을 **낱말 경계까지** 넓힌다.

    맞은 글자에서 딱 끊으면 낱말이 잘린다 — "2차 만족이 회원"(입니다가 잘림),
    "…어떻게 생겼냐면 이"(이렇습니다가 잘림). 같은 타임스탬프를 가진 글자는
    한 낱말이므로, 그 낱말이 끝날 때까지 밀어 준다.
    """
    n = len(times)
    while lo > 0 and times[lo - 1] == times[lo]:
        lo -= 1
    while hi + 1 < n and times[hi + 1] == times[hi]:
        hi += 1
    return lo, hi


def _spoken(asr_text: str, times, lo: int, hi: int) -> str:
    """그 구간에서 **실제로 하신 말**을 낱말 단위로 되살린다."""
    lo, hi = _word_bounds(times, lo, min(hi, len(times) - 1))
    words, cur, prev = [], "", None
    for k in range(lo, min(hi + 1, len(times))):
        if prev is not None and times[k] != prev:
            words.append(cur)
            cur = ""
        cur += asr_text[k]
        prev = times[k]
    if cur:
        words.append(cur)
    return " ".join(w for w in words if w)


def build_pieces(lines: list[str], tr, keeps, speed: float,
                 max_chars: int = SCRIPT_MAX_CHARS, min_ratio: float = 0.5,
                 video=None, protect_gaps: bool = True):
    """자막 대본 → 타임라인 좌표의 SubtitlePiece 목록.

    셋을 같이 한다.

    1. **줄바꿈은 대표님이 정한 그대로** 쓴다 (다시 쪼개지 않는다)
    2. 촬영하며 말이 달라진 줄은 **실제로 하신 말**로 바꾼다
    3. 대본에 없는데 말씀하신 대목은 **자막을 채워 넣는다**

    그래야 자막이 대본이 아니라 **영상의 기록**이 된다.
    한 줄이 너무 길면 그때만 어절 경계에서 잘라 다음 자막으로 넘긴다.
    """
    from .cuts import SubtitlePiece, _split_one_line

    asr_text, times = _asr_index(tr)

    spans, acc, parts = [], 0, []
    for ln in lines:
        n = norm(ln)
        spans.append((acc, acc + len(n)))
        acc += len(n)
        parts.append(n)
    script = "".join(parts)

    sm = SequenceMatcher(None, script, asr_text, autojunk=False)
    cmap: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            cmap[a + k] = b + k

    offs, acc2 = [], 0
    for k in keeps:
        offs.append(acc2)
        acc2 += k.dur
    # ⚠ 여기서 배속으로 나누면 안 된다. build_draft 가 자막을 얹을 때 다시 speed 로
    # 나누므로 두 번 나뉘어 자막이 통째로 1/배속 지점까지만 깔린다 —
    # 2026-09-23 실측: 7분 06초 영상인데 자막이 5분 53초(83%)에서 끝나 있었고,
    # 뒤로 갈수록 말과 최대 2초까지 어긋났다. map_subtitles 와 같은 **배속 전** 좌표로 둔다.
    total = acc2

    def kept_span(a: int, b: int):
        best = None
        for k, off in zip(keeps, offs):
            lo, hi = max(a, k.start), min(b, k.end)
            if hi <= lo:
                continue
            if best is None or (hi - lo) > (best[1] - best[0]):
                best = (off + (lo - k.start), off + (hi - k.start))
        if best is None:
            return None
        # 시작은 **첫 낱말이 실제로 나오는 자리**로 당긴다.
        #
        # 위에서 고른 건 "가장 길게 겹친 클립" 이라, 한 줄이 컷 두 개에 걸치고
        # 첫 낱말이 앞쪽 짧은 클립에 있으면 자막이 그 낱말보다 늦게 떴다
        # (2026-09-16 대표님 "말하는 싱크에 안 맞는다" 지적을 파다 찾음).
        first = map_time(a)
        if first is not None and first < best[0]:
            best = (first, best[1])
        return best

    def map_time(t: int):
        """원본 시각 → 타임라인. 잘린 자리면 바로 뒤 클립 시작으로."""
        for k, off in zip(keeps, offs):
            if k.start <= t < k.end:
                return off + (t - k.start)
            if t < k.start:
                return off
        return None

    pieces, dropped, changed = [], [], []
    used: list[tuple[int, int]] = []          # 대본이 덮은 대본-글자 구간

    # ── 1차: 대본 글자를 받아쓰기 글자에 대어 줄마다 대략 시각을 잡는다 ──
    est: list[tuple[int, int, str, int]] = []   # (시작, 끝, 글, 줄 번호)
    for i, ln in enumerate(lines):
        s0, s1 = spans[i]
        hit = [cmap[k] for k in range(s0, s1) if k in cmap]
        ratio = len(hit) / max(1, s1 - s0)
        if not hit or ratio < min_ratio:
            dropped.append(ln)
            continue
        lo, hi = min(hit), max(hit)
        used.append((lo, hi))

        text = ln
        if ratio < DEVIATED:                  # 말이 달라졌다 → 하신 말로
            said = _spoken(asr_text, times, lo, hi).strip()
            if len(norm(said)) >= 2:
                changed.append((ln, said))
                text = said
        est.append((times[lo][0], max(times[lo][0] + 1, times[hi][1]), text, i))

    # ── 2차: 그 글자들을 **소리에 직접 정렬**해 시각을 다시 잡는다 ──
    #
    # 1차는 대본 글자와 받아쓰기 글자를 짝지어 시각을 옮기는 것이라, 말이 대본과
    # 달라진 대목에서 통째로 밀린다. 2026-09-23 촬영본(대본 닮음 87%)에서 273줄 중
    # 141줄이 0.3초 넘게 어긋났고, 받아쓰기로 직접 끊은 자막은 90% 가 0.11초 안이었다.
    if video is not None:
        try:
            from . import forced_align
            fixed = forced_align.refine_spans(video, [(a, b, t) for a, b, t, _ in est])
            est = [(f[0], f[1], t, i) if f else (a, b, t, i)
                   for (a, b, t, i), f in zip(est, fixed)]
        except Exception:                      # noqa: BLE001  모델이 없으면 1차 그대로
            pass

    # ── 자막 줄 안쪽의 짧은 쉼은 되살린다 (컷이 줄을 가르지 않게) ──
    new_keeps = None
    if protect_gaps and est:
        from .silence import Span
        drop_at = set()
        for a0, b0, _, _ in est:
            for k1, k2 in zip(keeps, keeps[1:]):
                gap = k2.start - k1.end
                if 0 < gap <= PROTECT_GAP_US and a0 < k1.end and k2.start < b0:
                    drop_at.add(k1.end)
        if drop_at:
            merged = [Span(keeps[0].start, keeps[0].end)]
            for k in keeps[1:]:
                if merged[-1].end in drop_at:
                    merged[-1].end = k.end
                else:
                    merged.append(Span(k.start, k.end))
            new_keeps = merged
            keeps = merged
            offs, acc2 = [], 0
            for k in keeps:
                offs.append(acc2)
                acc2 += k.dur
            total = acc2

    speech_end: dict[int, int] = {}            # 조각 id → 말이 끝난 타임라인 좌표
    for a0, b0, text, i in est:
        span = kept_span(a0, max(a0 + 1, b0))
        if span is None:
            dropped.append(lines[i])
            continue
        a, b = span
        pc = SubtitlePiece(start=a, dur=max(1, b - a), text=text,
                           words=[], seg_id=i, keep_idx=-1)
        speech_end[id(pc)] = b
        pieces.append(pc)

    # ── 대본에 없는데 말씀하신 대목 채우기 ──
    used.sort()
    holes, cur = [], 0
    for lo, hi in used:
        if lo - cur > 0:
            holes.append((cur, lo))
        cur = max(cur, hi + 1)
    if cur < len(asr_text):
        holes.append((cur, len(asr_text)))

    for lo, hi in holes:
        if hi - lo < 4 or lo >= len(times):
            continue
        hi = min(hi, len(times))
        t0, t1 = times[lo][0], times[hi - 1][1]
        if t1 - t0 < UNCOVERED_MIN_US:
            continue
        span = kept_span(t0, max(t0 + 1, t1))
        if span is None:
            continue
        said = _spoken(asr_text, times, lo, hi - 1).strip()
        if len(norm(said)) < 4:
            continue
        a, b = span
        pieces.append(SubtitlePiece(start=a, dur=max(1, b - a), text=said,
                                    words=[], seg_id=10_000 + lo, keep_idx=-1))

    pieces.sort(key=lambda p: (p.start, p.seg_id))

    out: list[SubtitlePiece] = []
    for p in pieces:
        if out and p.start <= out[-1].start:
            p.start = out[-1].start + 1
        out.append(p)

    for i, p in enumerate(out):
        nxt = out[i + 1].start if i + 1 < len(out) else total
        p.dur = max(1, nxt - p.start)

    # ── 자막 경계를 컷 경계에 붙인다 ──
    #
    # 두 가지를 한다.
    #  (1) 컷이 SNAP_US 안쪽이면 자막 시작을 그 컷 자리로 당긴다 (pad 0.08초 어긋남 해소)
    #  (2) 앞 줄이 **말을 끝낸 뒤** 다음 줄이 시작하기 전(=쉼)에 컷이 있으면, 다음 줄을
    #      그 컷 자리에서 띄운다. 자막은 다음 줄이 뜰 때까지 떠 있으므로, 이렇게 해야
    #      "자막이 컷을 가로지르는" 일이 없다. 말이 있는 구간으로는 절대 넘어가지 않는다.
    bounds = sorted(set(offs + [acc2]))
    for i, p in enumerate(out):
        lo = out[i - 1].start if i else -1
        prev_speech = speech_end.get(id(out[i - 1])) if i else None
        moved = False
        if prev_speech is not None and prev_speech < p.start:
            inside = [b for b in bounds if prev_speech < b < p.start]
            if inside:
                p.start = inside[0]
                moved = True
        if not moved:
            near = min(bounds, key=lambda b: abs(b - p.start))
            if abs(near - p.start) <= SNAP_US and near > lo:
                p.start = near
    out.sort(key=lambda p: p.start)
    for i, p in enumerate(out):
        nxt = out[i + 1].start if i + 1 < len(out) else total
        p.dur = max(1, nxt - p.start)

    # ── 스쳐 지나가는 줄은 버린다 ──
    # 같은 대목을 대본 줄과 "대본에 없는 말 채우기"가 둘 다 잡으면 앞 줄이 몇 ms 만
    # 떴다 사라진다 (2026-09-23 실측 2줄: 3ms · 8ms). 눈에는 깜빡임으로만 보인다.
    out = [p for p in out if p.dur >= MIN_PIECE_US] or out
    for i, p in enumerate(out):
        nxt = out[i + 1].start if i + 1 < len(out) else total
        p.dur = max(1, nxt - p.start)

    # ── 한 줄에 안 들어가는 것만 쪼갠다 ──
    final: list[SubtitlePiece] = []
    for p in out:
        final.extend(_split_one_line(p, max_chars) if len(p.text) > max_chars
                     else [p])
    for i, p in enumerate(final):
        nxt = final[i + 1].start if i + 1 < len(final) else total
        p.dur = max(1, nxt - p.start)

    # ── 마지막으로 한 번 더 컷에 붙인다 ──
    # 줄을 쪼개거나 짧은 줄을 버리면서 경계가 다시 어긋날 수 있다. 자막 끝자락에
    # 컷이 걸려 있으면(= 그 말을 마친 뒤의 쉼) 다음 자막을 그 컷에서 띄운다.
    # 0.6초 안쪽만 당긴다 — 더 당기면 자막이 말보다 너무 일찍 뜬다.
    for i in range(len(final) - 1):
        a, b = final[i], final[i + 1]
        inside = [x for x in bounds if a.start + a.dur // 2 < x < b.start
                  and b.start - x <= 600_000]
        if inside and inside[0] > a.start + MIN_PIECE_US:
            b.start = inside[0]
            a.dur = max(1, b.start - a.start)
    for i, p in enumerate(final):
        nxt = final[i + 1].start if i + 1 < len(final) else total
        p.dur = max(1, nxt - p.start)
    return final, dropped, changed, new_keeps
