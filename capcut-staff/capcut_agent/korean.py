"""한국어 끊어읽기 판단.

자막을 글자수로만 끊으면 "영상 자동 / 편집에" 처럼 한 덩어리를 쪼갠다.
읽는 사람은 그 순간 문장을 다시 조립해야 해서 흐름이 끊긴다.

여기서는 어절 사이마다 **끊어도 되는 정도**를 점수로 매긴다. 높을수록 좋은
자리. 실제로 어디서 끊을지는 이 점수로 chunk() 가 DP 로 정한다.

형태소 분석기는 쓰지 않는다 (설치 부담 + 한국어 모델 용량). 대신 조사·어미·
의존명사 같은 **표층 단서**와 **발화 사이 쉼**을 함께 본다. 쉼은 화자가 실제로
끊어 읽은 자리라 어떤 규칙보다 정확할 때가 많다.
"""

from __future__ import annotations

import re

US = 1_000_000

# ── 끊으면 안 되는 자리 ───────────────────────────────────────────

# 의존명사. 혼자 못 서고 앞말에 붙는다. "할 수 / 있다" 처럼 끊으면 말이 부서진다.
#
# 한 글자짜리는 골라 넣었다. "바"(의존명사) 를 넣으면 "바로" 가 걸리고,
# "지" 를 넣으면 "지금" 이, "중" 을 넣으면 "중요한" 이 걸린다. 앞글자만 보고
# 판단하면 멀쩡한 낱말이 줄줄이 오탐된다.
BOUND_NOUNS = (
    "것", "수", "때", "때문", "줄", "뿐", "등", "및", "겸", "만큼", "대로",
    "따름", "나름", "가지", "차례", "동안", "무렵", "즈음", "이후", "이전",
)

# '것' 이 줄어든 꼴. 말할 때는 거의 이 꼴로 나온다 — "잘하고 있는 걸",
# "중요한 게", "할 거예요". 목록에 없어서 "있는 / 걸 많은 사람들이" 로
# 갈라졌다 (2026-09-16). 앞글자만 보면 '거기'·'게임' 이 걸리므로 통째로 맞춘다.
BOUND_CONTRACTED = frozenset((
    "거", "걸", "건", "게", "겁니다", "거예요", "거에요", "거죠", "거고", "거라",
    "거야", "걸로", "거라고", "거든요", "건데", "건데요", "거지", "거죠.",
))

# 의존명사 뒤에 붙을 수 있는 꼬리. 이 목록에 정확히 맞아야 의존명사로 인정한다.
_TAILS = frozenset((
    "", "이", "가", "은", "는", "을", "를", "에", "에서", "에도", "도", "만",
    "과", "와", "의", "로", "으로", "부터", "까지", "이다", "입니다", "이라",
    "이라고", "이랑", "조차", "마저", "밖에", "이나", "이야", "였다", "이었다",
    "들", "들이", "들을", "들은",
))

# 보조용언 어간. 앞 본용언에 붙어 하나의 서술어를 이룬다.
# 단독으로 쓰면 "오늘은"(오), "하지만"(하), "가장"(가) 이 전부 걸리므로
# **앞말이 보조적 연결어미로 끝날 때만** 본다.
AUX_VERB_STEMS = (
    "있", "없", "계시", "보", "주", "드리", "싶", "버리", "놓", "두", "가", "오",
    "말", "내", "대", "쌓", "빠지", "치우", "지",
    # "~게 되다", "~게 하다" 는 한국어에서 가장 흔한 보조용언 구성인데
    # 빠져 있었다. 그래서 "아시게 / 될 거예요" 가 갈라졌다.
    "되", "하",
)
# 본용언과 보조용언을 잇는 어미.
# "야" 를 빠뜨렸었다 — "~어야 하다/되다" 는 한국어에서 아주 흔한 구성인데
# 그 바람에 "그만둬야 / 하나" 가 갈라졌다.
_AUX_LINK = ("아", "어", "여", "해", "고", "게", "지", "야")

# 앞말에 붙어 하나의 서술어를 이루는 부정 표현.
# "실력이 없어서가 / 아닙니다" 처럼 끊으면 '~가 아니다' 가 두 동강 난다.
# '없' 을 빠뜨렸었다 — "잘 보여줄 수도 / 없습니다" 가 그대로 나왔다 (2026-09-16).
NEGATIVE_PREDICATES = ("아니", "않", "못하", "말고", "없이", "없")

# 관형사·수관형사. 반드시 뒤 체언과 붙는다. "이 / 사람" 은 끊으면 안 된다.
DETERMINERS = (
    "이", "그", "저", "요", "한", "두", "세", "네", "다섯", "여섯", "일곱",
    "여덟", "아홉", "열", "몇", "각", "매", "온", "모든", "어떤", "무슨", "웬",
    "새", "옛", "첫", "여러", "온갖", "갖은", "딴", "다른", "헌", "순",
    # 빠져 있어서 "줬었는데 어느 / 날", "연계된 건데 그런 / 건" 이 갈라졌다
    "어느", "그런", "이런", "저런", "아무", "그러한", "이러한",
)

# ── 끊어도 좋은 자리 ─────────────────────────────────────────────

# 부정 부사. 뒤 용언과 한 덩어리다 — "답이 / 안 나오는지" 로 끊으면
# '안 나오다' 라는 서술어가 두 동강 난다.
# '잘' 은 뺐다 (2026-09-16). 부정이 아니라 정도 부사라 **뒤**에서 끊는 게 문제지
# 앞에서 끊는 건 괜찮다. 여기 넣어 두니 앞에서도 못 끊고 뒤에서도 못 끊어서,
# 결국 "수업을 못하는 트레이너는 잘 / 보여줄 수도 없습니다" 가 나왔다.
NEGATION_ADVERBS = ("안", "못", "덜")

# 명사절·의문 어미. 여기서 절이 끝나므로 끊기 좋은 자리다.
# ("…답이 안 나오는지 / 정확하게 아시게 될 거예요")
NOMINAL_ENDINGS = ("는지", "은지", "ㄴ지", "을지", "ㄹ지", "는가", "은가",
                   "는데도", "다는", "라는", "던지", "든지")

# 연결어미. 절이 끝나는 자리라 자연스럽게 쉰다.
CONNECTIVE_ENDINGS = (
    "고", "며", "면서", "는데", "은데", "ㄴ데", "지만", "아서", "어서", "여서",
    "니까", "으니까", "므로", "다가", "거나", "든지", "라서", "면", "자",
    "도록", "게", "듯", "듯이", "처럼", "치고", "커녕", "든가",
)

# 절을 맺는 보조용언. 홀로 한 어절로 서면 거기서 절이 끝난 것이다.
# "부족한가 / 보다" 가 아니라 "부족한가 보다 / 자격증 하나 더" 로 끊어야 한다.
CLAUSE_END_AUX = ("보다", "같다", "싶다", "말다", "하다", "되다", "보네요",
                  "같아요", "싶어요", "봅니다", "합니다", "됩니다")

# 종결어미. 문장이 끝난 자리 — 가장 좋은 끊는 자리.
FINAL_ENDINGS = (
    "습니다", "ㅂ니다", "입니다", "됩니다", "합니다", "니다",
    "어요", "아요", "에요", "예요", "네요", "세요", "지요", "구요", "고요",
    "죠", "요", "다", "까", "나", "냐", "라", "자", "야",
)

# 조사. 체언이 끝나는 자리라 끊어도 무리가 없다.
# "든", "나", "며" 는 뺐다. 조사로도 쓰이지만 관형형·어간 끝소리와 겹쳐서
# ("만든", "하나", "하며") 오탐이 더 많다. 애매하면 안 끊는 쪽이 안전하다.
PARTICLES = (
    "은", "는", "이", "가", "을", "를", "에", "에서", "에게", "께", "한테",
    "으로", "로", "와", "과", "랑", "이랑", "도", "만", "까지", "부터",
    "보다", "마다", "조차", "마저", "밖에", "이나", "라고",
    "이라고", "에다", "에게서", "으로서", "로서", "으로써", "로써",
)

# 체언+조사 축약형. 조사 목록으로는 안 잡히지만 뒤에서 끊어도 되는 말들.
CONTRACTED = (
    "이건", "그건", "저건", "이게", "그게", "저게", "이건", "요건",
    "여긴", "거긴", "저긴", "난", "넌", "전", "걘", "얜", "쟨",
    "뭐가", "뭘", "누가", "언젠", "어딘",
    # '것을/것은' 이 줄어든 꼴 — 뒤에서는 끊어도 된다 ("잘하고 있는 걸 / 많은 사람들이")
    "걸", "건",
)

# 관형형. 뒤 체언을 꾸미므로 끊으면 안 된다.
#
# "하는", "만든" 처럼 조사 '는/은' 과 끝소리가 같아서 표층만으로는 구별이 안 된다
# ("오늘은" 은 체언+조사, "하는" 은 용언 관형형). 형태소 분석기 없이 가르려면
# 결국 자주 쓰이는 형태를 적어두는 수밖에 없다. 여기 없는 관형형은 놓친다.
ADNOMINAL_FORMS = (
    "하는", "되는", "있는", "없는", "가는", "오는", "보는", "주는", "받는",
    "쓰는", "먹는", "만드는", "만든", "된", "한", "드는", "나는", "사는",
    "아는", "모르는", "여는", "닫는", "넣는", "빼는", "쉬는", "찾는", "말하는",
    "같은", "다른", "좋은", "나쁜", "많은", "적은", "작은", "큰", "넓은",
    "좁은", "높은", "낮은", "빠른", "느린", "새로운", "어려운", "쉬운",
    "중요한", "필요한", "가능한", "다양한", "확실한", "정확한", "간단한",
    "라는", "이라는", "라던", "던",
)
# 접미 패턴으로도 잡는다 (사전에 없는 동사까지 커버)
_ADNOMINAL_SUFFIX = ("하는", "되는", "시는", "지는", "라는", "당한", "스러운")

# 뒤 서술어를 꾸미는 부사. 뒤에서 끊으면 꾸밈 대상과 갈라진다.
ADVERBS = (
    "바로", "아주", "매우", "정말", "진짜", "너무", "가장", "제일", "다시",
    "좀", "잘", "더", "또", "먼저", "특히", "이미", "아직", "벌써", "곧",
    "항상", "늘", "자주", "가끔", "전혀", "결코", "절대", "반드시", "꼭",
    "직접", "따로", "함께", "같이", "빨리", "천천히", "계속", "그냥", "훨씬",
    "많이", "적게", "없이", "달리", "멀리", "널리", "깊이", "굳이", "온통",
    "제대로", "충분히", "완전히", "거의", "대충", "일단", "우선", "역시",
)
# '~히' 로 끝나면 거의 부사다 (확실히, 정확히, 조용히, 특히)
_ADVERB_SUFFIX = ("히",)

# 앞 체언에 붙어 하나의 후치사구를 이루는 말들.
# "영상 편집에 / 대해" 처럼 끊으면 '~에 대해' 가 두 동강 난다.
# 반대로 이 말들 **뒤에서는** 구가 끝나므로 끊어도 좋다.
POSTPOSITIONS = (
    "대해", "대해서", "대하여", "관해", "관해서", "관하여",
    "위해", "위해서", "위하여", "통해", "통해서", "통하여",
    "의해", "의해서", "의하여", "따라", "따라서", "비해", "비해서",
    "향해", "향해서", "때문에", "덕분에", "탓에", "불구하고",
    "대신", "비롯한", "비롯해", "포함한", "제외한", "가운데", "사이에",
)

# 접속부사. 새 절을 여는 말이라 이 **앞에서** 끊는 게 자연스럽다.
CONJUNCTIONS = (
    "그래서", "그러나", "그런데", "근데", "하지만", "그리고", "또한", "또",
    "즉", "결국", "따라서", "그러므로", "왜냐하면", "다만", "물론", "게다가",
    "심지어", "오히려", "반면", "예를", "가령", "만약", "만일", "그러면",
    "그럼", "그러니까", "아무튼", "어쨌든", "특히",
)

_SENT_END = re.compile(r"[.!?…]+$")
_COMMA_END = re.compile(r"[,·]$")
_HANGUL = re.compile(r"[가-힣]")

# 점수 기준값. chunk() 가 (GOOD - score) 를 비용으로 쓴다.
GOOD = 100

# 끊는 자리 점수표. 숫자를 코드 한복판에 박아두면 대표님 손질본에 맞춰
# 다시 잴 수가 없다 — tools/test_breaks.py 로 채점하며 고친다.
#
# 2026-09-16 대표님 손질본 두 편(풀링3·0916, 327문장)에 대고 격자 탐색으로 다시 잡았다.
# 한 편으로 고르고 다른 편으로 검증해 둘 다 오른 조합만 썼다.
#   완전일치  풀링3 125→132 / 0916 110→112 · 경계 F1 56.1→62.5 / 64.7→69.2
# 바뀐 건 셋 — 조사 뒤 55→40, 쉼표 뒤 70→95, 18자 넘는 줄에 글자당 3점.
# (연결어미 점수를 올리면 나아질 줄 알았는데 효과가 없었다. 규칙이 다음 절 주어를
#  앞줄로 끌어오던 건 연결어미가 약해서가 아니라 조사 뒤가 너무 후했고 줄을 꽉
#  채우려 했기 때문이다.)
W = {
    # 끊으면 안 되는 자리
    "bound_noun": -110, "postposition_next": -110, "negation_adv_next": -110,
    "neg_pred_next": -110, "aux_pair": -70, "determiner": -120,
    "adverb": -70, "adnominal": -60, "gen_ui": -90, "noun_noun": -55,
    # 끊어도 좋은 자리
    "sent_end": 100, "comma": 95, "clause_aux": 75, "postposition_prev": 50,
    "nominal": 80, "final": 85, "connective": 60, "particle": 40,
    "conj_next": 65, "gap_max": 45,
    # 줄 비용
    "line_cost": 12, "min_chars": 8, "short_w": 12,
    "soft_max": 18, "soft_w": 3, "comma_short_ok": 0,
}


def _strip_punct(w: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]+$", "", w)


def _endswith_any(w: str, cands: tuple[str, ...]) -> bool:
    return any(w.endswith(c) for c in cands)


def is_bound_noun(word: str) -> bool:
    """의존명사로 시작하는 어절인가.

    앞글자만 맞으면 안 되고, 남은 꼬리가 조사/서술격이어야 한다.
    ("것입니다" 는 맞고, "데이터" 는 아니다)
    """
    w = _strip_punct(word)
    if w in BOUND_CONTRACTED:
        return True
    for b in BOUND_NOUNS:
        if w.startswith(b) and w[len(b):] in _TAILS:
            return True
    return False


def _syllable_core(ch: str) -> tuple[int, int] | None:
    """완성형 한글 한 글자의 (초성, 중성). 받침은 무시."""
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return code // 588, (code % 588) // 28


def starts_with_stem(word: str, stem: str, max_extra: int | None = 1) -> bool:
    """word 가 어간 stem 으로 시작하는가. **받침이 붙어도 인정한다.**

    한글은 startswith 로 어간을 못 찾는다. "될" 은 "되" 로 시작하지 않는다 —
    유니코드에서 둘은 별개의 완성형 글자다(될 = ㄷ+ㅚ+ㄹ, 되 = ㄷ+ㅚ).
    그래서 "아시게 / 될" 이 보조용언 검사를 통과해버렸다.
    어간의 마지막 글자는 초성·중성만 맞으면 같은 어간으로 본다.
    """
    if word.startswith(stem):
        return True
    # 받침만 다른 경우는 **짧은 활용형에만** 인정한다.
    # 안 그러면 "이야기하겠습니다" 가 어간 '있'(ㅇ+ㅣ)과 초성·중성이 같다는
    # 이유로 보조용언 취급된다. 보조용언은 '될·볼·줄' 처럼 짧게 붙는다.
    if max_extra is not None and len(word) > len(stem) + max_extra:
        return False
    if len(word) < len(stem) or not word.startswith(stem[:-1]):
        return False
    a, b = _syllable_core(word[len(stem) - 1]), _syllable_core(stem[-1])
    return a is not None and a == b


def is_aux_verb_pair(prev: str, nxt: str) -> bool:
    """prev(본용언) + nxt(보조용언) 한 덩어리인가. "정리해 / 볼" 같은 자리."""
    p, n = _strip_punct(prev), _strip_punct(nxt)
    # 한 글자 보조용언도 잡아야 한다 — "아시게 / 될", "해 / 볼", "먹어 / 줄".
    # len(n) >= 2 로 막아뒀더니 '될' 이 빠져서 서술어가 갈라졌다.
    if not n or not p:
        return False
    if not p.endswith(_AUX_LINK):
        return False
    return any(starts_with_stem(n, s) for s in AUX_VERB_STEMS)


def is_adverb(word: str) -> bool:
    w = _strip_punct(word)
    return w in ADVERBS or (len(w) >= 2 and w.endswith(_ADVERB_SUFFIX))


def is_adnominal(word: str) -> bool:
    w = _strip_punct(word)
    return w in ADNOMINAL_FORMS or (len(w) >= 3 and w.endswith(_ADNOMINAL_SUFFIX))


def break_score(prev: str, nxt: str, gap_us: int = 0) -> int:
    """어절 prev 와 nxt 사이에서 끊었을 때의 점수. 높을수록 좋은 자리."""
    p_raw = prev.strip()
    p = _strip_punct(p_raw)
    n = _strip_punct(nxt.strip())
    if not p:
        return 0

    score = 0
    sent_end = bool(_SENT_END.search(p_raw))

    # 부사·관형형은 뒤 낱말을 꾸민다. 끝소리가 조사와 겹쳐도
    # ("바로"의 로, "하는"의 는) 조사 대접을 하면 안 된다.
    # 뒤 낱말과 한 덩어리라 보너스를 주면 안 되는 자리.
    #
    # 보조용언 앞을 빠뜨렸었다: "그만둬야 / 하나" 에서 '야' 가 종결어미로도
    # 등록돼 있어 +85 를 받아버렸고, 보조용언 감점(-70)을 상쇄해 +15 가 됐다.
    binds_next = (
        not sent_end and (
            is_adverb(p) or is_adnominal(p) or p in DETERMINERS
            or (len(p) >= 2 and p.endswith("의"))
            or is_aux_verb_pair(p_raw, nxt)
        )
    )

    # ── 끊으면 안 되는 자리부터 (음수가 크다) ──
    if n and is_bound_noun(n):
        # 의존명사 앞. "할 / 수 있다", "가는 / 것" 같은 파열을 막는다.
        score += W["bound_noun"]
    if n in POSTPOSITIONS:
        # 후치사 앞. "편집에 / 대해" 는 '~에 대해' 를 가른 것이다.
        score += W["postposition_next"]
    if n in NEGATION_ADVERBS:
        # 부정 부사 앞. "답이 / 안 나오는지" 는 서술어를 가른 것이다.
        score += W["negation_adv_next"]
    # 부정 서술어 앞. "없어서가 / 아닙니다" 는 '~가 아니다' 를 가른 것이다.
    # "아닙니다" 는 '아니' 로 시작하지 않는다 — 아니+ㅂ니다 가 축약돼 '닙' 이
    # 됐다. 그래서 초성·중성으로 맞춰야 한다.
    # 어미가 붙어봐야 3글자다 (아닙니다·않습니다·못합니다). 길이를 안 막으면
    # "안녕하세요" 가 '않'(ㅇ+ㅏ) 과 초성·중성이 같다는 이유로 걸린다.
    # 문장이 끝난 자리는 애초에 따질 것도 없다.
    if not sent_end and n and any(starts_with_stem(n, x, max_extra=3)
                                  for x in NEGATIVE_PREDICATES):
        score += W["neg_pred_next"]
    if is_aux_verb_pair(p_raw, nxt):
        # 본용언 + 보조용언. "편집해 / 줍니다" 는 서술어 하나를 가른 것이다.
        score += W["aux_pair"]
    # "~ㄹ 수 / 있다", "~ㄹ 줄 / 모른다" — 의존명사 **뒤**도 막아야 한다.
    # 앞만 막아 두었더니 촬영대본이 "오늘 바로 바뀔 수 / 있는 방법을" 로 갈라졌다
    # (2026-09-16).
    if p in ("수", "줄", "리") and n and n[0] in "있없알모":
        score += W["bound_noun"]
    if p in DETERMINERS:
        # 관형사 뒤. "이 / 사람" 은 절대 안 된다.
        score += W["determiner"]
    elif is_adverb(p):
        score += W["adverb"]
    elif is_adnominal(p):
        score += W["adnominal"]
    if len(p) >= 2 and p.endswith("의") and not sent_end:
        # 관형격 조사 — 뒤 체언과 한 덩어리다
        score += W["gen_ui"]

    # ── 끊어도 좋은 자리 ──
    if sent_end:
        score += W["sent_end"]
    elif _COMMA_END.search(p_raw):
        score += W["comma"]
    elif p in CLAUSE_END_AUX:
        # 절을 맺는 보조용언 뒤. "…부족한가 보다 / 자격증 하나 더 따고"
        score += W["clause_aux"]
    elif p in POSTPOSITIONS:
        # 후치사구가 끝난 자리 — 여기서는 끊어도 좋다
        score += W["postposition_prev"]
    elif binds_next:
        pass                                    # 꾸밈말에는 보너스를 주지 않는다
    elif _endswith_any(p, NOMINAL_ENDINGS) and len(p) >= 3:
        # 명사절이 끝난 자리. "…답이 안 나오는지 / 정확하게…"
        score += W["nominal"]
    elif _endswith_any(p, FINAL_ENDINGS) and len(p) >= 3:
        score += W["final"]
    elif _endswith_any(p, CONNECTIVE_ENDINGS) and len(p) >= 3:
        # 연결어미 뒤 — 절이 끝난 자리.
        #
        # 대표님은 여기서 끊고 **다음 절의 주어를 다음 줄 머리에** 둔다.
        # 조사 뒤(체언이 끝난 자리)와 점수가 거의 같으면 줄을 꽉 채우려고
        # 다음 절 첫 낱말을 앞줄로 끌어온다 — 실측(2026-09-16):
        # "감정 다 빼고 제가 / 트레이너부터", "관리자를 거쳐 대표가 / 되고"
        # 대표님 손질은 "감정 다 빼고 / 제가 트레이너부터".
        score += W["connective"]
    elif p in CONTRACTED or (_endswith_any(p, PARTICLES) and len(p) >= 2):
        score += W["particle"]

    # ── 명사 + 명사 붙임 위험 ──
    # 조사도 어미도 안 붙은 맨 체언 뒤는 합성어 한복판일 수 있다.
    # ("영상 자동 / 편집" 이 여기서 걸린다)
    if (score <= 0 and _HANGUL.search(p) and _HANGUL.search(n)
            and not binds_next and p not in POSTPOSITIONS and p not in CONTRACTED
            and not _endswith_any(p, PARTICLES)
            and not _endswith_any(p, FINAL_ENDINGS)
            and not _endswith_any(p, CONNECTIVE_ENDINGS)):
        score += W["noun_noun"]

    # ── 접속부사 앞 ──
    # 새 절이 시작되는 지점이라 여기서 끊으면 오히려 읽기 좋다.
    if n in CONJUNCTIONS:
        score += W["conj_next"]

    # ── 실제로 쉰 자리 ──
    # 화자가 숨을 고른 지점은 어떤 규칙보다 믿을 만하다.
    if gap_us > 0:
        score += min(W["gap_max"], int(gap_us / 1000 / 8))

    return score


def chunk(
    items: list[str],
    max_chars: int,
    gaps_us: list[int] | None = None,
    line_cost: int | None = None,
    min_chars: int | None = None,
) -> list[list[int]]:
    """어절을 자막 줄로 나눈다. 인덱스 묶음 리스트를 돌려준다.

    글자수만 맞추는 게 아니라 **끊어읽기 점수**까지 함께 최소화한다.
    줄이 하나 늘어나는 비용(line_cost)보다 나쁜 자리에서 끊는 비용이 크면
    줄을 하나 더 쓴다 — 억지로 두 줄에 욱여넣지 않는다.
    """
    n = len(items)
    if n == 0:
        return []
    gaps_us = gaps_us or [0] * n
    line_cost = W["line_cost"] if line_cost is None else line_cost
    min_chars = W["min_chars"] if min_chars is None else min_chars

    # lens[i][j] = items[i..j-1] 을 한 줄로 붙였을 때 글자수
    def line_len(i: int, j: int) -> int:
        return sum(len(items[k]) for k in range(i, j)) + (j - i - 1)

    INF = float("inf")
    dp = [INF] * (n + 1)
    prev = [-1] * (n + 1)
    dp[0] = 0.0

    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            ln = line_len(i, j)
            if ln > max_chars and j - i > 1:
                break                      # 더 늘려봐야 계속 초과
            if dp[i] == INF:
                continue

            cost = dp[i] + line_cost
            # 너무 짧은 줄은 보기 나쁘다.
            #
            # 마지막 줄을 예외로 뒀더니 "아닙니다." (5자) 같은 토막이
            # 한 화면을 차지했다. 위치와 상관없이 짧으면 벌점을 준다.
            # 다만 쉼표로 끝나는 나열 항목은 짧아도 된다 — 대표님은
            # "센터 직원, / 중간관리자, / 그리고 센터 대표." 처럼 한 항목씩 띄운다.
            comma_item = W["comma_short_ok"] and _COMMA_END.search(items[j - 1])
            if ln < min_chars and not comma_item:
                cost += (min_chars - ln) * W["short_w"]
            # 한 줄이 길어질수록 조금씩 벌점. 넘치지 않아도 절이 끝나면 끊는 쪽으로.
            if ln > W["soft_max"]:
                cost += (ln - W["soft_max"]) * W["soft_w"]
            # 이 줄 다음에서 끊는 비용
            if j < n:
                cost += max(0, GOOD - break_score(items[j - 1], items[j], gaps_us[j - 1]))
            if cost < dp[j]:
                dp[j] = cost
                prev[j] = i

    if dp[n] == INF:                       # 한 어절이 max_chars 보다 긴 경우 등
        return [list(range(n))]

    out: list[list[int]] = []
    j = n
    while j > 0:
        i = prev[j]
        out.append(list(range(i, j)))
        j = i
    out.reverse()
    return out
