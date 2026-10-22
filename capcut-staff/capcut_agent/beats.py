"""비트 — 전체 화면에 한 문장만 크게 띄우는 리듬 장치.

강의 장표(제목 + 불릿)가 **아니다.** 풀링콘텐츠는 넓고 가볍게 닿아야 하는
층이라, 강의처럼 보이는 순간 키콘텐츠가 올라설 계단이 사라진다. 여기서
필요한 건 권위가 아니라 **화면이 단조롭지 않게 만드는 박자**다.

그래서 규칙이 단순하다 — 한 번에 한 생각, 1.5~2.5초, 도입부에는 없음.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import korean
from .style import emphasis_font_for_pillow

US = 1_000_000

BG = (10, 10, 10)
FG = (255, 255, 255)
DIM = (122, 122, 122)

# 도입부에는 아무것도 얹지 않는다. 시작하자마자 화면이 덮이면
# 무슨 영상인지 파악하기 전에 시선을 뺏긴다.
OPENING_GUARD_US = 8_000_000
MIN_APART_US = 15_000_000     # 비트끼리 이만큼은 떨어뜨린다
SECTION_APART_US = 6_000_000  # 차례 카드끼리는 이만큼만 떨어지면 된다 (뼈대라 촘촘해도 된다)


@dataclass
class Beat:
    """타임라인(배속 적용 뒤) 좌표에 놓이는 비트 한 장."""
    at_us: int
    dur_us: int
    text: str
    label: str = ""            # 위에 작게 얹는 말 (첫 번째 / 30초 진단 …)
    lines: list[str] = field(default_factory=list)   # 직접 줄을 나눌 때
    span_lines: int = 1        # 덮는 자막 줄 수


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(text: str, max_chars: int) -> list[str]:
    """두 줄로 나눌 때는 **뜻이 붙는 자리**를 지킨다.

    글자 수만 세면 "조회수 말고 검색 / 결과" 처럼 한 덩어리를 쪼갠다.
    끊어읽기 판단은 자막에서 쓰는 규칙(korean.chunk)을 그대로 쓴다.
    """
    items = text.split()
    if not items:
        return []
    return [" ".join(items[i] for i in g)
            for g in korean.chunk(items, max_chars, None)]


def _list_lines(text: str, max_chars: int = 14) -> list[str]:
    """'A · B · C' 나열은 **항목 단위**로 줄을 나눈다.

    낱말 단위로 감싸면 가운뎃점이 둘째 줄 머리에 붙는다 —
    "이름 · 좋아진 점 / · 운동 사진" (2026-09-16 실제로 이렇게 구워졌다).
    """
    if "·" not in text:
        return []
    items = [x.strip() for x in text.split("·") if x.strip()]
    if len(items) < 2:
        return []
    lines, cur = [], ""
    for it in items:
        cand = f"{cur} · {it}" if cur else it
        if cur and len(cand) > max_chars:
            lines.append(cur)
            cur = it
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _fit(fp: str, lines: list[str], w: int, h: int,
         cap: float) -> tuple[ImageFont.FreeTypeFont, int]:
    """줄이 화면 폭 안에 들어올 때까지 글자를 줄인다.

    한 줄로 보여줄 수 있으면 그게 제일 잘 읽힌다. 억지로 감싸는 대신
    크기를 낮춰서 한 줄을 지킨다.
    """
    size = int(h * cap)
    while size > int(h * 0.05):
        f = _font(fp, size)
        if max(f.getbbox(ln)[2] - f.getbbox(ln)[0] for ln in lines) <= w * 0.86:
            return f, size
        size = int(size * 0.94)
    return _font(fp, size), size


def render(beat: Beat, w: int, h: int, out: Path) -> Path:
    """비트 한 장을 PNG 로.

    글은 **화면 한가운데보다 조금 위**에 놓는다. 자막이 맨 아래(y=-0.832)에
    깔려 있어서, 가운데에 두면 둘이 겹쳐 보인다.
    """
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    fp = emphasis_font_for_pillow()

    lines = beat.lines or ([beat.text] if len(beat.text) <= 16
                           else _list_lines(beat.text) or _wrap(beat.text, 12))
    f, size = _fit(fp, lines, w, h, 0.135 if len(lines) == 1 else 0.105)
    step = int(size * 1.34)

    center = int(h * 0.50)                      # 자막(맨 아래)과는 넉넉히 떨어진다
    y = center - (len(lines) * step) // 2

    if beat.label:
        lsize = int(h * 0.055)
        lf = _font(fp, lsize)
        d.text((w // 2, y - int(lsize * 1.7)), beat.label, font=lf,
               fill=DIM, anchor="ma")

    for ln in lines:
        d.text((w // 2, y), ln, font=f, fill=FG, anchor="ma")
        y += step

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def to_clip(png: Path, dur_us: int, w: int, h: int, fps: float,
            out: Path) -> Path:
    """PNG 를 짧은 mp4 로. 캡컷에서 영상 소재로 다루면 스키마 위험이 없다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-i", str(png), "-t", f"{dur_us / 1_000_000:.3f}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-shortest", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(int(round(fps))),
        "-vf", f"scale={w}:{h}", "-c:a", "aac", str(out),
    ], check=True)
    return out


def space_out(beats: list[Beat], total_us: int) -> list[Beat]:
    """도입부를 비우고, 너무 붙은 것은 버린다.

    **차례를 짚는 카드(라벨 있는 것)를 먼저 앉힌다.** 시간순으로만 훑으면
    앞에 있던 평범한 카드가 자리를 차지하고 파트 카드가 밀려난다 —
    실측(2026-09-08): '두 번째 프리랜서' 와 '첫째 유치율' 이 이렇게 빠져서,
    원고에 차례가 6개인데 카드는 절반도 안 나왔다.
    파트 카드는 영상의 뼈대라 하나라도 빠지면 구조가 안 보인다.
    """
    def ok(b: Beat) -> bool:
        return (b.at_us >= OPENING_GUARD_US
                and b.at_us + b.dur_us <= total_us)

    picked: list[Beat] = []
    for group in (
        [b for b in beats if b.label and ok(b)],      # 파트 카드 먼저
        [b for b in beats if not b.label and ok(b)],  # 나머지는 빈자리에만
    ):
        for b in sorted(group, key=lambda x: x.at_us):
            if any(abs(b.at_us - p.at_us) < MIN_APART_US for p in picked):
                continue
            picked.append(b)
    return sorted(picked, key=lambda x: x.at_us)


# ── 자막 경계에 붙이기 ────────────────────────────────────────────
#
# 비트를 **절대 시각**에 박아두면 안 된다. 대표님이 뒤에 컷을 더 하시면 말은
# 앞으로 당겨지는데 비트는 제자리에 남아 어긋난다. 실측(2026-08-18 완성본):
# 비트 9개가 전부 0.82~2.72초씩 밀려 있었고, 대표님이 하나씩 끌어다 맞추셨다.
#
# 맞춰 놓으신 자리를 재보니 규칙이 분명했다 —
#   시작 : 그 대사 자막의 시작에 **정확히** (9개 중 7개가 오차 0.00초)
#   끝   : 자막 경계에 **정확히** (9개 중 6개가 오차 0.00초)
# 길이를 2.0→1.6, 1.8→1.2 로 줄이신 것도 끝을 경계에 맞추기 위해서였다.


def sub_bounds(subs: list[tuple[int, int, str]]) -> list[int]:
    """자막 시작·끝을 모아 정렬한다."""
    return sorted({s for a, d, _ in subs for s in (a, a + d)})


def _flat(s: str) -> str:
    """띄어쓰기·문장부호를 턴 비교용 문자열."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


def find_anchor(subs: list[tuple[int, int, str]], text: str) -> int | None:
    """앵커 문구가 들어 있는 **첫** 자막의 시작 시각.

    글자 그대로 비교하면 안 된다. 자막은 16자 단위로 잘려 있어서 쉼표나
    띄어쓰기가 계획과 조금만 달라도 못 찾는다 — 실측(2026-09-08)에서
    '센터 직원, 중간관리자' 같은 앵커 4개가 이렇게 통째로 빠졌다.
    그래서 양쪽 다 공백·부호를 털고 비교한다.
    """
    want = _flat(text)
    if not want:
        return None
    for a, _, t in subs:
        if want in _flat(t):
            return a
    # 자막이 문구 한복판에서 잘렸을 수도 있다. 앞부분만이라도 맞으면 인정한다.
    head = want[:max(6, len(want) * 2 // 3)]
    for a, _, t in subs:
        if head and head in _flat(t):
            return a
    return None


SNAP_BACK_US = 600_000     # 이만큼 앞에 경계가 있으면 당긴다
MIN_BEAT_US = 1_000_000    # 이보다 짧아지진 않는다


def snap(at_us: int, want_dur_us: int, bounds: list[int],
         tol_us: int = SNAP_BACK_US) -> tuple[int, int]:
    """시작은 그대로 두고, 끝을 **앞쪽** 자막 경계로만 당긴다.

    대표님이 맞춰놓으신 9개를 재보니 규칙이 하나로 떨어졌다 —
    목표 끝보다 **앞선** 경계가 0.6초 안에 있으면 거기로 당기고,
    뒤쪽 경계로는 **절대 늘리지 않는다.**

      원한 2.0s · 경계 -0.36 → 1.6s 로 당김
      원한 1.8s · 경계 -0.58 → 1.2s 로 당김
      원한 2.4s · 경계 -1.16 / +0.64 → 그냥 2.4s (앞은 멀고 뒤로는 안 늘린다)

    늘리지 않는 이유는 짐작이 간다. 비트가 길어지면 말하는 사람이 그만큼 오래
    사라진다. 짧은 쪽 실수가 싸다.
    """
    target = at_us + want_dur_us
    cand = [b for b in bounds
            if at_us + MIN_BEAT_US <= b <= target and target - b <= tol_us]
    end = max(cand) if cand else target
    return at_us, max(MIN_BEAT_US, end - at_us)


def read_subs(content: dict) -> list[tuple[int, int, str]]:
    """드래프트에서 자막을 (시작, 길이, 글) 로 읽는다."""
    tm = {m["id"]: m for m in content["materials"].get("texts", [])}
    tracks = [t for t in content["tracks"]
              if t["type"] == "text" and t.get("segments")]
    if not tracks:
        return []
    tt = max(tracks, key=lambda t: len(t["segments"]))
    out = []
    for s in tt["segments"]:
        m = tm.get(s["material_id"], {})
        try:
            txt = json.loads(m.get("content", "{}")).get("text", "")
        except (ValueError, TypeError):
            txt = ""
        out.append((s["target_timerange"]["start"],
                    s["target_timerange"]["duration"], txt))
    out.sort()
    return out


# ── 카드 길이 = 그 말을 하는 동안 ────────────────────────────────
#
# 2026-09-16 실측으로 규칙이 바뀌었다. 풀링3(대표님 완성본) 카드 15장과 0916
# 카드 14장을 재보니 **전부** 자막 줄 경계에서 시작해 자막 줄 경계에서 끝났다.
# 고정 2초가 아니라 **카드가 보여주는 그 줄을 말하는 동안만** 떠 있다.
#   "반은 맞고 반은 틀렸습니다."          1.50s  = 그 한 줄
#   "센터 직원, / 중간관리자, / 그리고 센터 대표."  2.20s  = 세 줄
#   "내일부터는 수업에서 / 꼭 적용해보시길…"    2.40s  = 두 줄
# 중앙값이 1.3초라 예전 2초짜리는 대표님이 매번 깎으셨다.

MIN_CARD_US = 500_000
_SPAN_LEAD = ("그리고", "그래서", "그런데", "근데", "그러니까", "그러면",
              "그럼", "하지만")


def _covers(card_flat: str, line: str) -> bool:
    """이 자막 줄의 말이 카드 글에 들어 있는가."""
    lf = _flat(line)
    for c in _SPAN_LEAD:
        if lf.startswith(c) and len(lf) > len(c) + 1:
            lf = lf[len(c):]
            break
    if len(lf) < 2 or not card_flat:
        return False
    if lf in card_flat:
        return True
    from difflib import SequenceMatcher
    m = SequenceMatcher(None, card_flat, lf, autojunk=False).find_longest_match(
        0, len(card_flat), 0, len(lf))
    return m.size >= max(2, int(len(lf) * 0.6))


def span(subs: list[tuple[int, int, str]], at_us: int, text: str,
         max_lines: int = 3) -> tuple[int, int]:
    """카드가 덮을 자막 줄 — (길이, 줄 수).

    시작 줄은 늘 넣고, 뒤따르는 줄의 말이 카드 글에 들어 있는 동안 늘린다.
    "센터 직원 · 중간관리자 · 센터 대표" 카드는 세 줄, "회원수가 곧 월급입니다"
    카드는 한 줄에서 멈춘다.
    """
    i = next((k for k, s in enumerate(subs) if s[0] == at_us), None)
    if i is None:
        return MIN_CARD_US, 1
    cf = _flat(text)
    j = i
    while j + 1 < len(subs) and j - i + 1 < max_lines:
        gap = subs[j + 1][0] - (subs[j][0] + subs[j][1])
        if gap > 300_000 or not _covers(cf, subs[j + 1][2]):
            break
        j += 1
    end = subs[j][0] + subs[j][1]
    return max(MIN_CARD_US, end - at_us), j - i + 1


def is_section(label: str) -> bool:
    """차례를 짚는 카드인가 (첫 번째 · 둘째 · ① …)."""
    return bool(label) and (any(o in label for o in _ORDINALS)
                            or bool(re.search(r"[①-⑩]", label)))


def hides_subtitle(b: Beat) -> bool:
    """카드가 떠 있는 동안 그 줄 자막을 내리는가.

    실측(2026-09-16): 풀링3 과 0916 두 영상에서 대표님이 **카드 밑 자막을
    직접 12줄 지우셨다** (6줄씩). 카드에 같은 말이 크게 떠 있는데 아래에
    또 자막이 나오면 글이 두 번 보이기 때문이다.
    한 번도 안 지우신 건 두 종류다 —
      · 차례 카드(첫 번째 센터 직원…) 6장: 카드엔 '일하는 형태 ①' 이 붙어
        말과 모양이 달라서
      · 세 줄 이상 나열 카드("센터 직원, / 중간관리자, / 그리고 센터 대표."):
        자막이 항목을 하나씩 짚어 주는 역할이라
    안 지운 카드도 11장 있었는데(주로 한 영상의 앞부분·뒷부분), 대표님 확인
    결과 **카드 밑 자막은 내리는 게 기본**이다 (2026-09-16).
    """
    if is_section(b.label):
        return False
    if b.span_lines >= 3:
        return False
    return True


def resolve(plan: list[dict], subs: list[tuple[int, int, str]],
            total_us: int) -> tuple[list[Beat], list[str]]:
    """계획(앵커 문구 + 카드 글)을 실제 자리로 푼다."""
    beats, missing = [], []
    for p in plan:
        at = find_anchor(subs, p["anchor"])
        if at is None:
            missing.append(p["anchor"])
            continue
        dur, n = span(subs, at, p["text"])
        beats.append(Beat(at_us=at, dur_us=dur, text=p["text"],
                          label=p.get("label", ""), lines=p.get("lines", []),
                          span_lines=n))
    return space_out(beats, total_us), missing


# ── 대본에서 비트 후보 고르기 ──────────────────────────────────────
#
# 밀도는 대표님 기준으로 **6분에 14개**(약 26초에 하나). 9개는 부족하다고 하셨다.
# 자리 규칙은 손질본에서 역산한 그대로 — 자막 시작에 붙이고, 끝은 앞선 경계로만
# 당긴다.
#
# **자막 한 줄이 아니라 문장에서 고른다.** 자막은 16자짜리 조각이라 그대로 쓰면
# "몇 년을 일해도" 나 "마지막 / 에 말씀드릴게요" 처럼 토막이 나온다.

# 풀링3 1.8장/분 · 0916(대표님 손질) 1.9장/분 · 0818 비트14 2.3장/분("딱 좋다")
BEAT_PER_MIN = 2.0
MAX_CARD_CHARS = 26         # 이보다 길면 카드로 못 쓴다 (16자 넘으면 두 줄)
SECTION_CARD_CHARS = 18     # 차례 카드 글은 항목 이름만 — 더 짧게

# 문장 맨 앞의 이런 말은 카드에 안 넣는다. 화면에 크게 띄우면 군더더기다.
_LEAD_DROP = ("그래서", "그런데", "근데", "그리고", "그러니까", "그러면",
              "그럼", "이제", "자", "뭐", "사실", "물론", "오히려", "아")
# 차례를 가리키는 말은 카드 위 작은 라벨로 올린다.
# 뒤에 조사가 붙으면(마지막에) 차례가 아니므로 건드리지 않는다.
_ORDINALS = ("첫 번째", "두 번째", "세 번째", "네 번째",
             "첫째", "둘째", "셋째")


def group_sentences(subs: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    """자막 줄을 문장으로 이어 붙인다 — (첫 줄 시작 시각, 문장)."""
    out, cur, at = [], [], None
    for a, _, t in subs:
        t = t.strip()
        if not t:
            continue
        if at is None:
            at = a
        cur.append(t)
        if t.endswith((".", "?", "!")):
            out.append((at, " ".join(cur)))
            cur, at = [], None
    if cur and at is not None:
        out.append((at, " ".join(cur)))
    return out


_ORD_ANY = re.compile(r"(첫|두|세|네|다섯|여섯)\s*번째|첫째|둘째|셋째|넷째")


def _card_text(raw: str) -> tuple[str, str]:
    """문장을 (라벨, 카드 글) 로. 카드로 못 쓰면 ("","") 를 준다."""
    t = " ".join(raw.split()).strip().strip("…").rstrip(".?!,")
    label = ""
    for o in _ORDINALS:
        if t.startswith(o + " ") or t.startswith(o + ","):
            label = o
            t = t[len(o):].lstrip(" ,").strip()
            break
    # 차례 표시가 **문장 끝이나 가운데**에 있는 경우 (2026-09-25 실측)
    #   "좋은 센터의 조건 첫 번째."            ← 끝에 붙어 못 알아봤다
    #   "두 번째, 관리자의 트레이너 교육은, …"  ← 뒤가 길어 통째로 버려졌다
    # 둘 다 대표님이 "여기에 장표가 있어야 한다"고 짚으신 자리다.
    if not label:
        m = _ORD_ANY.search(t)
        if m:
            label = " ".join(m.group(0).split())
            before = t[:m.start()].strip(" ,")
            after = t[m.end():].strip(" ,")
            # 차례 **뒤**가 그 항목의 내용이다 ("두 번째, 관리자의 트레이너 교육은…").
            # 뒤가 비면("좋은 센터의 조건 첫 번째.") 앞을 쓴다.
            t = after if len(re.sub(r"[^가-힣0-9]", "", after)) >= 4 else before
    if label and "," in t:          # 차례 카드는 그 항목 이름까지만 (앞 토막)
        head = t.split(",")[0].strip()
        if len(re.sub(r"[^가-힣0-9]", "", head)) >= 4:
            t = head
    # 차례 카드는 **항목 이름**이라 짧아야 한다 (대표님 완성본: 3~12자)
    if label and len(t) > SECTION_CARD_CHARS:
        # 앞 토막(첫 쉼표까지)만 남긴다 — 그래도 길면 어절 단위로 줄인다
        head = t.split(",")[0].strip()
        if len(head) > SECTION_CARD_CHARS:
            # 낱말 수로만 자르면 "…남기기는 지금" 처럼 어정쩡하게 끊긴다.
            # 자막에서 쓰는 끊어읽기 규칙으로 첫 덩어리만 가져온다.
            words = head.split()
            groups = korean.chunk(words, SECTION_CARD_CHARS, None)
            head = " ".join(words[i] for i in groups[0]) if groups else head
        t = head
    if not label:
        for w in _LEAD_DROP:
            if t.startswith(w + " "):
                t = t[len(w):].strip()
                break
    t = t.rstrip(".?!,").strip()
    # 차례를 짚는 카드는 **짧아도 된다.** 파트 이름은 원래 짧다 —
    # 실측(2026-09-08 촬영본): '프리랜서'(4자) 'PT샵'(3자) '유치율'(3자)
    # '재등록률'(4자) 가 최소 5자에 걸려 통째로 빠졌다. 원고에 차례 표시가
    # 6개였는데 카드는 2개만 나왔다. 뼈대가 되는 자리를 길이로 떨어뜨리면 안 된다.
    low = 2 if label else 5
    if not (low <= len(t) <= MAX_CARD_CHARS):
        return "", ""
    return label, t


def _score(raw: str, label: str, text: str) -> float:
    """카드로 세울 만한 문장인가."""
    s = 0.0
    if label:
        s += 6.0                                  # 차례를 짚는 자리는 뼈대다
    if text.endswith(("다", "요")):
        s += 1.5                                  # 단정하는 말
    if any(k in text for k in ("아닙니다", "없습니다", "안 ", "못 ", "뿐")):
        s += 2.0                                  # 뒤집는 말
    if any(k in text for k in ("보세요", "하세요", "쳐보", "해보")):
        s += 2.0                                  # 행동을 시키는 말
    if raw.rstrip().endswith("?"):
        s += 1.5                                  # 던지는 질문
    if len(text) <= 13:
        s += 1.0                                  # 짧을수록 세다
    # 영상의 뼈대를 세우는 말 — 풀링3 에서 대표님이 남기신 카드가 여기 몰렸다
    # ("회원수가 곧 월급입니다", "알고 보면 하나입니다", "센터 직원·중간관리자·대표")
    if re.search(r"\S+[이가은는] 곧 \S+", text) or "곧 " in text:
        s += 3.0                                  # A가 곧 B — 등식으로 못 박는 말
    if re.search(r"(두|세|네|다섯) ?(가지|단어|개)", text) or "하나입니다" in text:
        s += 2.5                                  # 구조를 예고하는 말
    # '핵심' 가산점은 넣었다가 뺐다 (2026-09-16). "그 다음이 오늘의 핵심입니다" 같은
    # 예고 문장이 올라가서, 대표님이 밑 자막까지 지워 확정하신 카드 두 장
    # ("이 한마디입니다", "거기 어디야?")을 간격 규칙으로 밀어냈다.
    if any(k in text for k in ("이거", "그거", "저거", "이렇게", "그렇게",
                               "이분", "저는", "제가")):
        s -= 2.5                                  # 가리키는 말은 띄우면 뜬금없다
    # 지난 일을 들려주는 대목은 카드로 세우면 안 된다. 비트는 **주장**에 붙는다.
    # ("시키는 것보다 늘 더 하셨어요" 같은 사례 서술이 이렇게 걸러진다)
    if text.endswith(("셨어요", "셨습니다", "했어요", "였어요", "됐어요",
                      "있었어요", "니다만")):
        s -= 3.0
    # 말끝을 흐리거나 덧붙이는 어미 — 화면에 크게 띄울 말이 아니다
    if text.endswith(("거든요", "잖아요", "더라고요", "고요", "먼저요")):
        s -= 2.5
    return s


def pick(subs: list[tuple[int, int, str]], total_us: int,
         per_min: float = BEAT_PER_MIN) -> list[dict]:
    """자막에서 비트 계획(앵커 + 글)을 뽑는다.

    돌려주는 건 add_beats/resync 와 같은 계획 형식이라, 파일로 저장해 두면
    나중에 손으로 고칠 수 있다.
    """
    by_start = {a: t for a, _, t in subs}
    cands = []
    sents = group_sentences(subs)
    for si, (at, sent) in enumerate(sents):
        if at < OPENING_GUARD_US:
            continue
        label, text = _card_text(sent)
        # 차례만 선언하고 내용은 다음 문장에 있는 경우 —
        # "좋은 센터의 조건 첫 번째." / "내 이름이 남는 센터입니다."
        # 그대로 두면 카드 셋이 전부 '좋은 센터의 조건' 이 된다. 뒤 문장을 글로 쓴다.
        if label and si + 1 < len(sents):
            nxt = _card_text(sents[si + 1][1])[1]
            if nxt and (len(text) < 9 or _flat(text) in _flat(sent)[:0] or
                        sum(1 for _, s2 in sents if _flat(text) and _flat(text) in _flat(s2)) > 1):
                text = nxt
        if not text:
            continue
        anchor = by_start.get(at, "").strip()
        if not anchor:
            continue
        cands.append((_score(sent, label, text), at, label, text, anchor))

    budget = max(1, int(round(total_us / US / 60 * per_min)))
    cands.sort(key=lambda c: (-c[0], c[1]))
    taken: list[tuple[int, str, str, str]] = []

    # ── 차례를 짚는 자리는 **무조건** 카드로 세운다 (2026-09-25 대표님 지시) ──
    # "첫 번째·두 번째·세 번째처럼 소제목으로 나뉘는 곳에는 장표가 있어야 한다."
    # 0923 촬영본에서 차례 6곳 중 카드가 0곳이었다 — 개수 한도(분당 2장)에 밀렸다.
    seen: set[str] = set()
    for _, at, label, text, anchor in cands:
        if not label or _flat(text) in seen:
            continue
        if any(abs(at - t[0]) < SECTION_APART_US for t in taken):
            continue
        taken.append((at, label, text, anchor))
        seen.add(_flat(text))

    n_section = len(taken)
    for _, at, label, text, anchor in cands:
        if len(taken) - n_section >= budget:
            break
        if _flat(text) in seen:
            continue
        if any(abs(at - t[0]) < MIN_APART_US for t in taken):
            continue
        taken.append((at, label, text, anchor))
        seen.add(_flat(text))

    taken.sort()
    # "at" 을 같이 실어 보낸다. 앵커 문구만으로는 같은 말이 두 번 나올 때 앞자리로
    # 잘못 붙는다 — 0923 에서 "두 번째," 가 두 번 나와 뒤 카드가 앞으로 끌려갔다.
    return [{"anchor": anchor, "at": at, "dur": 2.0 if len(text) <= 13 else 2.4,
             "label": label, "text": text} for at, label, text, anchor in taken]
