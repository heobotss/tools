"""무음컷 + 잔말/NG 컷을 하나의 타임라인으로 통합하고, 자막을 재매핑한다.

컷을 하면 원본시간(source) 과 타임라인시간(timeline) 이 어긋난다.
자막은 원본시간에 붙어 있으므로 반드시 다시 매핑해야 한다. 안 하면
자막이 화면과 따로 논다 — 이게 자동편집에서 제일 티 나는 실패다.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import korean
from .silence import Span
from .style import apply_plan, pick_emphasis
from .transcript import Seg, Transcript, Word

US = 1_000_000

# 한 음절이 이보다 오래 걸리면 낱말 안에 뭔가 더 들어 있다.
# 이 영상 실측: 중앙 0.16s · 90% 0.34s · 97% 0.52s (음절당).
_HANGUL = __import__("re").compile(r"[가-힣]")
MAX_SEC_PER_SYLLABLE = 0.45


# ── 구간 연산 ────────────────────────────────────────────────────
def subtract(spans: list[Span], holes: list[Span]) -> list[Span]:
    """spans 에서 holes 를 파낸다."""
    out = [Span(s.start, s.end) for s in spans]
    for h in sorted(holes, key=lambda x: x.start):
        nxt: list[Span] = []
        for s in out:
            if h.end <= s.start or h.start >= s.end:
                nxt.append(s)
                continue
            if s.start < h.start:
                nxt.append(Span(s.start, h.start))
            if h.end < s.end:
                nxt.append(Span(h.end, s.end))
        out = nxt
    return out


def intersect(a: Span, b: Span) -> Span | None:
    s, e = max(a.start, b.start), min(a.end, b.end)
    return Span(s, e) if e > s else None


@dataclass
class KeepPlan:
    keeps: list[Span]
    silence_removed_us: int
    speech_removed_us: int

    @property
    def total_us(self) -> int:
        return sum(k.dur for k in self.keeps)


def protect_words(keeps: list[Span], tr: Transcript,
                  margin_s: float = 0.05,
                  silences: list[Span] | None = None) -> list[Span]:
    """말이 있는 자리는 무슨 일이 있어도 살린다.

    세기만 보면 작게 말한 낱말이 무음으로 잘린다. 실제로 "그래서 시간 내서…"
    의 '그래서'(-36dB)가 통째로 날아가 문장이 "시간 내서…" 로 시작한 적이 있다.
    접속부사·조사처럼 약하게 발음되는 말일수록 잘 잘리는데, 하필 그런 말이
    빠지면 앞뒤 맥락이 어긋난다.

    임계값을 낮추면 이번엔 정적이 안 잘린다 (룸톤도 -36dB 였다).
    세기로는 못 가른다 — **대본이 낱말 위치를 알고 있으니 그걸 믿는다.**
    """
    m = int(margin_s * US)

    sil = sorted(silences or [], key=lambda s: s.start)

    def voiced_parts(w) -> list[Span]:
        """낱말 구간에서 **소리가 있는** 부분만."""
        return subtract([Span(w.start, w.end)], sil) if sil else [Span(w.start, w.end)]

    def kept_ratio(w) -> float:
        """소리 있는 부분이 얼마나 살아남았는가.

        시간 기준으로 재면 안 된다. whisper 가 낱말 뒤에 쉼을 붙여놓은 탓에
        "60% 남았다" 고 나오는데 정작 **소리는 잘린 40% 안에** 있는 일이 생긴다.
        (실제로 '세미나' 가 이렇게 보호 대상에서 빠졌다)
        """
        parts = voiced_parts(w)
        total = sum(p.dur for p in parts)
        if total <= 0:
            return 1.0                       # 원래 소리가 없던 구간
        got = sum(max(0, min(p.end, k.end) - max(p.start, k.start))
                  for p in parts for k in keeps)
        return got / total

    words: list[Span] = []
    for s in tr.segs:
        if s.dropped:
            continue          # 일부러 버리는 세그먼트는 지켜주지 않는다
        for w in s.words:
            if any(a <= w.start < b for a, b in list(s.holes) + list(s.media_holes)):
                continue      # 가장자리 잔말·재테이크는 지키지 않는다
            # **잘릴 위기의 낱말만** 살린다.
            #
            # 모든 낱말을 통째로 보호하면 정적까지 딸려 들어온다. whisper 는
            # 낱말 끝시각을 다음 낱말 시작까지 늘려 잡는 버릇이 있어서,
            # 낱말 구간 = 발화 구간 + 뒤따르는 쉼 이 되기 때문이다.
            # (전부 보호했더니 결과가 6분10초에서 6분36초로 늘었다)
            if kept_ratio(w) >= 0.5:
                continue      # 이미 대부분 살아 있으면 손대지 않는다
            # 낱말 길이가 말이 안 되게 길면, 그 안에 whisper 가 지워버린
            # 것이 들어 있다 — 쉼이거나 **다시 말한 앞 시도**다.
            #
            # 실제 사례: "아무리 좋은… (1.3초 멈춤) …아무리 좋은 물을 부어도"
            # 를 whisper 가 "…물을 부어도" 로 정리하면서 '물을' 하나에
            # 2.24초(음절당 1.12초, 중앙값의 7배)를 몰아줬다. 그 구간을
            # 통째로 보호하는 바람에 실패한 시도가 그대로 살아남았다.
            #
            # 다시 말한 쪽은 **뒤**에 있으므로 끝에서부터 그럴듯한 길이만 지킨다.
            span = w.end - w.start
            syl = max(1, len(_HANGUL.findall(w.text)))
            plausible = int(syl * MAX_SEC_PER_SYLLABLE * US)
            if span > plausible:
                words.append(Span(max(0, w.end - plausible - m), w.end))
                continue
            # 뒤쪽만 깎는다. whisper 는 낱말 **시작**은 제법 정확한데
            # **끝**을 다음 낱말 시작까지 늘려 잡아서, 뒤에 쉼이 딸려온다.
            # 앞까지 깎으면 낱말 첫머리가 잘려 소리가 뭉개진다.
            tail = span // 5
            words.append(Span(max(0, w.start - m), max(w.start + 1, w.end - tail)))
    if not words:
        return keeps

    # 되살릴 구간에서 **무음은 빼고** 되살린다.
    #
    # whisper 는 낱말 끝을 다음 낱말 시작까지 늘려 잡는다. 그래서 '지금'
    # 한 낱말이 1.14초로 잡히고, 실제 발화는 0.3초뿐이며 나머지 0.86초가
    # 무음인 일이 생긴다. 낱말 구간을 통째로 되살리면 그 무음까지 딸려온다.
    # (사용자 편집본과 대조하니 이렇게 남은 무음이 13.8초였다)
    #
    # 앞뒤 어느 쪽에 소리가 있는지 추측할 필요가 없다 — 무음을 빼면 된다.
    if silences:
        kept_spans = subtract(words, list(silences))
        # 무음을 빼다가 보호가 통째로 사라지는 낱말이 생긴다. 무음 검출은
        # 0.28초 이상 이어진 구간을 통으로 잡기 때문에, 그 안에 짧게 들어
        # 있는 소리까지 같이 먹는다. (실제로 '세미나' 가 이렇게 날아갔다)
        # 그런 낱말은 가운데 절반이라도 남긴다.
        for w in words:
            if any(k.start < w.end and w.start < k.end for k in kept_spans):
                continue
            mid = (w.start + w.end) // 2
            half = max(1, (w.end - w.start) // 4)
            kept_spans.append(Span(mid - half, mid + half))
        words = kept_spans

    merged = keeps + words
    merged.sort(key=lambda x: x.start)
    out: list[Span] = []
    for s in merged:
        if out and s.start <= out[-1].end:
            out[-1].end = max(out[-1].end, s.end)
        else:
            out.append(Span(s.start, s.end))
    return out


def trim_edges(keeps: list[Span], tr: Transcript,
               pre_roll_s: float = 0.12, tail_s: float = 0.25) -> list[Span]:
    """말이 시작되기 전과 끝난 뒤를 잘라낸다.

    영상 앞뒤에는 카메라를 켜고 자세를 잡는 시간이 늘 붙어 있다. 세기로만
    보면 룸톤이 임계값 언저리라 안 걸리는 경우가 많다. 첫 낱말·끝 낱말이
    어디인지는 대본이 알고 있으니 거기에 맞춘다.
    """
    # 구멍(헛시작·가장자리 잔말)에 든 낱말은 첫 낱말로 세면 안 된다.
    # 안 그러면 헛시작을 지워놓고도 영상은 여전히 거기서부터 시작한다.
    live = [w for s in tr.segs if not s.dropped for w in s.words
            if not any(a <= w.start < b for a, b in s.holes)]
    if not live or not keeps:
        return keeps
    first = min(w.start for w in live) - int(pre_roll_s * US)
    last = max(w.end for w in live) + int(tail_s * US)
    out = []
    for k in keeps:
        a, b = max(k.start, first), min(k.end, last)
        if b > a:
            out.append(Span(a, b))
    return out


SLIVER_MAX_S = 1.0        # 이보다 긴 조각은 무조건 살린다
SLIVER_WORD_COVER = 0.6   # 낱말을 이만큼 담고 있으면 "온전한 말" 로 본다
SLIVER_FLOOR_S = 0.45     # 이보다 짧으면 무슨 낱말이 들었든 버린다 (소스 기준)
#
# 하한선을 따로 두는 이유: whisper 가 낱말 길이 자체를 짧게 잡으면 꼬리 조각도
# "낱말을 60% 담았다" 로 통과해버린다. 실측에서 '분들을' 0.12초, '그런지랑'
# 0.14초가 이렇게 살아남았는데, 3~4음절을 그 시간에 말하는 건 불가능하다.
# 소스 0.45초는 1.2배속에서 화면 0.375초 — 열 프레임 남짓 스치는 정도라,
# 진짜 말이 들었더라도 그렇게 끊어 붙이면 편집이 튄다.
# 대표님 손질본의 최소 클립은 화면 0.97초로, 여기서 한참 위다.


def drop_slivers(keeps: list[Span], tr: Transcript,
                 max_s: float = SLIVER_MAX_S,
                 cover: float = SLIVER_WORD_COVER) -> list[Span]:
    """컷하고 남은 **파편**을 걷어낸다.

    protect_words 는 잘릴 위기의 낱말을 길이 제한 없이 되살린다. 짧아도 진짜
    말이면 살려야 하니 맞는 판단인데, 되살아난 게 낱말 **전체**가 아니라 잘리고
    남은 꼬리일 때가 있다. 그러면 0.12~0.3초짜리 클립이 타임라인에 남는다.

    실측(2026-08-18): 1초 미만 클립 19개가 남았고 대표님은 그중 **16개를**
    직접 지우셨다. 대표님 손질본의 최소 클립은 0.96초다. 남기신 2개는
    "하나만 먼저요."(0.82초) 처럼 뜻이 온전한 말이었다.

    그래서 길이로 자르지 않고 **낱말을 온전히 담고 있는지**로 가른다.
    '그런지랑' 의 꼬리 0.14초는 버리고, 0.82초짜리 온전한 문장은 지킨다.
    """
    live = [(sg, w) for sg in tr.segs if not sg.dropped for w in sg.words
            if not any(a <= w.start < b
                       for a, b in list(sg.holes) + list(sg.media_holes))]
    limit = int(max_s * US)
    out: list[Span] = []
    floor = int(SLIVER_FLOOR_S * US)
    for k in keeps:
        if k.dur >= limit:
            out.append(k)
            continue
        if k.dur < floor:
            continue
        whole = False
        for _, w in live:
            span = w.end - w.start
            if span <= 0:
                continue
            ov = min(w.end, k.end) - max(w.start, k.start)
            if ov > 0 and ov / span >= cover:
                whole = True
                break
        if whole:
            out.append(k)
    return out


def build_keeps(
    silence_keeps: list[Span],
    tr: Transcript | None,
    total_us: int,
    min_keep_s: float = 0.30,
    guard_s: float = 0.04,
    silences: list[Span] | None = None,
) -> KeepPlan:
    """무음컷 결과에서 잔말/NG 세그먼트를 추가로 파낸다.

    guard_s: 잘라낼 구간을 아주 살짝 넓혀서 앞뒤 말꼬리가 남지 않게 한다.
    """
    silence_removed = total_us - sum(k.dur for k in silence_keeps)

    if tr is None:
        return KeepPlan(silence_keeps, silence_removed, 0)


    guard = int(guard_s * US)
    holes: list[Span] = []
    for s in tr.segs:
        if s.dropped:
            # 통째로 버리는 세그먼트
            holes.append(Span(max(0, s.start - guard), min(total_us, s.end + guard)))
        else:
            # 살리되 가장자리 잔말만 떼어내는 경우.
            # 여기엔 guard 를 적용하지 않는다 — 붙어 있는 실제 발화를 갉아먹는다.
            for a, b in list(s.holes) + list(s.media_holes):
                if b > a:
                    holes.append(Span(max(0, a), min(total_us, b)))
    if holes:
        cut = subtract(silence_keeps, holes)
        min_keep = int(min_keep_s * US)
        cut = [s for s in cut if s.dur >= min_keep]
    else:
        # 잘라낼 세그먼트가 없어도 낱말 보호와 앞뒤 정리는 반드시 거쳐야 한다.
        # 여기서 일찍 돌려보내는 바람에 보호가 통째로 건너뛰어진 적이 있다.
        cut = list(silence_keeps)

    # 낱말 되살리기는 **min_keep 필터 뒤에** 해야 한다.
    # 앞에서 하면 문장 첫 낱말처럼 홀로 떨어진 짧은 조각이 "너무 짧다"고
    # 다시 지워진다. 되살린 낱말에는 길이 제한을 적용하지 않는다 —
    # 짧아도 진짜 말이니까.
    cut = trim_edges(protect_words(cut, tr, silences=silences), tr)
    # 되살리기·다듬기가 끝난 **뒤에** 파편을 걷는다. 앞에서 하면 아직 파편이
    # 안 생긴 상태라 잡을 게 없다.
    cut = drop_slivers(cut, tr)

    # 잘라낸 '말' 의 양. 낱말 보호로 되살린 만큼은 빼지 않는다 —
    # 그대로 두면 보호가 많을 때 음수가 나온다 (실제로 -1:49 가 찍혔다).
    speech_removed = max(0, sum(b - a for a, b in
                                ((h.start, h.end) for h in holes)))

    return KeepPlan(
        keeps=cut,
        silence_removed_us=silence_removed,
        speech_removed_us=speech_removed,
    )


# ── 자막 재매핑 ──────────────────────────────────────────────────
@dataclass
class SubtitlePiece:
    """타임라인 좌표계의 자막 한 조각. 항상 한 줄에 들어가는 길이다."""
    start: int          # 타임라인 위 시작
    dur: int
    text: str
    words: list[Word]   # 타임라인 좌표로 변환된 단어들 (상대시간 아님)
    kind: str = "normal"   # normal | neg | pos — 어떤 자막 스타일로 낼지
    seg_id: int = 0        # 어느 문장에서 나왔는지. 문장을 넘어 합치면 안 된다.
    keep_idx: int = -1     # 몇 번째 클립에 속하는지. 클립을 넘어 걸치면 안 된다.

    @property
    def end(self) -> int:
        return self.start + self.dur


def _chunk_words(items: list[str], max_chars: int,
                 gaps_us: list[int] | None = None) -> list[list[int]]:
    """어절을 한 줄짜리 묶음들로 나눈다.

    글자수만 맞추면 "영상 자동 / 편집에" 처럼 한 덩어리를 쪼갠다.
    끊어읽기 판단은 korean.chunk 가 한다 (조사·어미·의존명사·쉼).
    """
    return korean.chunk(items, max_chars, gaps_us)


def _split_one_line(p: SubtitlePiece, max_chars: int) -> list[SubtitlePiece]:
    """한 줄을 넘는 자막을 여러 조각으로 쪼갠다.

    줄바꿈(\\n)으로 두 줄을 만들지 않는다. 넘치는 만큼은 **다음 자막**이 된다.
    끊는 위치는 어절 경계 — 낱글자로 쪼개면 읽을 수 없다.
    """
    text = " ".join(p.text.split())
    p.text = text
    if len(text) <= max_chars:
        return [p]

    use_words = bool(p.words) and len(p.words) > 1
    items = [w.text for w in p.words] if use_words else text.split(" ")
    # 어절 사이 쉼 — 화자가 실제로 끊어 읽은 자리를 끊는 판단에 쓴다
    gaps = ([max(0, p.words[i + 1].start - p.words[i].end)
             for i in range(len(p.words) - 1)] + [0]) if use_words else None
    groups = _chunk_words(items, max_chars, gaps)
    if len(groups) <= 1:
        return [p]

    # 조각 시작 시각을 정한다.
    starts: list[int] = []
    if use_words:
        starts = [p.words[g[0]].start for g in groups]
        starts[0] = p.start
    # 단어 타임스탬프가 없거나 시작이 단조증가하지 않으면(클램프 때문에
    # 여러 단어가 같은 지점에 몰릴 수 있다) 글자수 비례로 균등 배분한다.
    if not use_words or any(starts[i] >= starts[i + 1] for i in range(len(starts) - 1)):
        lens = [len(" ".join(items[i] for i in g)) for g in groups]
        tot = sum(lens) or 1
        starts, acc = [], p.start
        for ln in lens:
            starts.append(acc)
            acc += max(1, (p.dur * ln) // tot)

    out: list[SubtitlePiece] = []
    for gi, g in enumerate(groups):
        s = starts[gi]
        e = starts[gi + 1] if gi + 1 < len(groups) else p.end
        out.append(SubtitlePiece(
            start=s, dur=max(1, e - s),
            text=" ".join(items[i] for i in g),
            words=[p.words[i] for i in g] if use_words else [],
            kind=p.kind, seg_id=p.seg_id, keep_idx=p.keep_idx,
        ))
    return out


def _repair_breaks(pieces: list[SubtitlePiece], max_chars: int) -> list[SubtitlePiece]:
    """컷 경계 때문에 어색하게 갈라진 자막을 다시 붙인다.

    자막은 두 번 갈라진다 — 끊어읽기(korean.chunk)로 한 번, **컷 경계**로 또
    한 번. 뒤쪽은 언어 규칙과 무관하게 갈라지므로 "요즘" 한 낱말만 남거나
    "…수 / 있습니다" 처럼 서술어가 두 동강 나기도 한다.

    붙여도 한 줄에 들어가고, 갈라두면 확실히 나쁜 자리만 되돌린다.
    """
    if len(pieces) < 2:
        return pieces
    out = [pieces[0]]
    for p in pieces[1:]:
        last = out[-1]
        wa, wb = last.text.split(), p.text.split()
        if (wa and wb and last.kind == p.kind and last.seg_id == p.seg_id
                and last.keep_idx == p.keep_idx):
            joined = len(last.text) + 1 + len(p.text)
            sc = korean.break_score(wa[-1], wb[0])
            # 한 낱말짜리 토막은 조금 넉넉히 봐준다 — 혼자 떠 있으면 더 어색하다
            limit = max_chars if len(wa) > 1 else int(max_chars * 1.25)
            if sc < -40 and joined <= limit:
                last.text = f"{last.text} {p.text}"
                last.dur = p.end - last.start
                last.words = last.words + p.words
                continue
        out.append(p)
    return out


# ── 강조 색은 짧게 ────────────────────────────────────────────────
#
# 2026-09-16 대표님 지시: "색깔 처리가 너무 길면 안 돼."
# 풀링3(완성본) 실측 — 색 자막 87줄(26.6%) · 빨강 56 / 파랑 31 · 전부 **줄 통째**
# (한 줄 안에서 일부만 칠한 건 0). 이어서 칠한 줄 수는 2줄 27번 · 1줄 11번 ·
# 3줄 6번 · 4줄 1번(네 자리를 나란히 부른 나열).
# 그런데 색은 **문장 단위**로 정해지고, 긴 문장은 자막 5~10줄로 쪼개진다. 그래서
# 한 문장이 걸리면 화면이 한참 동안 빨갛다 — 0916 에서 빨강이 10줄 이어졌다.
# 내가 칠하자고 한 긴 문구를 대표님이 줄이실 때는 **앞쪽(도입)을 걷고 끝(판단)을
# 남기셨다**: "인터넷 보면 트레이너 돈 못 번다, / 3년 넘기기 힘들다, / 요즘 PT 안
# 팔린다" → 뒤 두 줄만.
EMPH_MAX_RUN = 2
# 같은 색 문장 사이에 둘 흰 줄 수. 2 로 올리면 0916 마무리 같은 빨흰빨 반복은
# 없어지지만, 풀링3 에서 대표님이 흰 줄 하나 사이로 같은 색을 다시 쓰신 곳이
# 47덩어리 중 6곳이라 F1 이 58.3 → 54.8 로 떨어졌다(0818 은 35.8 → 36.9). 1 로 둔다.
EMPH_GAP_LINES = 1
EMPH_LINE_RATIO = 0.26
EMPH_SENT_RATIO = 0.45


def _cap_emphasis(pieces: list[SubtitlePiece],
                  line_ratio: float | None) -> list[SubtitlePiece]:
    """한 문장에서 이어 칠한 줄을 끝 두 줄로 줄이고, 전체 비율을 맞춘다."""
    from .style import score as emph_score

    groups: list[list[int]] = []
    for i, p in enumerate(pieces):
        if p.kind == "normal":
            continue
        if (groups and pieces[groups[-1][-1]].seg_id == p.seg_id
                and pieces[groups[-1][-1]].kind == p.kind
                and groups[-1][-1] == i - 1):
            groups[-1].append(i)
        else:
            groups.append([i])

    for g in groups:
        for k in g[:-EMPH_MAX_RUN]:
            pieces[k].kind = "normal"
        del g[:-EMPH_MAX_RUN]

    # 문장이 달라도 **화면에서는 이어져 보인다.** 이웃한 두 문장이 각각 두 줄씩
    # 칠해지면 네 줄 연속이 된다(2026-09-16 실측). 이어진 색이 세 줄을 넘거나,
    # 흰 줄이 EMPH_GAP_LINES 줄 미만으로 떨어진 같은 색 두 문장은 약한 쪽 색을 뺀다.
    groups = [g for g in groups if g]
    i = 0
    while i + 1 < len(groups):
        a, b = groups[i], groups[i + 1]
        if (a and b and b[0] - a[-1] - 1 < EMPH_GAP_LINES
                and pieces[a[0]].kind == pieces[b[0]].kind
                and (b[0] - a[-1] - 1 > 0 or len(a) + len(b) > EMPH_MAX_RUN + 1)):
            sa = emph_score(" ".join(pieces[k].text for k in a))[1]
            sb = emph_score(" ".join(pieces[k].text for k in b))[1]
            weak = a if sa < sb else b
            for k in weak:
                pieces[k].kind = "normal"
            groups.remove(weak)
            continue
        i += 1

    if line_ratio is not None and pieces:
        budget = int(round(len(pieces) * line_ratio))
        colored = sum(len(g) for g in groups)
        if colored > budget:
            # 약한 것부터 걷어내되 **구간 몫은 지킨다.**
            # 그냥 약한 순으로 걷으면 점수가 낮게 나오는 도입부가 먼저 비어 버린다 —
            # 2026-10-05 촬영본에서 앞 2분 색 비율이 1% 였다 (대표님 완성본 12~27%).
            span = max(1, pieces[-1].start + pieces[-1].dur)
            nwin = max(1, min(8, round(span / 60_000_000)))
            def win_of(i: int) -> int:
                return min(nwin - 1, int(pieces[i].start / span * nwin))
            in_win = [0] * nwin
            for i, p in enumerate(pieces):
                in_win[win_of(i)] += 1
            left = [0] * nwin
            for g in groups:
                left[win_of(g[0])] += len(g)
            floor = [max(1, int(round(n * line_ratio * 0.5))) for n in in_win]
            ranked = sorted(groups, key=lambda g: emph_score(
                " ".join(pieces[k].text for k in g))[1])
            for g in ranked:
                if colored <= budget:
                    break
                w = win_of(g[0])
                if left[w] - len(g) < floor[w]:
                    continue                 # 이 구간은 더 걷으면 비어 버린다
                for k in g:
                    pieces[k].kind = "normal"
                colored -= len(g)
                left[w] -= len(g)
    return pieces


def _fill_gaps(pieces: list[SubtitlePiece],
               clips: list[tuple[int, int]]) -> list[SubtitlePiece]:
    """빈틈을 없애되 **클립 경계를 넘지 않게** 채운다.

    자막이 붙었다 떨어졌다 하면 화면이 깜빡이는 것처럼 보이니 빈틈은 없어야
    한다. 그런데 다음 자막까지 무작정 늘리면 컷을 가로질러 버린다 — 실측
    (2026-08-18): 자막 241개 중 **140개**가 클립 경계를 넘고 있었고, 클립
    시작과 자막 시작이 중앙 75ms, 최대 1.34초까지 어긋났다.

    그래서 클립마다 따로 채운다. 클립 안에서는 자막끼리 사슬처럼 잇고,
    첫 자막은 클립 시작에, 마지막 자막은 클립 끝에 **정확히** 붙인다.
    자막이 하나도 없는 클립은 앞 자막의 글을 그대로 이어 화면이 비지 않게 한다.
    """
    if not pieces or not clips:
        return pieces
    by_clip: dict[int, list[SubtitlePiece]] = {}
    for p in pieces:
        by_clip.setdefault(p.keep_idx, []).append(p)

    out: list[SubtitlePiece] = []
    carry: SubtitlePiece | None = None
    for ci, (cs, ce) in enumerate(clips):
        ps = sorted(by_clip.get(ci, []), key=lambda x: x.start)
        if not ps:
            if carry is not None:
                out.append(SubtitlePiece(
                    start=cs, dur=ce - cs, text=carry.text, words=[],
                    kind=carry.kind, seg_id=carry.seg_id, keep_idx=ci))
            continue
        for i in range(len(ps) - 1):
            ps[i].dur = ps[i + 1].start - ps[i].start
        ps[0].dur += ps[0].start - cs
        ps[0].start = cs
        ps[-1].dur = ce - ps[-1].start
        out.extend(ps)
        carry = ps[-1]
    return [p for p in out if p.dur > 0]


def _removed_has_speech(keeps: list[Span], words, a: int, b: int) -> bool:
    """클립 a~b 사이에서 **살려야 할 말을 들어냈는가.**

    길이로 재면 안 된다. 숨 고르기는 2초여도 이어 붙여야 자연스럽고,
    잔말은 0.3초여도 이으면 뜻이 어긋난다. 기준은 길이가 아니라
    **거기에 말이 있었느냐**다.

    무음을 들어낸 자리는 넘어가도 된다. **잔말("음", "어")도 마찬가지다** —
    잔말은 애초에 없던 것처럼 지우는 게 목적이라, 그 자리를 가로질러
    "제가" + "아는 선생님이 한 분 있어요" 로 이어야 문장이 산다.

    막아야 하는 건 **NG·재테이크를 들어낸 자리**뿐이다. 거기를 이으면
    서로 다른 테이크의 말이 한 줄에 붙어 뜻이 어긋난다.
    """
    if a == b:
        return False
    if a < 0 or b < 0 or a >= b or b >= len(keeps):
        return True             # 모르면 잇지 않는다
    for i in range(a, b):
        lo, hi = keeps[i].end, keeps[i + 1].start
        if hi - lo <= 0:
            continue
        for w, kind in words:
            if kind != "ng":            # 무음·잔말은 넘어가도 된다
                continue
            # 들어낸 구간과 낱말이 절반 넘게 겹치면 "그 말을 들어냈다"고 본다
            ov = min(hi, w.end) - max(lo, w.start)
            if ov > 0 and ov * 2 > (w.end - w.start):
                return True
    return False


# 이 글자수 이하면 "토막"으로 보고 한 줄 한도를 이만큼 늘려서라도 잇는다
FRAG_CHARS = 5
FRAG_SLACK = 6


def _join_fragments(pieces: list[SubtitlePiece], keeps: list[Span], words,
                    max_chars: int) -> list[SubtitlePiece]:
    """컷 때문에 갈라진 **말 토막**을 도로 잇는다.

    컷이 촘촘해지면(실측 170컷) 한 문장이 클립 경계마다 잘려
    "세" / "가지만 말씀드릴게요" 처럼 낱말 한복판이 갈라진다.
    대표님 완성본은 자막이 컷을 그냥 넘어간다 (1편 39%).

    잇는 조건 — 넷 다 맞을 때만:
      · 같은 문장에서 나왔고
      · 시간이 붙어 있고
      · 그 사이에서 **NG를 들어내지 않았고** (잔말·무음은 이어도 된다)
      · 이어 붙여도 한 줄 길이를 안 넘는다
    """
    out: list[SubtitlePiece] = []
    for p in pieces:
        if out:
            last = out[-1]
            joined = f"{last.text} {p.text}".strip()
            # 한쪽이 아주 짧으면(토막) 한 줄 한도를 조금 넘겨도 잇는다.
            # "근데" 나 "그건" 이 혼자 한 줄을 차지하는 것보다, 스무 자 남짓
            # 되더라도 말이 이어지는 편이 읽기 좋다.
            tiny = min(len(last.text), len(p.text)) <= FRAG_CHARS
            limit = max_chars + (FRAG_SLACK if tiny else 0)
            if (last.seg_id == p.seg_id
                    and abs(p.start - last.end) <= 2
                    and len(joined) <= limit
                    and not _removed_has_speech(keeps, words,
                                                last.keep_idx, p.keep_idx)):
                last.text = joined
                last.dur = p.end - last.start
                last.words = last.words + p.words
                continue
        out.append(p)
    return out


def map_subtitles(
    keeps: list[Span],
    tr: Transcript,
    max_chars: int = 18,
    min_dur_s: float = 0.25,
    merge_gap_s: float = 0.12,

    fill_gaps: bool = True,
    emphasis_plan: dict[str, str] | None = None,
    auto_emphasis: bool = False,
) -> list[SubtitlePiece]:
    """살아남은 대본 세그먼트를 타임라인 좌표의 자막 조각으로 바꾼다.

    순서: 컷 경계로 조각내기 → 갈라진 문장 다시 잇기 → 한 줄 길이로 쪼개기
          → 빈틈 메우기

    한 세그먼트가 컷에 의해 두 동강 나면 조각도 둘로 나뉜다. 이때 단어
    타임스탬프가 있으면 글자도 제대로 갈라준다 (없으면 통문장을 유지).
    """
    # keeps 각각의 타임라인 시작 위치를 미리 계산
    offsets: list[int] = []
    acc = 0
    for k in keeps:
        offsets.append(acc)
        acc += k.dur

    min_dur = int(min_dur_s * US)
    pieces: list[SubtitlePiece] = []

    # 강조 배정은 **대본 전체를 놓고** 정한다. 문장을 하나씩 보면 키워드가
    # 걸린 것마다 강조가 붙어서 화면 절반이 색깔이 된다.
    live_segs = [s for s in tr.segs if not s.dropped and s.text.strip()]
    texts = [s.text for s in live_segs]
    # 계획 파일이 있으면 그걸 먼저 따른다. 어디가 전환점이고 무엇이 핵심
    # 주장인지는 글 전체를 읽어야 아는 것이라 규칙으로는 못 잡는다.
    # 강조(빨강·파랑)는 **기본으로 안 넣는다.**
    #
    # 어디가 부정이고 어디가 전환점인지는 글 전체를 읽고 판단해야 하는데,
    # 규칙으로 고르면 타이밍이 들쭉날쭉했다. 2026-08-18 대표님 결정 —
    # 색 자막과 확대는 직접 넣으시고, 에이전트는 컷과 싱크만 책임진다.
    # 계획 파일(emphasis_plan)을 주면 그건 그대로 따른다.
    if emphasis_plan:
        assigned = apply_plan(texts, emphasis_plan)
    elif auto_emphasis:
        # 문장을 넉넉히 고른다. 아래 _cap_emphasis 가 문장마다 끝 두 줄만
        # 남기고, 전체가 자막 줄의 26% 를 넘지 않게 약한 것부터 걷어낸다.
        assigned = pick_emphasis(texts, ratio=EMPH_SENT_RATIO)
    else:
        assigned = ["normal"] * len(texts)
    kinds = dict(zip((id(s) for s in live_segs), assigned))

    for seg in tr.segs:
        if seg.dropped or not seg.text.strip():
            continue

        # 이 세그먼트가 걸치는 keep 들
        hits = []
        for ki, (k, off) in enumerate(zip(keeps, offsets)):
            inter = intersect(Span(seg.start, seg.end), k)
            if inter is not None and inter.dur >= min_dur:
                hits.append((ki, k, off, inter))
        if not hits:
            continue

        # 일부러 떼어낸 가장자리 잔말만 제외한다
        live = [w for w in seg.words
                if not any(a <= w.start < b for a, b in seg.holes)]

        if not live:
            ki, k, off, inter = max(hits, key=lambda h: h[3].dur)
            pieces.append(SubtitlePiece(
                start=off + (inter.start - k.start), dur=inter.dur,
                text=seg.effective_text(), words=[],
                kind=kinds.get(id(seg), 'normal'), seg_id=id(seg),
                keep_idx=ki))
            continue

        # 단어를 걸치는 keep 에 배정한다.
        #
        # 핵심: 어느 keep 에도 안 걸리는 단어라도 **버리지 않는다.**
        # 무음컷은 침묵을 지우는 것이지 단어를 지우는 게 아니다. 단어가 컷
        # 구간에 놓였다면 그건 whisper 타임스탬프 오차이지 삭제 의도가 아니다.
        # 여기서 버리면 자막이 실제 음성과 다른 말을 하게 된다.
        buckets: dict[int, list[Word]] = {i: [] for i in range(len(hits))}
        for w in live:
            best, best_ov = -1, 0
            for i, (_, _, _, inter) in enumerate(hits):
                ov = min(w.end, inter.end) - max(w.start, inter.start)
                if ov > best_ov:
                    best, best_ov = i, ov
            if best < 0:  # 컷 구간에 놓인 단어 → 시간상 가장 가까운 조각으로
                best = min(range(len(hits)),
                           key=lambda i: min(abs(w.start - hits[i][3].start),
                                             abs(w.start - hits[i][3].end)))
            buckets[best].append(w)

        for i, (ki, k, off, inter) in enumerate(hits):
            ws = buckets[i]
            if not ws:
                continue
            tl_words: list[Word] = []
            for w in ws:
                # keep 밖으로 삐져나온 부분은 경계로 당긴다
                s = min(max(w.start, inter.start), inter.end)
                e = min(max(w.end, inter.start), inter.end)
                tl_words.append(Word(start=off + (s - k.start),
                                     end=off + (max(e, s) - k.start),
                                     text=w.text))
            pieces.append(SubtitlePiece(
                start=off + (inter.start - k.start),
                dur=inter.dur,
                text=" ".join(w.text for w in ws).strip(),
                words=tl_words, seg_id=id(seg), keep_idx=ki,
                # 강조 판정은 **문장 전체**로 한다. 조각난 뒤에 판단하면
                # "힘들지" / "않습니다" 가 갈려서 뜻이 뒤집힌다.
                kind=kinds.get(id(seg), 'normal'),
            ))

    pieces.sort(key=lambda p: p.start)

    # 붙어 있는 조각 병합 — 같은 문장이 컷 때문에 갈라진 경우를 되돌린다.
    # 여기서는 길이를 신경쓰지 않는다. 어차피 아래에서 한 줄씩 다시 쪼갠다.
    merge_gap = int(merge_gap_s * US)
    all_words = [(w, getattr(sg, 'kind', '')) 
                 for sg in tr.segs for w in (sg.words or [])]
    merged: list[SubtitlePiece] = []
    for p in pieces:
        if merged:
            last = merged[-1]
            gap = p.start - last.end
            # 같은 문장에서 갈라진 것만 다시 붙인다.
            # 문장을 넘어 붙이면 "될 거예요. 제가" 처럼 한 자막에 두 문장이
            # 섞여서, 읽는 사람이 어디서 끊어 읽어야 할지 알 수 없게 된다.
            # 클립이 달라도 **짧은 무음만 잘린 자리라면 다시 붙인다.**
            #
            # 예전엔 클립이 다르면 무조건 끊었는데, 컷이 촘촘해지자(170컷)
            # "세" / "가지만 말씀드릴게요" 처럼 낱말 한복판이 갈라졌다.
            # 대표님 완성본은 자막이 컷을 그냥 넘어간다 (1편 39% · 오리엔 7%).
            #
            # 다만 아무 데서나 이으면 안 된다. 잔말·NG 처럼 **말을 실제로
            # 들어낸 자리**를 가로지르면 "…보이는" + "신호가" 사이에 없어진
            # 말이 있는데도 한 줄로 읽혀 뜻이 어긋난다. 그래서 그 사이에서
            # 원본을 얼마나 들어냈는지 보고, 숨 고르기 수준일 때만 잇는다.
            if (last.seg_id == p.seg_id and last.keep_idx == p.keep_idx
                    and 0 <= gap <= merge_gap):
                last.dur = p.end - last.start
                last.text = f"{last.text} {p.text}".strip()
                last.words = last.words + p.words
                continue
        merged.append(p)

    # 짧은 조각을 그냥 버리면 **글자가 사라진다.** 같은 클립 이웃에 붙여
    # 넘기고, 이웃이 없을 때만(클립에 혼자일 때) 그대로 둔다 — 어차피 아래
    # _fill_gaps 가 클립 길이만큼 늘려준다.
    kept: list[SubtitlePiece] = []
    for i, p in enumerate(merged):
        if p.dur >= min_dur:
            kept.append(p)
            continue
        if kept and kept[-1].keep_idx == p.keep_idx:
            kept[-1].text = f"{kept[-1].text} {p.text}".strip()
            kept[-1].dur = p.end - kept[-1].start
            kept[-1].words = kept[-1].words + p.words
        elif i + 1 < len(merged) and merged[i + 1].keep_idx == p.keep_idx:
            nxt = merged[i + 1]
            nxt.text = f"{p.text} {nxt.text}".strip()
            nxt.dur = nxt.end - p.start
            nxt.start = p.start
            nxt.words = p.words + nxt.words
        else:
            kept.append(p)
    merged = kept

    # 한 줄에 들어가는 길이로 쪼갠다 (두 줄로 감싸지 않는다)
    split: list[SubtitlePiece] = []
    for p in merged:
        split.extend(_split_one_line(p, max_chars))

    # 겹침 제거 — 캡컷 자막 트랙에서 세그먼트가 겹치면 로드가 깨진다
    split.sort(key=lambda p: p.start)
    for i in range(len(split) - 1):
        overlap = split[i].end - split[i + 1].start
        if overlap > 0:
            split[i].dur -= overlap
    split = [p for p in split if p.dur > 0]
    split = _repair_breaks(split, max_chars)
    split = _cap_emphasis(split, EMPH_LINE_RATIO if (auto_emphasis and not emphasis_plan)
                          else None)

    if fill_gaps:
        split = _fill_gaps(split, [(o, o + k.dur)
                                   for k, o in zip(keeps, offsets)])

    # **마지막에 한 번 더 정리한다.**
    #
    # 위에서 겹침을 걷어내도 _repair_breaks / _fill_gaps 가 뒤에서 길이를
    # 다시 늘린다. 자막이 클립을 넘어갈 수 있게 된 뒤로는 그 늘리기가
    # 다음 자막을 파고들었다 (실측 겹침 17건 · 순서까지 뒤집혔다).
    # 캡컷은 자막 트랙이 겹치면 로드가 깨지므로 여기서 못을 박는다.
    split.sort(key=lambda p: p.start)
    for i in range(len(split) - 1):
        over = split[i].end - split[i + 1].start
        if over > 0:
            split[i].dur -= over
    split = [p for p in split if p.dur > 0]

    # **토막 잇기는 제일 마지막에 한다.**
    #
    # 중간에서 이으면 뒤따르는 클립별 처리(by_clip)가 클립을 넘어선 조각을
    # 다루지 못해 자막이 중복되고 순서가 뒤집힌다 (실측: "한 명도 없었어요."
    # 가 네 번 찍혔다). 여기는 뒤에 아무 단계도 없어서 안전하다.
    return _join_fragments(split, keeps, all_words, max_chars)

# ── 자막 경계를 컷 경계에 붙이기 (2026-09-24 대표님 지시, 10-05 받아쓰기 경로에도 적용) ──
#
# "자막 자른 것과 영상 자른 것이 똑같이 맞아떨어져야 한다."
# 컷은 말 앞뒤로 pad(0.08초) 여유를 두는데 자막은 말 끝에서 끝나 그만큼 어긋난다.
# 대표님 완성본 기준: 자막이 컷을 가로지르는 비율 0.8~4.6%.
SNAP_US = 300_000          # 이 안쪽이면 자막 경계를 컷 자리로 당긴다
PROTECT_GAP_US = 400_000   # 자막 한 줄 **안쪽**의 이보다 짧은 쉼은 자르지 않는다


def keep_bounds(keeps: list[Span]) -> list[int]:
    """클립 경계를 배속 전 타임라인 좌표로."""
    out, acc = [0], 0
    for k in keeps:
        acc += k.dur
        out.append(acc)
    return out


def protect_inner_gaps(pieces: list[SubtitlePiece], keeps: list[Span],
                       max_gap_us: int = PROTECT_GAP_US) -> list[Span] | None:
    """자막 한 줄 안에서 잘린 **짧은 쉼**을 되살린 keeps 를 돌려준다 (없으면 None)."""
    bounds = keep_bounds(keeps)
    drop: set[int] = set()
    for p in pieces:
        for i, b in enumerate(bounds[1:-1], start=1):
            if p.start < b < p.start + p.dur:
                gap = keeps[i].start - keeps[i - 1].end
                if 0 < gap <= max_gap_us:
                    drop.add(i)
    if not drop:
        return None
    out = [Span(keeps[0].start, keeps[0].end)]
    for i, k in enumerate(keeps[1:], start=1):
        if i in drop:
            out[-1].end = k.end
        else:
            out.append(Span(k.start, k.end))
    return out


def snap_pieces_to_cuts(pieces: list[SubtitlePiece], keeps: list[Span],
                        tol_us: int = SNAP_US) -> int:
    """자막 시작을 컷 경계에 붙인다. 말이 있는 구간으로는 넘어가지 않는다."""
    if not pieces:
        return 0
    bounds = keep_bounds(keeps)
    moved = 0
    for i, p in enumerate(pieces):
        lo = pieces[i - 1].start if i else -1
        prev_speech = (pieces[i - 1].words[-1].end
                       if i and pieces[i - 1].words else None)
        new = None
        if prev_speech is not None and prev_speech < p.start:
            inside = [b for b in bounds if prev_speech < b < p.start]
            if inside:
                new = inside[0]
        if new is None:
            near = min(bounds, key=lambda b: abs(b - p.start))
            if abs(near - p.start) <= tol_us and near > lo:
                new = near
        if new is not None and new != p.start and new > lo:
            p.start = new
            moved += 1
    pieces.sort(key=lambda p: p.start)
    for i, p in enumerate(pieces):
        if i + 1 < len(pieces):
            p.dur = max(1, pieces[i + 1].start - p.start)
    return moved
