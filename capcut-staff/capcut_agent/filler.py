"""전체 대본을 보고 잔말(filler)과 NG(재테이크)를 판정한다.

판정은 **세그먼트(문장/호흡) 단위**로만 한다. 단어 단위로 자르면
"그 사람이" 의 '그' 까지 잔말로 먹어서 말이 부서진다.

기조는 보수적이다. 토킹 영상에서 덜 자른 건 사람이 캡컷에서 지우면 되지만,
잘못 잘린 건 알아채기도 어렵고 복구도 번거롭다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .transcript import Seg, Transcript, normalize, normalize_nospace

US = 1_000_000

# 단독으로 나오면 잔말인 토큰. 문장 안에 섞여 있으면 건드리지 않는다.
FILLER_TOKENS = {
    "어", "어어", "엄", "음", "음음", "아", "아아", "으", "으음", "흠",
    "그", "그그", "저", "저기", "뭐", "뭐지", "뭐랄까", "이제", "인제",
    "그니까", "그러니까", "그러면", "그래서", "근데", "그런데",
    "네", "예", "자", "좀", "약간", "막", "이렇게", "그렇게",
    "아니", "아니아니", "잠깐", "잠시만", "에", "엥", "웅", "응",
}

# 문장 **가장자리에서 떼어내도 되는** 것들.
#
# 위 목록을 그대로 쓰면 안 된다. "그래서 / 근데 / 그러니까" 는 잔말이 아니라
# 앞뒤 논리를 잇는 접속부사다. 실제로 "그래서 시간 내서 세미나 듣고…" 에서
# '그래서' 가 떨어져 나가 문장이 "시간 내서…" 로 시작해버린 적이 있다.
# 접속이 사라지면 앞 문장과의 관계가 끊겨서 말이 붕 뜬다.
#
# 여기에는 **뜻을 담지 않는 소리**만 남긴다.
EDGE_TRIM_TOKENS = {
    "어", "어어", "엄", "음", "음음", "아", "아아", "으", "으음", "흠",
    "그", "그그", "저", "저기", "뭐", "뭐지", "뭐랄까",
    "에", "엥", "웅", "응", "네", "예",
}

# NG 신호. 이게 나오면 그 앞은 실패한 테이크일 확률이 높다.
#
# 두 등급으로 나눈 이유: "다시" 는 정상 발화에도 흔하다 ("다시 말해서",
# "다시 한번 보면"). 강한 신호가 아니면 짧은 세그먼트에서만 인정한다.
# 촬영을 끊는 말은 실제로 짧게 튀어나오지, 문장 한복판에 박히지 않는다.
NG_MARKERS_STRONG = (
    "다시할게", "다시갈게", "다시하겠습니다", "다시가겠습니다", "다시찍",
    "리테이크", "한번더갈게", "말실수", "잘못말했", "틀렸다",
)
NG_MARKERS_WEAK = (
    "다시", "컷", "스톱", "잠깐만", "잠시만", "죄송", "미안",
    "아니다", "아니야", "한번더", "아까",
)
# 약한 신호를 NG 로 인정할 최대 세그먼트 길이
WEAK_MARKER_MAX_DUR_S = 2.5
# 약한 신호는 **그 신호가 문장의 거의 전부**일 때만 인정한다.
#
# '아까'·'다시'·'잠깐만' 은 평범한 한국어 낱말이라 길이만으로 거르면 정상
# 문장이 통째로 날아간다. 실측(2026-08-18): "아까 그 선생님도 3개월을 매일
# 쓰셨어요." 가 NG 로 찍혀 **앞 문장까지** 함께 지워졌다 (5.7초).
# 진짜 재촬영 신호는 "잠깐만요." 처럼 그 말만 뱉는다.
WEAK_MARKER_EXTRA_CHARS = 6

_HANGUL = re.compile(r"[가-힣]")
US = 1_000_000


@dataclass
class FillerParams:
    enable_filler: bool = True
    enable_ng: bool = True
    ng_similarity: float = 0.78     # 이 이상 닮았으면 재테이크로 본다
    ng_lookahead: int = 3           # 몇 세그먼트 뒤까지 비교할지
    ng_min_chars: int = 6           # 너무 짧은 문장은 우연히 닮으므로 제외
    ng_prefix_min_chars: int = 4    # 앞부분이 **정확히** 겹칠 때 쓰는 최소 길이
    filler_max_dur_s: float = 2.0   # 이보다 긴 세그먼트는 잔말로 안 본다
    drop_marker_segment: bool = True  # NG 마커가 들어간 세그먼트 자체도 버릴지
    trim_edges: bool = True         # 문장 앞뒤에 붙은 잔말만 따로 떼어낼지
    trim_max_s: float = 1.5         # 한쪽에서 떼어낼 수 있는 최대 길이
    tail_retake_ratio: float = 0.82  # 앞 문장 **꼬리**와 뒤 문장이 이만큼 닮으면 되풀이
    tail_retake_min_chars: int = 6   # 그 꼬리의 최소 글자수
    tail_retake_gap_s: float = 6.0   # 두 시도 사이가 이보다 멀면 되풀이로 안 본다


@dataclass
class FillerStats:
    filler: int = 0
    ng: int = 0
    trimmed: int = 0        # 문장 가장자리에서 떼어낸 잔말 개수
    tail_retakes: int = 0   # 꼬리만 다시 말해서 걷어낸 곳
    dropped_us: int = 0


def _trim_edges(seg: Seg, p: FillerParams) -> int:
    """문장 앞뒤에 달라붙은 잔말만 떼어낸다.

    Whisper 는 "음." 같은 짧은 잔말을 독립 세그먼트로 안 내주고 옆 문장에
    붙여버린다 ("음. 제가 준비한 내용은…"). 그래서 세그먼트 단위 판정만으로는
    영영 못 잡는다.

    판단 근거는 여전히 대본이다. 단어 경계는 **어디서 자를지**에만 쓴다 —
    단어 단위로 문장을 재구성하지는 않는다.
    """
    if not seg.words or len(seg.words) < 2:
        return 0

    def is_f(w) -> bool:
        t = normalize(w.text)
        return bool(t) and t in EDGE_TRIM_TOKENS

    limit = int(p.trim_max_s * US)
    n = len(seg.words)
    hit = 0

    # 앞쪽
    i = 0
    while i < n - 1 and is_f(seg.words[i]):
        i += 1
    if i > 0 and seg.words[i - 1].end - seg.start <= limit:
        seg.holes.append((seg.start, seg.words[i - 1].end))
        hit += i

    # 뒤쪽
    j = n - 1
    while j > i and is_f(seg.words[j]):
        j -= 1
    if j < n - 1 and seg.end - seg.words[j + 1].start <= limit:
        seg.holes.append((seg.words[j + 1].start, seg.end))
        hit += (n - 1 - j)

    return hit


def _is_filler(seg: Seg, p: FillerParams) -> bool:
    """세그먼트 전체가 잔말로만 이루어졌는가."""
    if seg.dur > int(p.filler_max_dur_s * US):
        return False
    norm = normalize(seg.text)
    if not norm:
        return True
    if not _HANGUL.search(norm):
        # 한글이 하나도 없는 짧은 조각 — 대개 잡음을 받아적은 것
        return len(norm) <= 3
    tokens = norm.split()
    if not tokens:
        return True
    # 모든 토큰이 잔말 사전에 있어야 한다. 하나라도 실제 단어면 살린다.
    return all(t in FILLER_TOKENS for t in tokens)


def _has_ng_marker(seg: Seg) -> str | None:
    """NG 신호를 찾는다. 약한 신호는 짧은 세그먼트에서만 인정."""
    flat = normalize_nospace(seg.text)
    for m in NG_MARKERS_STRONG:
        if m in flat:
            return m
    if seg.dur <= int(WEAK_MARKER_MAX_DUR_S * US):
        for m in NG_MARKERS_WEAK:
            if m in flat and len(flat) <= len(m) + WEAK_MARKER_EXTRA_CHARS:
                return m
    return None


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _is_retake(prev: str, cur: str, p: FillerParams) -> tuple[bool, str]:
    """prev 가 cur 의 실패한 앞선 테이크인가."""
    a, b = normalize_nospace(prev), normalize_nospace(cur)

    # 앞 문장이 뒤 문장의 **정확한** 앞부분이면 같은 말로 다시 시작한 것이다.
    # 흐릿한 유사도보다 훨씬 센 근거라 최소 길이를 따로 둔다. 실측: '왜 그런지랑'
    # (5자) 이 ng_min_chars=6 에 먼저 걸려 이 규칙까지 가지도 못하고 살아남았다.
    if p.ng_prefix_min_chars <= len(a) < len(b) and b.startswith(a):
        return True, "말하다 끊고 다시 시작"

    if len(a) < p.ng_min_chars or len(b) < p.ng_min_chars:
        return False, ""

    ratio = _similar(a, b)
    if ratio >= p.ng_similarity:
        return True, f"직후 반복 (유사도 {ratio:.0%})"

    # 말하다 끊고 다시 시작한 경우: 앞 문장이 뒤 문장의 앞부분과 겹친다
    if len(a) < len(b) and b.startswith(a[:max(p.ng_min_chars, len(a) * 8 // 10)]):
        return True, "말하다 끊고 다시 시작"

    return False, ""


def drop_tail_retakes(tr: Transcript, p: FillerParams | None = None) -> int:
    """문장 **꼬리만** 다시 말한 되풀이를 걷어낸다.

    실측(2026-09-23 촬영본 첫머리):
        "헬스 트레이너 업계 이제 진짜 바닥을 찍은 걸까요?"   ← 첫 시도
        "이제 진짜 바닥을 찍은 걸까요?"                      ← 다시 말함
    _is_retake 는 "앞 문장이 뒤 문장의 앞부분"인 경우만 봐서 이걸 못 잡았고
    (유사도 0.77 로 기준 0.78 바로 아래), 같은 말이 영상에 두 번 남았다.

    앞 문장을 통째로 버리면 "헬스 트레이너 업계" 가 사라진다. 그래서 **되풀이된
    꼬리만** 구멍으로 판다 — 낱말 경계에서 자르므로 자막도 그만큼만 짧아진다.
    """
    p = p or FillerParams()
    if not p.enable_ng:
        return 0
    live = [s for s in tr.segs if not s.dropped and s.words]
    n = 0
    for a, b in zip(live, live[1:]):
        if b.start - a.end > int(p.tail_retake_gap_s * US):
            continue
        cur = normalize_nospace(b.text)
        if len(cur) < p.tail_retake_min_chars:
            continue
        best = None
        for k in range(len(a.words)):
            tail = normalize_nospace(" ".join(w.text for w in a.words[k:]))
            if len(tail) < p.tail_retake_min_chars:
                break
            r = _similar(tail, cur)
            if best is None or r > best[0]:
                best = (r, k, len(tail))
        if not best or best[0] < p.tail_retake_ratio:
            continue
        r, k, _ = best
        if k == 0 and _similar(normalize_nospace(a.text), cur) >= p.ng_similarity:
            continue                       # 통째로 같은 건 _is_retake 가 처리한다
        a.holes.append((a.words[k].start, a.end))
        n += 1
    return n


def drop_false_start(tr: Transcript, max_gap_s: float = 1.5,
                     window_s: float = 25.0) -> int:
    """영상 첫머리의 헛시작을 떼어낸다.

    카메라를 켜고 한 마디 뱉었다가 멈추고, 자세를 고쳐 잡은 뒤 다시 시작하는
    일이 흔하다. 그 한 마디는 **소리로는 멀쩡한 말**이라 무음컷에 안 걸리고,
    대본에도 정상 낱말로 남는다.

    단서는 **그 뒤의 긴 공백**이다. 실제 사례: "요즘"(1.7~3.4s) 다음 낱말이
    8.4s — 5초가 비어 있었다.

    다만 **낱말 자체는 지우지 않는다.** 그 공백 뒤에 같은 말을 다시 하는
    경우(진짜 헛시작)도 있지만, 그냥 한 마디 뱉고 생각하다 이어가는 경우도
    많다. 실제로 "요즘 (5초) 회원이 없어서…" 는 뒤가 이어지는 한 문장이었고,
    낱말까지 지웠더니 영상이 "회원이 없어서" 로 시작해버렸다.
    공백에는 뒤척이는 소리가 섞여 무음컷에 안 걸리므로 여기서 잘라준다.

    돌려주는 값: 잘라낸 공백 수.
    """
    live = [(s, w) for s in tr.segs if not s.dropped for w in s.words]
    live.sort(key=lambda x: x[1].start)
    if len(live) < 2:
        return 0

    gap = int(max_gap_s * US)
    limit = int(window_s * US)
    dropped = 0
    for i in range(len(live) - 1):
        seg, w = live[i]
        if w.start > limit:
            break                       # 도입부에서만 본다
        if live[i + 1][1].start - w.end < gap:
            break                       # 여기서부터 말이 이어진다 — 진짜 시작
        # 낱말은 그대로 두고 **뒤따르는 공백만** 잘라낸다
        seg.media_holes.append((w.end, live[i + 1][1].start))
        dropped += 1
    return dropped


def drop_retakes(tr: Transcript, video, strong_per_syllable: float = 0.70,
                 similarity: float = 0.80) -> list[tuple[int, int, float]]:
    """소리로 재테이크(말더듬)를 찾아 실패한 시도를 떼어낸다.

    whisper 는 같은 말을 두 번 하면 한 번만 받아적는다. 대신 **삼킨 시간이
    낱말 하나에 몰린다** — 실제로 "물을" 한 낱말이 2.24초(음절당 1.12초,
    중앙값의 7배)로 잡혔고 그 안에 실패한 시도가 들어 있었다.

    소리 유사도만 보면 17곳이 걸리는데 대부분 오검출이다(다른 낱말끼리도
    0.80 은 나온다). **낱말 길이 이상치와 겹치는 곳만** 자른다 — 두 신호가
    같이 가리키면 믿을 만하다.

    돌려주는 값: 자르지 않고 넘긴 약한 후보들 (사람이 볼 목록).
    """
    from .retake import find_retakes

    anom = []
    for s in tr.segs:
        for w in s.words:
            syl = len(_HANGUL.findall(w.text))
            if syl and (w.end - w.start) / US / syl >= strong_per_syllable:
                anom.append(w)

    # 첫 낱말은 재테이크 검출에서 지킨다.
    #
    # 영상 첫머리는 "한 마디 하고 뜸 들이기" 가 흔해서, 소리만 보면 말더듬과
    # 구분이 안 된다. 실제로 "요즘 (5초) 회원이 없어서…" 의 '요즘' 이 여기서
    # 잘려나가 영상이 "회원이 없어서" 로 시작해버렸다.
    # 도입부 공백은 drop_false_start 가 이미 처리하므로 여기선 손대지 않는다.
    first_word_end = min((w.end for s in tr.segs if not s.dropped
                          for w in s.words), default=0)

    weak: list[tuple[int, int, float]] = []
    for r in find_retakes(video, similarity=similarity):
        if r.start <= first_word_end:
            continue
        near = [w for w in anom
                if r.start - 2 * US <= w.start <= r.end + 2 * US]
        if not near:
            weak.append((r.start, r.end, r.score))
            continue
        # 실패한 시도를 구멍으로 표시한다.
        # 다만 한 문장의 대부분을 먹어버리면 말더듬이 아니라 오검출이다 —
        # 실제로 20자짜리 문장이 0.5초만 남은 적이 있다. 그런 건 넘긴다.
        for s in tr.segs:
            if s.start <= r.start <= s.end or s.start <= r.end <= s.end:
                overlap = min(r.end, s.end) - max(r.start, s.start)
                if overlap > (s.end - s.start) * 0.5:
                    weak.append((r.start, r.end, r.score))
                else:
                    s.media_holes.append((r.start, r.end))
                break
    return weak


def mark(tr: Transcript, p: FillerParams | None = None) -> FillerStats:
    """Transcript 의 각 Seg 에 kind/reason 을 채운다. 제자리 수정."""
    p = p or FillerParams()
    st = FillerStats()
    segs = tr.segs

    # 매번 다시 돌려도 같은 결과가 나오도록 초기화
    for s in segs:
        s.kind, s.reason, s.holes, s.media_holes = "speech", "", [], []

    # ── 1) 잔말 ──
    if p.enable_filler:
        for s in segs:
            if _is_filler(s, p):
                s.kind, s.reason = "filler", "잔말"

    # ── 2) NG 마커 ──
    if p.enable_ng:
        for i, s in enumerate(segs):
            if s.dropped:
                continue
            m = _has_ng_marker(s)
            if not m:
                continue
            # 마커 앞의 살아있는 세그먼트 하나를 실패 테이크로 본다
            for j in range(i - 1, max(-1, i - 1 - p.ng_lookahead), -1):
                if not segs[j].dropped:
                    segs[j].kind = "ng"
                    segs[j].reason = f"NG 신호 '{m}' 직전"
                    break
            if p.drop_marker_segment:
                s.kind, s.reason = "ng", f"NG 신호 '{m}'"

    # ── 3) 재테이크(연속 중복) ──
    if p.enable_ng:
        for i, s in enumerate(segs):
            if s.dropped:
                continue
            for j in range(i + 1, min(len(segs), i + 1 + p.ng_lookahead)):
                nxt = segs[j]
                if nxt.dropped:
                    continue
                hit, why = _is_retake(s.text, nxt.text, p)
                if hit:
                    s.kind, s.reason = "ng", why
                    break

    # ── 4) 살아남은 문장의 가장자리 잔말 다듬기 ──
    if p.enable_filler and p.trim_edges:
        for s in segs:
            if not s.dropped:
                st.trimmed += _trim_edges(s, p)

    # ── 5) 꼬리만 다시 말한 되풀이 ──
    st.tail_retakes = drop_tail_retakes(tr, p)

    # ── 6) 도입부 헛시작 ──
    st.trimmed += drop_false_start(tr)

    for s in segs:
        if s.kind == "filler":
            st.filler += 1
            st.dropped_us += s.dur
        elif s.kind == "ng":
            st.ng += 1
            st.dropped_us += s.dur
        else:
            st.dropped_us += sum(b - a for a, b in s.holes)
    return st


# 문장 **안쪽**에서 되풀이된 시도를 걷어낸다.
#
# whisper 는 말을 하다 끊고 다시 시작하면 조각을 여러 개로 뱉는데,
# build_sentences 가 그걸 한 문장으로 붙여버린다. 그러면 중복이 문장 안으로
# 들어가서, 문장끼리 비교하는 재테이크 검출(_is_retake)은 영원히 못 본다.
#
# 실측(2026-08-18 촬영본):
#   "왜 그런지랑 그 시기에 뭘 / 왜 그런지랑 / 왜 그런지랑 그 시기에 뭘 보고
#    버텨야 하는지 마지막에 말씀드릴게요."
# 앞의 두 시도(2.9초)가 그대로 살아남아 대표님이 직접 잘라내셨다.
# 낱말 두 개만 겹쳐도 되풀이로 보면 정상 문장을 부순다. 실측: "지금 나를 보고
# 온 사람이 없으면 앞으로도 나를 보고 올 사람이 없다는 겁니다" 에서 '나를 보고'
# 가 겹친다는 이유로 문장 한복판이 날아갔다 — 되풀이가 아니라 병렬 구조였다.
# 연속 세 낱말이 통째로 같은 경우는 우연히 잘 안 나온다.
INNER_MIN_BLOCK = 3      # 이만큼 낱말이 통째로 겹쳐야 되풀이로 본다
INNER_MAX_SKIP = 15      # 버려지는 시도가 이보다 길면 다른 말로 본다
INNER_MAX_SPAN_S = 8.0   # 되풀이 사이가 이보다 벌어지면 우연으로 본다


def drop_inner_restarts(tr: Transcript) -> int:
    """한 문장 안에서 같은 말로 다시 시작한 앞 시도를 잘라낸다.

    낱말 블록이 뒤에서 그대로 다시 나오면, 그 사이는 버린 시도다.
    가장 **뒤쪽** 되풀이를 진짜 발화로 보고 그 앞을 전부 판다.

    돌려주는 값: 잘라낸 문장 수.
    """
    hit = 0
    for s in tr.segs:
        if s.dropped or len(s.words) < INNER_MIN_BLOCK * 2:
            continue
        w = [x.text.strip().rstrip(".,?!") for x in s.words]
        n = len(w)
        # **문장 맨 앞에서** 다시 시작한 경우만 인정한다.
        #
        # 문장 한복판의 겹침은 되풀이가 아니라 병렬 구조인 일이 훨씬 많다.
        # 실측 오검출: "유치 쪽을 담당하는 노 / 유지 쪽을 담당하는 노 / 이 두
        # 개가 맞물려…" 에서 '쪽을 담당하는 노' 가 겹친다고 한쪽 노를 지워버려
        # 뒤의 '이 두 개' 가 가리킬 게 없어졌다. "수업만 잘하시는 분은 유지 쪽
        # 노만 / 상담만 잘하시는 분은 유치 쪽 노만" 도 같은 식으로 무너졌다.
        #
        # 되풀이를 놓치면 대표님이 한 번 자르면 되지만, 멀쩡한 절을 지우면
        # 뜻이 부서진다. 놓치는 쪽으로 기운다.
        best: tuple[int, int, int] | None = None      # (블록길이, i, j)
        for i in (0,):
            for j in range(i + 1, min(n, i + 1 + INNER_MAX_SKIP)):
                m = 0
                while (j + m < n and i + m < j
                       and w[i + m] == w[j + m] and w[i + m]):
                    m += 1
                if m < INNER_MIN_BLOCK:
                    continue
                if (s.words[j].start - s.words[i].start) > INNER_MAX_SPAN_S * US:
                    continue
                if best is None or (m, j) > (best[0], best[2]):
                    best = (m, i, j)
        if best is None:
            continue
        _, i, j = best
        s.holes.append((s.words[i].start, s.words[j].start))
        hit += 1
    return hit
