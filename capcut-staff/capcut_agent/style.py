"""편집 스타일 프로필 — 사용자의 실제 편집본에서 뽑은 값.

추정이 아니라 `0811` 드래프트(6분11초, 101클립·262자막) 실측이다.
기본값을 내가 정하지 않고 사용자가 실제로 해온 대로 맞춘다.

    배속        전 구간 1.2배
    컷          78곳 / 중앙 0.64초 (짧은 텀 위주) / 클립 평균 4.41초
    확대        101개 중 38개, 1.0~1.35배
    자막        평균 12.4자 · 1.42초 · y=-0.832
      일반      흰색 size 7, 배경 검정 60%      (check_flag 23)
      강조      size 13 동해독도, 외곽선+그림자  (check_flag 47)
                부정 #ec1d1d · 긍정 #247bff
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# 강조 자막 폰트 "동해 독도". 캡컷이 효과 리소스로 내려받아 캐시에 둔다.
#
# 주의: 이 경로는 **존재 확인을 하면 안 된다.** 캡컷이 JSON 에 적는 이름은
# NFC(완성형 '동')인데 디스크에 실제로 저장된 이름은 NFD(자모 분리 '동')다.
# Windows 는 둘을 다른 파일로 보므로 os.path.exists 가 False 를 돌려준다.
# 캡컷은 font_resource_id 로 폰트를 찾으니 경로가 어긋나도 알아서 해결한다.
# (여기서 존재 확인을 했다가 강조 자막이 전부 기본 폰트로 나갔다)
EMPHASIS_FONT_ID = "7347588288930124290"
EMPHASIS_FONT_NAME = "동해 독도"
EMPHASIS_FONT = ("C:/Users/허재윤/AppData/Local/CapCut/User Data/Cache/effect/"
                 "7347588288930124290/3d2b923c421a42ce29e2ef74b4b51249/동해 독도.ttf")


def resolve_on_disk(path: str) -> str:
    """정규화 차이를 감안해 디스크의 실제 파일을 찾는다 (Pillow 렌더링용).

    캡컷에 넘기는 경로와 달리, 직접 글자를 그릴 때는 진짜로 열 수 있어야 한다.
    """
    import unicodedata

    p = Path(path)
    if p.exists():
        return str(p)
    for form in ("NFC", "NFD"):
        q = Path(unicodedata.normalize(form, path))
        if q.exists():
            return str(q)
    # 폴더를 훑어 이름이 정규화만 다른 파일을 찾는다
    try:
        target = unicodedata.normalize("NFC", p.name)
        for f in p.parent.iterdir():
            if unicodedata.normalize("NFC", f.name) == target:
                return str(f)
    except OSError:
        pass
    return ""


def capcut_system_font() -> str:
    """캡컷 내장 기본 폰트. 버전이 올라가면 경로가 바뀌므로 찾아서 쓴다."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
    cands = sorted(base.glob("*/Resources/Font/SystemFont/en.ttf"), reverse=True)
    return str(cands[0]).replace("\\", "/") if cands else ""


def emphasis_font() -> str:
    """캡컷 드래프트에 적을 강조 폰트 경로. 존재 확인을 하지 않는다 (위 설명 참고)."""
    return EMPHASIS_FONT


def emphasis_font_for_pillow() -> str:
    """직접 글자를 그릴 때 쓸, 실제로 열리는 경로."""
    return resolve_on_disk(EMPHASIS_FONT)


# ── 강조 판정 ────────────────────────────────────────────────────
#
# 사용자는 부정적인 말에 빨강, 긍정적인 말에 파랑을 쓴다.
# 문장 전체 뜻으로 판단해야 한다 — 낱말 하나로는 뒤집힌다
# ("힘들지 않습니다" 는 부정어가 들어 있지만 긍정문이다).

# 점수가 높을수록 강한 신호. 흔한 낱말은 낮게 준다 —
# "준비", "유지" 같은 말은 이 사람 대본에 계속 나와서 변별력이 없다.
NEGATIVE = {
    "그만두": 3, "포기": 3, "실패": 3, "무너": 3, "위험": 3, "한숨": 3,
    "새어나": 3, "빠져나": 3, "구멍": 3, "함정": 3, "착각": 3, "막막": 3,
    "손해": 3, "헤매": 3, "멈춰": 3, "텅": 3, "죽는다": 3,
    "문제": 2, "실수": 2, "힘들": 2, "어렵": 2, "불안": 2, "답답": 2,
    "놓치": 2, "떨어지": 2, "줄어들": 2, "비어": 2, "정체": 2,
    "없어서": 2, "아닙니다": 2, "안 됩니다": 2,
    # 2026-09-16 풀링3(대표님 완성본)에서 놓친 줄로 보탰다 — 주제 낱말은 빼고
    # 어느 원고에나 나올 말만. 0818 영상은 검증용으로만 썼다.
    "퇴사": 3, "소홀": 3, "쉽지 않": 3, "안타깝": 2, "싫어": 2,
    "못하": 2, "못 ": 2, "없었": 2, "없다는": 2, "없잖": 2,
    "줄고": 2, "줄어": 2, "내려가": 2, "빠지면": 2, "빠져": 2,
    "끝이고": 2, "끝입니다": 2, "몰라서": 2, "가만히": 2, "운이 없": 3,
}
POSITIVE = {
    "선순환": 3, "확 달라": 3, "훨씬": 3, "바로 시작": 3, "해결책": 3,
    "성공": 3, "성장": 3, "돌파": 3, "핵심": 3,
    "쉬워": 2, "해결": 2, "빨라": 2, "빠르게": 2, "늘어": 2, "올라": 2,
    "바뀝니다": 2, "달라집니다": 2, "채워": 2, "흐름": 2, "안정": 2,
    "여유": 2, "확실": 2,
    # 2026-09-16 보탬 (위와 같은 기준)
    "좋습니다": 3, "잘 버는": 3, "벌립니다": 3, "행복": 3, "움직여": 2,
    "따라갑니다": 3, "달라질": 2, "만들 수 있": 2, "힘이에요": 2, "힘이고": 2,
}

# 이 비율만 강조로 남긴다. 실측: 262개 중 62개 = 24%.
# 키워드가 걸린다고 다 강조하면 강조가 강조가 아니게 된다.
EMPHASIS_RATIO = 0.24
# 부정어가 뒤에 붙으면 뜻이 뒤집힌다
NEGATION_TAIL = ("않습니다", "않아요", "않고", "않은", "않는", "말고", "아니라")

_STRIP = re.compile(r"[^가-힣A-Za-z0-9 ]")


def _flat(text: str) -> str:
    return _STRIP.sub(" ", text)


def _negated(t: str, at: int, word: str, window: int = 6) -> bool:
    """그 낱말 바로 뒤에 부정 표현이 붙었는가.

    문장 전체를 뒤집으면 안 된다. "여유 있을 때 준비해 두면 흔들리지 않고" 는
    '흔들리지'만 뒤집히는 것이지 '여유'까지 부정이 되는 게 아니다.

    어미가 붙어 길이가 들쭉날쭉하므로("힘들지 않습니다", "힘들진 않아요")
    통째로 맞추지 않고 부정을 나타내는 글자만 찾는다.
    """
    tail = t[at + len(word): at + len(word) + window]
    # 앞에 붙은 부정도 본다 — "월급이 안 무너져요" 는 '무너' 가 들어 있지만 좋은 말이다.
    head = t[max(0, at - 2): at]
    return ("않" in tail or "못" in tail or "아니" in tail
            or head in ("안 ", "못 "))


def score(text: str) -> tuple[str, int]:
    """문장의 (성격, 강도). 강도가 0 이면 신호 없음."""
    t = _flat(text)
    neg = pos = 0
    for table, same, other in ((NEGATIVE, "neg", "pos"), (POSITIVE, "pos", "neg")):
        for w, v in table.items():
            at = t.find(w)
            if at < 0:
                continue
            flipped = _negated(t, at, w)
            side = other if flipped else same
            if side == "neg":
                neg += v
            else:
                pos += v
    if neg > pos:
        return "neg", neg
    if pos > neg:
        return "pos", pos
    return "normal", 0


def classify(text: str) -> str:
    """단일 문장 판정. 비율 제한 없이 신호만 본다."""
    return score(text)[0]


def load_emphasis_plan(path: str | Path) -> dict[str, str]:
    """사람이(또는 내가) 대본을 읽고 정한 강조 계획을 불러온다.

    키워드 매칭은 낱말만 본다. "지금이 가장 위험한 시기" 가 왜 중요한지,
    어디가 이 영상의 전환점인지는 **글 전체를 읽어야** 안다. 그건 규칙으로
    안 되니, 읽고 판단한 결과를 파일로 받아 그대로 쓴다.

    형식: {"문장 앞부분": "neg" | "pos" | "normal", ...}
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {_flat(k)[:24]: v for k, v in data.items() if v in ("neg", "pos", "normal")}


def apply_plan(texts: list[str], plan: dict[str, str]) -> list[str]:
    """계획대로만 강조한다. 계획에 없는 문장은 전부 일반.

    자동 판정으로 빈자리를 채우면 안 된다. 계획(17문장)에 자동(17문장)이
    더해져 강조가 두 배가 됐던 적이 있다. 계획을 줬다는 건 "여기만" 이라는 뜻이다.
    """
    return [plan.get(_flat(t)[:24], "normal") for t in texts]


def pick_emphasis(texts: list[str], ratio: float = EMPHASIS_RATIO) -> list[str]:
    """문장 목록에서 **가장 강한 것만** 강조로 남기고 나머지는 normal 로.

    키워드가 걸렸다고 전부 강조하면 화면의 절반이 빨강·파랑이 된다.
    사람은 그렇게 편집하지 않는다 — 실측도 24% 였다.
    """
    scored = [score(t) for t in texts]
    cap = max(1, int(round(len(texts) * ratio)))
    order = sorted((i for i, (k, s) in enumerate(scored) if s > 0),
                   key=lambda i: -scored[i][1])[:cap]
    keep = set(order)
    return [scored[i][0] if i in keep else "normal" for i in range(len(texts))]


LEARNED_RATIO = 0.55
WINDOW_FLOOR = 0.55      # 구간마다 적어도 이만큼(평균 대비)은 칠한다 — 앞부분이 비는 걸 막는다     # 학습 판별기로 고를 때 문장 비율 (뒤에서 _cap_emphasis 가 줄 26% 로 줄인다)
_MODEL: dict = {}


def pick_emphasis_learned(texts: list[str], ratio: float = LEARNED_RATIO) -> list[str]:
    """사전 점수 + 대표님 완성본으로 학습한 판별기로 강조 문장을 고른다.

    tools/train_emphasis.py 가 만든 모델이 없으면 사전만 쓰는 pick_emphasis 로 돌아간다.
    빨/파는 사전이 판정하면 사전을, 못 하면 판별기를 따른다 (사전 쪽이 더 정확하다 90% vs 78%).
    """
    import pickle
    from pathlib import Path
    if "m" not in _MODEL:
        import os
        p = Path(os.environ.get("CAPCUT_EMPH_MODEL")
                 or Path(__file__).parent / "templates" / "emphasis_model.pkl")
        try:
            _MODEL["m"] = pickle.loads(p.read_bytes())
        except Exception:                            # noqa: BLE001
            _MODEL["m"] = None
    m = _MODEL["m"]
    if m is None or not texts:
        return pick_emphasis(texts)
    X = m["vec"].transform(texts)
    prob = m["pick"].predict_proba(X)[:, 1]
    pneg = m["kind"].predict_proba(X)[:, 1]
    scored = [score(t) for t in texts]
    s = [p + 0.35 * min(d[1], 3) / 3 for p, d in zip(prob, scored)]
    n = int(round(len(texts) * ratio))

    # ── 한쪽으로 몰리지 않게 구간을 나눠 고른다 ──
    #
    # 영상 전체에서 점수순으로만 뽑으면 센 말이 모인 뒷부분에 색이 쏠린다 —
    # 2026-10-05 촬영본에서 앞 2분 색 비율이 1% 였다(대표님 완성본은 12~27%).
    # 그래서 구간마다 최소 몫을 먼저 채우고, 남은 자리를 전체 점수로 채운다.
    top: set[int] = set()
    win = max(12, len(texts) // 5)
    for a in range(0, len(texts), win):
        idx = list(range(a, min(a + win, len(texts))))
        floor = int(round(len(idx) * ratio * WINDOW_FLOOR))
        for i in sorted(idx, key=lambda i: -s[i])[:floor]:
            top.add(i)
    for i in sorted(range(len(texts)), key=lambda i: -s[i]):
        if len(top) >= n:
            break
        top.add(i)
    out = []
    for i, (k, _) in enumerate(scored):
        if i not in top:
            out.append("normal")
        else:
            out.append(k if k != "normal" else ("neg" if pneg[i] > 0.5 else "pos"))
    return out


# ── 용도별 프리셋 ────────────────────────────────────────────────
#
# 배속을 하나로 못 박으면 안 된다. 같은 사람이 만드는 영상이라도 **파는 것**과
# **끌어오는 것**은 성질이 다르다.
#
#   유료 본편은 1.0× — 돈 내고 보는 사람은 이해가 목적이라 빠르면 안 되고,
#   총 길이 자체가 상품 가치다 (10시간을 8시간으로 줄이면 오히려 손해).
#   유튜브는 1.2× — 이탈을 막아야 하니 밀도가 높아야 한다.
#
# 2026-08-16 확정.

@dataclass
class Preset:
    key: str
    label: str
    speed: float
    note: str


PRESETS: dict[str, Preset] = {
    "youtube": Preset(
        "youtube", "유튜브 (풀링·키·파트 0)", 1.2,
        "이탈을 막아야 하므로 밀도를 높인다"),
    "vod": Preset(
        "vod", "VOD 유료 본편 (파트 1~4)", 1.0,
        "이해가 목적이고 총 길이가 상품 가치라 배속을 걸지 않는다"),
}
DEFAULT_PRESET = "youtube"


def get_preset(key: str) -> Preset:
    return PRESETS.get((key or "").strip().lower(), PRESETS[DEFAULT_PRESET])


@dataclass
class FramingPlan:
    """4K 원본에서 뽑아낼 프레이밍. zoom 은 캡컷 clip.scale 값."""
    name: str
    zoom: float
    offset_y: float = 0.0     # 화면 절반높이 기준. + 가 위쪽.


@dataclass
class StyleProfile:
    """사용자 편집 스타일. 전부 실측값이 기본값이다."""

    # ── 속도 ──
    #
    # 여기 못 박지 않는다. 용도에 따라 갈린다 (PRESETS 참고).
    # StyleProfile.for_preset("vod") 처럼 만들어 쓴다.
    preset: str = DEFAULT_PRESET
    speed: float = PRESETS[DEFAULT_PRESET].speed

    # ── 컷 ──
    #
    # 전부 실측으로 정했다. 원본 오디오의 dB 분포와 사용자가 실제로 자른
    # 구간을 겹쳐 보니 이렇게 나왔다.
    #
    #   · -40dB 이 무음/발화를 가장 깨끗하게 가른다
    #     (-32dB 는 살릴 말까지 무음으로 잡고, -45dB 는 진짜 정적을 놓친다)
    #   · 정적이 길수록 잘라낸다: 1~2초 91% · 2초+ 95% · 0.2~0.3초 65%
    #   · 짧은 호흡은 남긴다 — 남긴 구간의 10.3% 가 무음이다.
    #     즉 정적을 **없애는** 게 아니라 **짧게 줄이는** 편집이다.
    #     그래서 pad(앞뒤로 남길 여백)가 사실상 '허용 정적 길이/2' 역할을 한다.
    # 아래 값은 사용자 실제 컷과 대조해 격자탐색으로 고른 것이다.
    # -38dB / 0.28s / pad 0.08 → 122.0초 제거 (실제 123.2초와 거의 일치)
    noise_db: float = -38.0
    min_silence_s: float = 0.28      # 이보다 긴 정적은 손댄다
    pad_s: float = 0.08              # 말 사이에 0.16초쯤 남긴다
    min_keep_s: float = 0.40

    # ── 자막 ──
    subtitle_y: float = -0.832
    # 실측: 평균 12.4자 · 중앙 12자 · **최대 23자**.
    # 평균에 맞춰 14자로 조였더니 좋은 자리에서 끊을 여유가 없어서
    # "…시기일 수 / 있습니다" 처럼 서술어가 두 동강 났다. 한도는 최대치에
    # 맞추고, 실제 길이는 끊어읽기 판단이 알아서 짧게 가져간다.
    # 한도를 실측 **최대치**(23자)에 맞춘다. 평균(12.4자)에 맞춰 조였더니
    # "…답이 / 안 나오는지" 처럼 서술어가 갈라졌다 — 좋은 자리에서 끊으려면
    # 여유가 있어야 한다. 실제 길이는 끊어읽기 판단이 알아서 짧게 가져간다.
    subtitle_max_chars: int = 23
    normal_size: float = 7.0
    emphasis_size: float = 13.0
    color_normal: tuple[float, float, float] = (1.0, 1.0, 1.0)
    color_neg: str = "#ec1d1d"
    color_pos: str = "#247bff"

    # ── 확대 ──
    # 사용자 확인: 보통 1.2배, 필요하면 1.4배까지. (실측 최대 1.35)
    framings: list[FramingPlan] = field(default_factory=lambda: [
        FramingPlan("풀샷", 1.00, 0.00),
        FramingPlan("미디엄", 1.20, 0.06),
        FramingPlan("클로즈업", 1.40, 0.12),
    ])
    zoom_ratio: float = 0.38         # 실측 101개 중 38개에 확대

    @classmethod
    def for_preset(cls, key: str) -> "StyleProfile":
        """용도에 맞는 프로필. 지금은 배속만 갈리지만, 나중에 컷 성향이나
        자막 크기가 갈려도 여기만 손보면 된다."""
        p = get_preset(key)
        return cls(preset=p.key, speed=p.speed)


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore
