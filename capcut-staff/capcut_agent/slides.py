"""대본을 보고 장표를 언제 띄울지 정한다 — VOD 강의 편집 전용.

촬영 방식이 바뀌었다. 단초점 빔으로 슬라이드를 띄우고 그 앞에서 찍던 것을,
깔끔한 배경 앞에서만 찍고 **편집에서 PPT를 덧입히는** 방식으로 간다.
그래서 "어느 장표를 언제 띄울지"를 여기서 정한다.

## 왜 문구로 찾지 않는가 (실측)

큐 시트의 **문구**를 대본과 대조하는 방법을 먼저 해봤고, 실패했다.
문구는 5단어 요약이라 실제 발화와 글자가 다르다.

    「노는 두 개다」        ← 실제 발화 "트레이너 일은 노가 두 개입니다"
    「무너지기 전 3가지 신호」 ← 유사도가 고른 것: "누구보다 열심히 했습니다"
    「유능한 게 검색되게」    ← 원고에 그 말이 아예 없다 (의도적)

그럴듯한 오답을 고른다. `style.load_emphasis_plan` 이 "규칙으로 안 되니 읽고
판단한 결과를 파일로 받는다"고 한 것과 같은 자리다.

## 그래서 두 단계로 나눈다

    1) 큐 → **근거 문장**   큐 시트에 사람이 한 번 적어둔다 (편당 7~14줄)
    2) 근거 문장 → 시각     원고 전문과 ASR 전문을 통째로 정렬해서 읽는다

2)가 되는 이유는 실측했다 — 원고를 거의 그대로 읽으시기 때문이다.

    파트0 1편   130/131 문장 정렬 (99%)
    파트0 2편   108/108 문장 정렬 (100%)
    오리엔테이션 143/144 문장 정렬 (99%)

근거 문장을 손으로 준 상태에서 14/14 가 정확히 그 말을 하는 순간에 꽂혔다.

## 큐 시트 시점은 타이밍으로 쓰지 않는다

원고에 적힌 `시점`은 낭독 추정치라 실제와 크게 어긋난다 (실측 +12초 ~ +334초).
사양서 §11 도 "생성에는 안 쓰이고 촬영용 참고"라고 못 박고 있다.
여기서는 **어긋남을 보여주는 용도로만** 쓴다.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

US = 1_000_000
_KEEP = re.compile(r"[^가-힣0-9a-zA-Z]")


def norm(s: str) -> str:
    """비교용 정규화. 띄어쓰기·문장부호는 발화마다 흔들려서 다 버린다."""
    return _KEEP.sub("", s)


# ── 큐 시트 ──────────────────────────────────────────────────────
@dataclass
class Cue:
    """큐 시트 한 줄. 사양서 §11 표기법."""
    index: int          # 표 안에서의 순번 = 장표 파일 순번
    at_s: float         # 원고가 적어 둔 시점 (참고용, 타이밍에 안 씀)
    label: str
    kind: str           # 여백 / 문구 / 게이지 n/m / 흐름 / 비교 / 체크리스트
    focus: bool         # ★ 강조 배경
    phrase: str
    anchor: str         # 근거 문장 — 원고에서 이 말을 할 때 띄운다
    note: str

    @property
    def blank(self) -> bool:
        return self.kind.startswith("여백")


def parse_cue_sheet(md: str, header: str) -> list[Cue]:
    """`## <header>` 아래 첫 표를 읽는다.

    열은 사양서 §11 의 다섯 열에 **근거**를 더한 여섯 열이다.
    근거 열이 없는 예전 표도 읽히게 둔다 (그 경우 anchor 가 빈 문자열).
    """
    lines = md.splitlines()
    try:
        i = next(k for k, l in enumerate(lines)
                 if l.strip().startswith("#") and header in l)
    except StopIteration:
        raise ValueError(f"큐 시트를 못 찾았습니다: {header}")

    out: list[Cue] = []
    for line in lines[i + 1:]:
        if line.startswith("#") and out:
            break
        t = line.strip()
        if not t.startswith("|"):
            continue
        cells = [c.strip() for c in t.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0] in ("시점", "") or set(cells[0]) <= set("-: "):
            continue
        m = re.match(r"(\d+):(\d+)", cells[0])
        if not m:
            continue
        kind = cells[2]
        out.append(Cue(
            index=len(out),
            at_s=int(m.group(1)) * 60 + int(m.group(2)),
            label=cells[1],
            kind=kind.replace("★", "").strip(),
            focus="★" in kind,
            phrase=cells[3].replace("⏎", " ").strip(),
            anchor=(cells[5].strip() if len(cells) > 5 else ""),
            note=cells[4],
        ))
    return out


def parse_script_body(md: str, start_pat: str, end_pat: str) -> list[str]:
    """원고 본문을 문장 목록으로.

    **굵게** 표시한 줄을 버리면 안 된다 — 원고에서 굵게 쓴 문장이 곧 근거 문장이다.
    (여기서 `*` 로 시작하는 줄을 통째로 걸렀다가 근거를 절반이나 놓쳤다)
    """
    a = re.search(start_pat, md, re.M)
    if not a:
        raise ValueError(f"원고 시작을 못 찾았습니다: {start_pat}")
    body = md[a.end():]
    b = re.search(end_pat, body, re.M)
    if b:
        body = body[:b.start()]

    sents: list[str] = []
    for line in body.splitlines():
        t = line.strip()
        if not t or t.startswith(("#", ">", "|")):
            continue
        if re.match(r"^([-*+]|\d+\.)\s", t):      # 목록 기호만 걸러낸다
            continue
        if re.match(r"^\*\*\[", t):               # **[0:00~0:20 · 훅]** 머리말
            continue
        t = t.replace("**", "")
        for s in re.split(r"(?<=[.!?])\s+", t):
            s = s.strip()
            if len(norm(s)) >= 2:
                sents.append(s)
    return sents


# ── 원고 ↔ 촬영본 정렬 ───────────────────────────────────────────
def asr_index(tr) -> tuple[str, list[int]]:
    """정규화된 대본 전문과, 글자마다의 원본 시각(µs).

    낱말 타임스탬프가 있으면 그걸 쓴다. 낱말이 곧 "그 말을 시작한 순간"이라
    장표를 거기에 걸면 말과 화면이 붙는다.
    """
    text: list[str] = []
    times: list[int] = []
    for seg in tr.segs:
        words = getattr(seg, "words", None) or []
        if words:
            for w in words:
                n = norm(w.text)
                if not n:
                    continue
                text.append(n)
                times.extend([w.start] * len(n))
        else:
            n = norm(seg.text)
            if not n:
                continue
            text.append(n)
            dur = seg.end - seg.start
            for k in range(len(n)):
                times.append(seg.start + dur * k // max(1, len(n)))
    return "".join(text), times


# 끊긴 조각을 도로 끌어올 최대 거리(글자). 한 문장 안쪽 정도만.
NEAR_CHARS = 24


def align_script(sents: list[str], asr_text: str):
    """원고 문장 → (대본 글자 위치, 정렬된 비율), 그리고 원고글자→대본글자 지도.

    문장을 하나씩 찾아다니지 않고 **전문 대 전문**으로 한 번에 맞춘다.
    그래야 순서가 저절로 보장되고, 비슷한 문장이 여러 번 나와도 제자리를 찾는다.
    """
    spans: list[tuple[int, int]] = []
    acc = 0
    parts: list[str] = []
    for s in sents:
        n = norm(s)
        spans.append((acc, acc + len(n)))
        acc += len(n)
        parts.append(n)
    script_text = "".join(parts)

    sm = SequenceMatcher(None, script_text, asr_text, autojunk=False)
    m: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            m[a + k] = b + k

    out: dict[int, tuple[int, float]] = {}
    for i, (s0, s1) in enumerate(spans):
        hit = [(k, m[k]) for k in range(s0, s1) if k in m]
        if not hit:
            continue
        # 가장 **길게 이어 붙은** 자리를 그 문장의 위치로 본다.
        #
        # 제일 앞에 맞은 글자(min)를 쓰면 안 된다. 촬영본에 원고에 없는 말이
        # 끼면 앞 글자 몇 개가 엉뚱한 문장에 붙는다 — 실제로 「노는 두 개다」가
        # "트레이너"만 보고 "트레이너의 일이 바다 위에 배라고…" 로 끌려가서
        # 장표가 몇 초 먼저 떴다.
        best_start, best_len = hit[0][1], 1
        run_start, run_len = hit[0], 1
        for prev, cur in zip(hit, hit[1:]):
            if cur[0] == prev[0] + 1 and cur[1] == prev[1] + 1:
                run_len += 1
            else:
                run_start, run_len = cur, 1
            if run_len > best_len:
                best_start, best_len = run_start[1], run_len

        # 그 자리 **바로 앞**에 붙은 조각은 같은 말의 일부다. 어미가 조금
        # 달라지면("봐선" → "봐서는") 이어짐이 끊겨 뒤 토막만 남는데, 그러면
        # 장표가 한 박자 늦게 뜬다. 가까이 있는 것만 도로 끌어온다 —
        # 멀리 떨어진 조각은 다른 문장에 잘못 붙은 것이라 버려야 한다.
        near = [b for _a, b in hit if best_start - NEAR_CHARS <= b <= best_start]
        if near:
            best_start = min(near)
        out[i] = (best_start, len(hit) / max(1, s1 - s0))
    return out, spans, m


# 근거 자리를 찾을 때 이만큼까지는 앞뒤로 더듬는다 (어미 변형 흡수)
PHRASE_SLACK = 10

# 근거가 문장 이만큼 뒤에서 시작하면 "앞머리(군더더기)가 있다"고 본다.
#
# 대표님 최종본과 대조해 정한 값이다. 무조건 근거 자리로 당기면 오히려
# 나빠진다 — "회원은 여러분 실력을 보고 오는 게 아니에요"처럼 앞이 두세 어절뿐일
# 때는 **문장 머리**가 정답이었다(대표님도 거기 거셨다). 반대로
# "이게 제일 중요한 부분인데요, 다 보고 나서…"처럼 앞머리가 길면 본론으로 당겨야 한다.
PREAMBLE_MIN = 8


def _phrase_pos(key: str, sent: str, i: int, spans, cmap) -> int | None:
    """근거 문구가 대본에서 시작되는 글자 위치.

    원고 문장 안에서 근거가 몇 글자째인지 세고, 그 자리를 대본 좌표로 옮긴다.
    정확히 그 글자가 안 맞았으면 바로 뒤에서 맞은 글자를 찾아 쓴다 —
    촬영본은 어미가 조금씩 다르다.
    """
    n = norm(sent)
    off = n.find(key)
    if off < 0:
        return None
    base = spans[i][0] + off
    for k in range(base, base + PHRASE_SLACK):
        if k in cmap:
            return cmap[k]
    return None


@dataclass
class Resolved:
    """큐 하나가 촬영본의 어디에 걸렸는가."""
    cue: Cue
    src_us: int = -1        # 원본 기준 시각
    sent_i: int = -1
    ratio: float = 0.0      # 그 문장이 대본과 얼마나 일치했나
    heard: str = ""         # 그 시각에 실제로 하는 말 (눈으로 확인용)
    why: str = ""           # 못 찾았으면 이유


def resolve(cues: list[Cue], sents: list[str], tr) -> list[Resolved]:
    """큐 → 원본 시각. 근거 문장을 원고에서 찾고, 정렬로 시각을 읽는다."""
    asr_text, times = asr_index(tr)
    amap, spans, cmap = align_script(sents, asr_text)

    out: list[Resolved] = []
    last = -1
    for c in cues:
        r = Resolved(cue=c)
        if c.blank:
            out.append(r)
            continue
        if not c.anchor:
            r.why = "근거 문장이 비어 있음"
            out.append(r)
            continue

        key = norm(c.anchor)
        # 근거는 원고 문장의 일부만 적어도 되게 한다 (길게 적으면 오타가 난다)
        idx = [i for i, s in enumerate(sents) if key and key in norm(s)]
        if not idx:
            r.why = "근거 문장이 원고에 없음"
            out.append(r)
            continue
        # 순서를 지킨다 — 같은 말이 여러 번 나오면 앞 큐보다 뒤에 있는 것을 고른다
        after = [i for i in idx if i > last]
        i = after[0] if after else idx[0]

        if i not in amap:
            r.why = "그 문장이 촬영본에 없음 (안 하셨거나 다르게 말씀하심)"
            r.sent_i = i
            out.append(r)
            continue

        pos, ratio = amap[i]
        # 문장 **시작**이 아니라 **근거 문구가 놓인 자리**에 건다.
        #
        # 근거 문장 앞에 군더더기가 붙어 있으면 장표가 먼저 뜬다. 실측:
        #   "이게 제일 중요한 부분인데요. 다 보고나서 시작하는 게 아니라…"
        # 문장 머리에 걸어서 1.5초 빨랐고, 대표님이 본론으로 옮기셨다.
        head = norm(sents[i]).find(key)
        if head >= PREAMBLE_MIN:          # 앞머리가 있을 때만 본론으로 당긴다
            refined = _phrase_pos(key, sents[i], i, spans, cmap)
            if refined is not None and refined >= pos:
                pos = refined
        last = i
        r.sent_i, r.ratio = i, ratio
        r.src_us = times[pos] if pos < len(times) else -1
        r.heard = asr_text[pos:pos + 26]
        out.append(r)
    return out


# ── 화면에서 사람이 없는 자리 찾기 ───────────────────────────────
@dataclass
class SafeZone:
    """장표를 얹어도 사람을 안 가리는 가로 구간 (0~1 비율)."""
    x0: float
    x1: float
    confident: bool
    detail: str

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)


def measure_safe_zone(video: Path, duration_us: int, samples: int = 40,
                      w: int = 480, h: int = 270,
                      start_us: int = 0, end_us: int = 0) -> SafeZone:
    """사람이 지나간 자리를 실측한다.

    사람은 움직이고 배경은 안 움직인다. 프레임을 여러 장 떠서 픽셀별 시간축
    표준편차를 구하면 사람이 쓰는 영역이 그대로 드러난다.

    좌표를 눈대중으로 박으면 영상마다 틀린다 — 실제로 촬영본 3개의 안전
    구간이 서로 달랐다.

    **말하는 구간만 본다.** 파일 앞뒤에는 녹화 버튼을 누르러 오가는 장면이
    들어 있어서, 통째로 재면 화면을 가로지르는 그 몇 프레임 때문에 빈 자리가
    없다고 나온다 (실측: 왼쪽 절반이 비어 있는데 11% 로 나왔다).
    """
    import numpy as np
    from PIL import Image

    lo = max(0, start_us)
    hi = end_us if end_us > lo else duration_us
    dur_s = max(1.0, (hi - lo) / US)
    tmp = Path(video).parent / ".safezone_tmp"
    try:
        tmp.mkdir(exist_ok=True)
    except OSError:
        import tempfile
        tmp = Path(tempfile.mkdtemp())
    for f in tmp.glob("*.jpg"):
        try:
            f.unlink()
        except OSError:
            pass

    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{lo / US:.3f}", "-t", f"{dur_s:.3f}", "-i", str(video),
            "-vf", f"fps={samples / dur_s},scale={w}:{h}",
            "-frames:v", str(samples), str(tmp / "f%03d.jpg"),
        ], check=True, timeout=600)
        files = sorted(tmp.glob("*.jpg"))
        if len(files) < 4:
            return SafeZone(0.0, 0.5, False, "표본이 모자람")
        arr = np.stack([np.asarray(Image.open(p).convert("L"), dtype=np.float32)
                        for p in files])
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        return SafeZone(0.0, 0.5, False, f"측정 실패({e.__class__.__name__})")
    finally:
        for f in tmp.glob("*.jpg"):
            try:
                f.unlink()
            except OSError:
                pass

    col = arr.std(axis=0).mean(axis=0)          # 열별 움직임
    thr = max(float(col.max()) * 0.12, 1.5)

    # 왼쪽부터 연속으로 조용한 구간을 잡는다 (촬영이 화자를 오른쪽에 두고 있다)
    q = 0
    while q < w and col[q] <= thr:
        q += 1
    left = q / w

    # 오른쪽도 재서 더 넓은 쪽을 쓴다
    q2 = w - 1
    while q2 >= 0 and col[q2] <= thr:
        q2 -= 1
    right = 1.0 - (q2 + 1) / w

    if left >= right and left >= 0.25:
        return SafeZone(0.0, left, left >= 0.35,
                        f"왼쪽 {left:.0%} 가 정지")
    if right >= 0.25:
        return SafeZone(1.0 - right, 1.0, right >= 0.35,
                        f"오른쪽 {right:.0%} 가 정지")
    return SafeZone(0.0, max(left, 0.34), False,
                    f"빈 자리가 좁음 (왼쪽 {left:.0%} · 오른쪽 {right:.0%})")


# ── 자막을 컷에 맞추기 ───────────────────────────────────────────
#
# 장표를 아무리 잘 붙여도 **컷과 자막이 서로 안 맞아 있으면** 셋이 동시에
# 맞을 수 없다. 실측: 2편은 컷 시작의 94%가 자막 시작과 같은 자리였고
# (대표님이 그렇게 편집하셨다) 오리엔테이션은 17%뿐이었다.
#
# 자막 트랙은 빈틈 없이 이어져 있으므로(자막 끊기 규칙) **경계만 옮기면**
# 앞뒤 자막이 같이 따라온다. 옮기는 양은 아주 작다 — 실측 평균 0.09초.
SUB_SNAP_TOL_S = 0.40    # 이보다 멀면 그냥 둔다 (말과 어긋나면 안 되니까)
SUB_MIN_S = 0.35         # 이보다 짧아지는 자막은 만들지 않는다


def align_subtitles(text_track: dict, cuts, tol_s: float = SUB_SNAP_TOL_S,
                    min_s: float = SUB_MIN_S) -> tuple[int, float]:
    """자막 경계를 컷 경계에 맞춘다. (옮긴 개수, 평균 이동초)

    글자와 모양은 건드리지 않는다. 시간만 만진다.
    """
    import bisect

    segs = sorted(text_track.get("segments", []),
                  key=lambda s: s["target_timerange"]["start"])
    if len(segs) < 2:
        return 0, 0.0

    bounds = [segs[0]["target_timerange"]["start"]]
    for s in segs:
        r = s["target_timerange"]
        bounds.append(r["start"] + r["duration"])

    cuts = sorted(set(cuts))
    tol, min_us = int(tol_s * US), int(min_s * US)
    new = list(bounds)
    shifts = []

    # 맨 앞과 맨 끝은 그대로 둔다 (0 부터 타임라인 끝까지 채우는 규칙)
    for i in range(1, len(bounds) - 1):
        t = bounds[i]
        j = bisect.bisect_left(cuts, t)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(cuts) and abs(cuts[k] - t) <= tol:
                if best is None or abs(cuts[k] - t) < abs(best - t):
                    best = cuts[k]
        if best is None or best == t:
            continue
        if best - new[i - 1] < min_us:          # 앞 자막이 너무 짧아진다
            continue
        if bounds[i + 1] - best < min_us:       # 이 자막이 너무 짧아진다
            continue
        new[i] = best
        shifts.append(abs(best - t) / US)

    for i, s in enumerate(segs):
        st, en = new[i], new[i + 1]
        if en - st < min_us:                    # 안전장치 — 원래대로
            st, en = bounds[i], bounds[i + 1]
        s["target_timerange"]["start"] = st
        s["target_timerange"]["duration"] = en - st

    avg = sum(shifts) / len(shifts) if shifts else 0.0
    return len(shifts), avg


def align_subtitle_pieces(pieces, cuts, tol_s: float = SUB_SNAP_TOL_S,
                          min_s: float = SUB_MIN_S) -> tuple[int, float]:
    """새로 만드는 경로용 — 자막 조각 목록의 경계를 컷에 맞춘다.

    `align_subtitles` 는 이미 만들어진 캡컷 트랙을 고치고, 이쪽은 드래프트를
    만들기 **전**의 조각 목록을 고친다. 규칙은 같다: 이어붙은 트랙이라
    경계만 옮기면 되고, 0.4초 넘게 떨어진 자리는 그냥 둔다.
    """
    import bisect

    if len(pieces) < 2:
        return 0, 0.0
    pieces = sorted(pieces, key=lambda p: p.start)
    bounds = [pieces[0].start] + [p.start + p.dur for p in pieces]
    cuts = sorted(set(cuts))
    tol, min_us = int(tol_s * US), int(min_s * US)
    new, shifts = list(bounds), []
    for i in range(1, len(bounds) - 1):
        t = bounds[i]
        j = bisect.bisect_left(cuts, t)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(cuts) and abs(cuts[k] - t) <= tol:
                if best is None or abs(cuts[k] - t) < abs(best - t):
                    best = cuts[k]
        if best is None or best == t:
            continue
        if best - new[i - 1] < min_us or bounds[i + 1] - best < min_us:
            continue
        new[i] = best
        shifts.append(abs(best - t) / US)
    for i, pc in enumerate(pieces):
        st, en = new[i], new[i + 1]
        if en - st < min_us:
            st, en = bounds[i], bounds[i + 1]
        pc.start, pc.dur = st, en - st
    return len(shifts), (sum(shifts) / len(shifts) if shifts else 0.0)


# ── 경계 물리기 ──────────────────────────────────────────────────
#
# 장표를 **말이 시작되는 시각 그대로** 걸면 컷·자막 경계와 0.3초씩 어긋난다.
# 타임라인에서 세 트랙의 이음매가 삐뚤삐뚤해 보이고, 실제로 그렇게 보였다.
#
# 대표님이 손수 맞춘 2편을 재보니 답이 나왔다 — 장표 59조각 중 **50개가
# 컷 경계와 자막 경계에 정확히 물려** 있었다. 내 것은 67개 중 5개였다.
# 그래서 계산한 시각을 근처 경계로 끌어다 붙인다.
SNAP_TOL_S = 0.8         # 이 안쪽 경계까지만 끌어온다
SNAP_SAME_US = 40_000    # 이만큼 가까운 컷·자막은 같은 자리로 본다


def make_snapper(cuts, subs, tol_s: float = SNAP_TOL_S):
    """가까운 경계로 끌어다 붙이는 함수를 만든다.

    컷과 자막이 **둘 다 있는 자리**를 가장 좋아한다. 거기가 화면·글자·장표가
    한꺼번에 바뀌는 자리라 이음매가 깔끔하다.
    """
    import bisect

    cuts = sorted(set(cuts))
    subs = sorted(set(subs))

    def has(arr, t):
        i = bisect.bisect_left(arr, t)
        for j in (i - 1, i):
            if 0 <= j < len(arr) and abs(arr[j] - t) <= SNAP_SAME_US:
                return True
        return False

    # 순위: 컷+자막 > 컷 > 자막.
    #
    # 컷을 자막보다 위에 둔다. 컷은 화면이 실제로 튀는 자리라 이음매가 제일
    # 눈에 띈다. 자막만 있는 자리로 끌려가면 정작 컷과 어긋난다.
    def rank(t):
        c, b = has(cuts, t), has(subs, t)
        return 3 if (c and b) else (2 if c else 1)

    scored = [(t, rank(t)) for t in sorted(set(cuts) | set(subs))]
    tol = int(tol_s * US)

    def snap(t: int, lo: int | None = None, hi: int | None = None,
             tol_us: int | None = None) -> int:
        limit = tol if tol_us is None else tol_us
        best = None
        for v, sc in scored:
            if abs(v - t) > limit:
                continue
            if lo is not None and v < lo:
                continue
            if hi is not None and v > hi:
                continue
            key = (-sc, abs(v - t))
            if best is None or key < best[0]:
                best = (key, v)
        return best[1] if best else t

    return snap


# ── 배치 ─────────────────────────────────────────────────────────
@dataclass
class SlidePlan:
    """장표 한 장이 놓일 자리. 시간은 **타임라인** 좌표."""
    cue: Cue
    image: Path
    start: int
    dur: int
    fullscreen: bool
    scale: float
    pos_x: float
    pos_y: float
    src_us: int = -1


# 도입부는 비워 둔다. 시작하자마자 장표가 튀어나오면 누가 말하는지
# 파악하기 전에 시선을 뺏긴다. (cards.OPENING_GUARD_S 와 같은 취지)
MIN_SHOW_S = 3.0
# ★ 강조 장표가 화면을 꽉 채우고 있을 시간
FOCUS_HOLD_S = 5.0


def _timeline_mapper(keeps, speed: float):
    """원본 시각 → 타임라인 시각. 컷과 배속을 거친 자리를 되찾는다.

    안 하면 장표가 엉뚱한 말 위에 뜬다. 잘려나간 자리에 걸렸으면
    **다음으로 살아남은 순간**으로 민다 — 장표를 통째로 버리는 것보다 낫다.
    """
    offs, acc = [], 0
    for k in keeps:
        offs.append(acc)
        acc += k.dur
    total = acc

    def to_tl(src_us: int) -> tuple[int | None, int]:
        for k, off in zip(keeps, offs):
            if k.start <= src_us < k.end:
                return int((off + (src_us - k.start)) / speed), 0
        # 잘린 자리 — 그 뒤로 처음 살아 있는 지점
        for k, off in zip(keeps, offs):
            if k.start >= src_us:
                return int(off / speed), k.start - src_us
        return None, 0

    return to_tl, int(total / speed)


def mapper_from_spans(spans) -> tuple:
    """이미 편집된 드래프트의 메인 트랙에서 원본→타임라인 표를 만든다.

    사용자가 캡컷에서 컷을 다시 잡으면 우리가 계산한 keeps 는 더 이상 사실이
    아니다. 드래프트의 메인 트랙이 곧 "원본 어디를 어디에 놓았는가"의 정답이라
    거기서 직접 읽는다.

    spans: (src0, src1, tl0, tl1, speed) 목록
    """
    spans = sorted(spans, key=lambda x: x[2])
    total = max((x[3] for x in spans), default=0)

    def to_tl(src_us: int) -> tuple[int | None, int]:
        for s0, s1, t0, _t1, sp in spans:
            if s0 <= src_us < s1:
                return int(t0 + (src_us - s0) / max(sp, 1e-6)), 0
        nxt = [x for x in spans if x[0] >= src_us]
        if nxt:
            n = min(nxt, key=lambda x: x[0])
            return int(n[2]), n[0] - src_us      # 밀린 양(원본 기준)
        return None, 0

    return to_tl, total


def plan_slides(resolved: list[Resolved], slide_dir: Path, keeps, speed: float,
                zone: SafeZone, media, mapper=None, snap=None
                ) -> tuple[list[SlidePlan], list[str]]:
    """큐 결과 + 장표 이미지 → 타임라인 배치.

    · 장표는 **다음 큐가 올 때까지 계속** 떠 있는다. 2~3초 카드처럼 스쳐
      지나가면 안 된다 — 말하는 내내 그 화면이어야 한다.
    · `여백` 줄은 장표를 걷어내는 지시다. 얼굴만 보여야 하는 대목이 거기다.
    · `★`(강조 배경)만 전체화면. 나머지는 왼쪽 패널로 얼굴을 살린다.
    """
    warns: list[str] = []
    images = sorted(p for p in slide_dir.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if len(images) != len(resolved):
        warns.append(f"장표 {len(images)}장 ↔ 큐 {len(resolved)}줄 — 수가 다릅니다")

    to_tl, total_tl = mapper if mapper else _timeline_mapper(keeps, speed)

    w = panel_warning(zone)
    if w:
        warns.append(w)

    # 1) 각 큐의 타임라인 시각
    marks: list[tuple[int, Resolved, Path | None]] = []
    for r in resolved:
        img = images[r.cue.index] if r.cue.index < len(images) else None
        if r.cue.blank:
            # 여백은 '이 시점부터 장표 없음'인데, 보통 0:00 이라 시작점이다
            marks.append((0, r, None))
            continue
        if r.src_us < 0:
            warns.append(f"[{r.cue.label}] 건너뜀 — {r.why}")
            continue
        tl, moved = to_tl(r.src_us)
        if tl is None:
            warns.append(f"[{r.cue.label}] 건너뜀 — 잘려나간 구간")
            continue
        # 컷 경계에 몇 프레임 걸린 건 흔하고 결과도 같다. 눈에 띌 만큼
        # 밀렸을 때만 알린다 — 경고가 흔하면 진짜 경고를 안 보게 된다.
        if moved > int(0.5 * US):
            warns.append(f"[{r.cue.label}] 근거 대사가 잘려서 "
                         f"{moved/US:.1f}초 뒤로 밀었습니다")
        marks.append((tl, r, img))

    marks.sort(key=lambda m: m[0])

    # 계산한 시각을 **컷·자막 경계로 끌어다 붙인다.** 안 하면 세 트랙의
    # 이음매가 0.3초씩 어긋나 타임라인이 삐뚤삐뚤해 보인다 (실측: 67개 중
    # 경계에 맞은 게 5개뿐이었다. 대표님 손편집은 59개 중 50개였다).
    if snap:
        snapped, prev = [], -1
        for tl, r, img in marks:
            v = snap(tl)
            if v <= prev:            # 두 장표가 같은 경계로 끌려오면 원래대로
                v = tl
            if v <= prev:
                v = prev + 1
            snapped.append((v, r, img))
            prev = v
        marks = snapped

    # 2) 너무 짧게 스쳐 갈 장표는 아예 뺀다.
    #
    # 큐 시트는 두 장을 20초 간격으로 잡아뒀는데 실제로는 이어서 말씀하시는
    # 자리가 있다("딱 세 가지더라고요" 바로 뒤에 "첫 번째, 계속 봅니다").
    # 컷까지 거치면 1.6초가 되어 화면이 깜빡인다. 그럴 땐 **뒤엣것을 남긴다** —
    # 지금 하는 말에 더 가까운 쪽이 뒤다. 앞 장표가 그 자리를 이어받는다.
    min_us = int(MIN_SHOW_S * US)
    pruned = True
    while pruned and len(marks) > 1:
        pruned = False
        for n in range(len(marks) - 1):
            if marks[n][1].cue.blank:
                continue
            if marks[n + 1][0] - marks[n][0] < min_us:
                warns.append(
                    f"[{marks[n][1].cue.label}] 다음 장표와 "
                    f"{(marks[n+1][0]-marks[n][0])/US:.1f}초 차이라 뺐습니다")
                marks.pop(n)
                pruned = True
                break

    # 3) 다음 큐까지 유지
    out: list[SlidePlan] = []
    for n, (tl, r, img) in enumerate(marks):
        if r.cue.blank or img is None:
            continue
        end = next((marks[k][0] for k in range(n + 1, len(marks))
                    if marks[k][0] > tl), total_tl)
        # 다음 줄이 여백이면 거기서 걷힌다 (위 계산에 이미 반영된다)
        dur = end - tl
        if dur <= 0:
            continue

        scale, px, py = panel_geometry(zone)
        prev_full = bool(out) and out[-1].fullscreen

        if r.cue.focus and dur > FOCUS_HOLD_S * US * 1.5:
            # ★ 는 그 문장을 말하는 동안만 화면을 꽉 채우고, 나머지는 패널로
            # 내려앉는다. 계속 전체화면으로 두면 핵심 대목에서 얼굴이 1분 넘게
            # 사라진다 — 강조는 순간이어야 강조다.
            hold = int(FOCUS_HOLD_S * US)
            out.append(SlidePlan(r.cue, img, tl, hold, True, 1.0, 0.0, 0.0,
                                 r.src_us))
            out.append(SlidePlan(r.cue, img, tl + hold, dur - hold, False,
                                 scale, px, py, r.src_us))
        elif r.cue.focus:
            out.append(SlidePlan(r.cue, img, tl, dur, True, 1.0, 0.0, 0.0,
                                 r.src_us))
        else:
            lead = _lead_us(dur, prev_full)
            if lead and snap:
                # 리드가 끝나는 자리도 이음매다. 여유를 두고 경계로 붙인다.
                #
                # 붙인 뒤에 최소 길이를 다시 강제하면 안 된다 — 그러면 애써
                # 붙여 놓은 자리에서 도로 떨어져 나온다(실측 0.07~0.3초 어긋남).
                # 경계가 좀 이르면 **짧은 리드를 그냥 받아들인다.**
                lo = tl + int(LEAD_FLOOR_S * US)
                lb = snap(tl + lead, lo=lo, hi=end - int(1.0 * US),
                          tol_us=int(LEAD_SNAP_TOL_S * US))
                cand = lb - tl
                lead = cand if cand >= int(LEAD_FLOOR_S * US) else 0
                if lead and lead >= dur - int(1.0 * US):
                    lead = 0
            if lead:
                out.append(SlidePlan(r.cue, img, tl, lead, True, 1.0, 0.0, 0.0,
                                     r.src_us))
                out.append(SlidePlan(r.cue, img, tl + lead, dur - lead, False,
                                     scale, px, py, r.src_us))
            else:
                out.append(SlidePlan(r.cue, img, tl, dur, False, scale, px, py,
                                     r.src_us))
    return out, warns


# ── 전체화면 리드 ───────────────────────────────────────────────
#
# 장표를 처음부터 작은 패널로만 띄우면 **뜬 줄도 모르고 지나간다.**
# 대표님이 손수 고친 2편을 뜯어보니 규칙이 하나였다 —
# 장표마다 먼저 **화면을 꽉 채웠다가**, 몇 초 뒤 왼쪽 패널로 내려앉힌다.
# 읽을 시간을 준 다음 얼굴에 화면을 돌려주는 것이다.
#
# 실측 — 완성본 3편에서 뽑았다 (tools/learn_style.py)
#
#   파트0 1편      3.73초  (대표님이 손으로 붙이심 · 15/24장)
#   파트0 2편      2.73초  (자동값 그대로 두심 · 25/31장)
#   오리엔테이션   2.70초  (자동값 그대로 두심 · 33/33장)
#   합산 중앙      2.97초  (범위 1.30~5.90)
#
# 2.7 을 유지한다. 대표님이 최근에 승인하신 두 편이 이 값이고, 1편의 3.73은
# 리드 규칙이 생기기 전에 손으로 붙이신 값이다. 읽을 시간을 더 주고 싶으면
# 여기만 3.0 으로 올리면 된다.
LEAD_S = 2.7
# 리드가 끝나는 자리는 더 멀리까지 뒤져서라도 경계에 붙인다.
# 붙일 데가 없으면 혼자 어중간한 데 떨어져서 제일 눈에 띈다.
LEAD_SNAP_TOL_S = 1.4
# 경계에 붙이느라 리드가 짧아지는 건 받아들인다. 이보다 짧으면 리드를 뺀다.
LEAD_FLOOR_S = 1.0
LEAD_MIN_S = 1.6
# 리드를 붙이려면 패널로 남을 시간이 이만큼은 있어야 한다
LEAD_NEED_S = 3.5


def _lead_us(dur: int, prev_full: bool) -> int:
    """이 장표에 붙일 전체화면 리드 길이(µs). 0 이면 바로 패널로.

    앞 장표가 전체화면으로 끝났으면 리드를 안 붙인다. 전체화면이 연달아
    나오면 얼굴이 오래 사라지고 넘어간 티도 안 난다 — 대표님도 ★ 바로 뒤
    장표(발견·시작)에는 리드를 안 주셨다.
    """
    if prev_full or dur < LEAD_NEED_S * US:
        return 0
    return max(int(LEAD_MIN_S * US), min(int(LEAD_S * US), int(dur * 0.45)))


# 장표는 아래 1/3 이 비어 있다 (사양서: 편집 자막 자리). 패널로 줄이면 그
# 여백까지 같이 줄어들어 글자가 실제보다 위에 붙는다. 그래서 살짝 올려 앉힌다.
PANEL_MARGIN = 0.92
PANEL_Y = 0.14


# 장표 패널 자리 — **고정값이다. 영상마다 재지 않는다.**
#
# 예전엔 빈 자리를 실측해서 그때그때 맞췄다. 그랬더니 촬영본이 바뀌자
# 패널이 절반 크기로 쪼그라들었다 (실측 scale 0.579 → 0.313, x −0.371 → −0.66).
# 화면에 뜨는 장표 크기는 **강의마다 같아야 하는 브랜드 요소**지,
# 그 회차 프레이밍에 따라 달라질 값이 아니다.
#
# 값은 대표님이 승인한 완성본에서 그대로 가져왔다 —
#   파트0 2편 0.579 / −0.371 · 파트0 1편 0.581 / −0.369 · 오리엔 0.562 / −0.39
PANEL_SCALE = 0.579
PANEL_X = -0.371


def panel_geometry(zone: SafeZone) -> tuple[float, float, float]:
    """장표 패널 자리 → (scale, transform_x, transform_y). 늘 같은 값이다.

    캡컷 `clip.scale` 은 화면 대비 배율, `clip.transform` 은 화면 절반 기준
    정규화값이다 (+x 오른쪽 / +y 위).

    `zone` 은 이제 자리를 정하는 데 안 쓰고, **사람을 가리는지 확인**하는
    데만 쓴다 (`panel_warning`).
    """
    return PANEL_SCALE, PANEL_X, PANEL_Y


def panel_warning(zone: SafeZone) -> str:
    """고정 패널이 사람을 덮을 것 같으면 알린다. 자리를 바꾸지는 않는다.

    **측정이 확실할 때만 말한다.** 촬영본에 따라 빈 자리 측정이 통째로
    실패하는데(카메라 노이즈·자동노출), 그걸로 경고를 내면 멀쩡한 화면에도
    매번 빨간불이 뜬다 — 실제로 파트1 1강에서 "빈 자리 0%"로 나왔지만
    화면에 얹어 보니 사람을 전혀 안 가렸다.
    """
    if not zone.confident:
        return ""
    right = PANEL_X + PANEL_SCALE          # 패널 오른쪽 끝 (−1~+1)
    edge = (zone.x1 - 0.5) * 2             # 빈 자리 오른쪽 끝
    if right > edge + 0.02:
        over = (right - edge) / 2 * 100
        return (f"장표 패널이 빈 자리보다 {over:.0f}% 폭만큼 오른쪽으로 넘칩니다 "
                f"— 사람이 가릴 수 있습니다 (빈 자리 {zone.x0:.0%}~{zone.x1:.0%})")
    return ""


# ── 이미지 → 클립 ────────────────────────────────────────────────
# ── 늘릴 여유 ────────────────────────────────────────────────────
#
# 장표 클립을 **필요한 길이에 딱 맞춰** 구우면 캡컷에서 늘릴 수가 없다.
# 소재가 거기서 끝나 있어서 오른쪽 끝을 잡아당겨도 안 늘어난다.
# 실제로 대표님이 "길이를 더 늘릴 수 없더라"고 하셨고, 60조각 중 52개가
# 뒤로 0초, 56개가 앞으로 0초였다.
#
# 그래서 소재를 넉넉히 굽고 **가운데를 잘라 쓴다.** 정지 화면이라 어느
# 지점을 써도 그림이 같고, 용량도 거의 안 는다.
CLIP_PAD_S = 60.0        # 앞뒤로 늘릴 수 있는 여유
CLIP_LEN_S = 180.0       # 장표 소재 한 개의 길이


def to_clip(image: Path, dur_us: int, w: int, h: int, fps: float,
            out: Path) -> Path:
    """장표 JPG 를 그 길이만큼의 mp4 로 굽는다.

    캡컷에서 **이미지 소재는 스키마를 확인한 적이 없고 영상 소재는 이미
    검증을 통과했다** (cards.to_clip 과 같은 이유). 확인된 길로 간다.
    정지 화면이라 용량은 거의 안 늘고 인코딩도 금방 끝난다.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    # 이미 구워둔 게 **장표 그림보다 새것일 때만** 재사용한다.
    #
    # 그냥 "있으면 건너뛰기"로 뒀다가 문구를 고친 장표가 옛 그림 그대로 나갔다.
    # 지우고 다시 구우려 했지만 캡컷이 그 파일을 물고 있어 삭제가 조용히 실패했고,
    # 아무도 모르게 옛것이 그대로 실렸다. 시각을 비교하면 그 일이 안 생긴다.
    if out.exists() and out.stat().st_mtime >= Path(image).stat().st_mtime:
        return out
    if out.exists():
        try:
            out.unlink()
        except OSError:
            # 캡컷이 물고 있으면 못 지운다 → 옆에 새 이름으로 굽는다
            out = out.with_name(f"{out.stem}_{int(Path(image).stat().st_mtime)}{out.suffix}")
            if out.exists():
                return out
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-i", str(image),
        "-t", f"{dur_us / US:.3f}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        # 키프레임을 촘촘히 박으면(-g 60) 파일이 터진다. 장표에는 그레인이
        # 얹혀 있어서(사양서 v7) I프레임마다 노이즈를 통째로 다시 적기 때문이다.
        # 240초 한 개가 30MB 나왔다. 정지 화면이니 키프레임은 드물어도 되고,
        # 그 사이는 "바뀐 것 없음"이라 거의 0바이트로 눌린다.
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-crf", "26", "-pix_fmt", "yuv420p", "-r", str(int(round(fps))),
        "-g", "600", "-c:a", "aac", "-b:a", "64k", str(out),
    ], check=True, timeout=900)
    return out


# ── 덱 등록부 ────────────────────────────────────────────────────
#
# 촬영본 **파일명은 믿지 않는다.** 실제로 「0주차 오티 1」의 내용이 2편이었고
# 「강의 오리엔테이션 3」이 1편이었다. 그래서 파일명이 아니라 원고 첫마디로
# 짝을 맞춘다 — 내용이 곧 신원이다.

def _resolve_vod_root() -> Path:
    """원고·촬영 대본·장표 이미지를 읽는 뿌리 폴더.

    1. 환경변수 CAPCUT_VOD_ROOT 가 있으면 그것
    2. 대표님 PC 의 G 드라이브가 보이면 그것
    3. 아니면 프로젝트 안 「VOD자료」 폴더 — 직원 PC 용. 대표님이
       「강의 묶음 만들기.bat」로 만든 zip 을 여기 풀면 같은 구조가 된다.
    """
    import os
    env = os.environ.get("CAPCUT_VOD_ROOT")
    if env:
        return Path(env)
    g = Path(r"G:\내 드라이브\BPT\트레이너 마케팅 VOD 작업")
    if g.exists():
        return g
    return Path(__file__).resolve().parent.parent / "VOD자료"


VOD_ROOT = _resolve_vod_root()
SCRIPT_DIR = VOD_ROOT / "원고"
SLIDE_DIR = VOD_ROOT / "슬라이드" / "_장표"


@dataclass
class Deck:
    key: str
    label: str
    script_md: Path
    cue_header: str
    body_start: str
    body_end: str
    slide_dir: Path
    opening: str            # 원고 첫마디 — 촬영본을 이걸로 알아본다
    sub_script: Path | None = None   # 대표님이 손으로 끊어 둔 자막 대본(.txt)

    def load(self) -> tuple[list[Cue], list[str]]:
        md = self.script_md.read_text(encoding="utf-8")
        return (parse_cue_sheet(md, self.cue_header),
                parse_script_body(md, self.body_start, self.body_end))


DECKS: list[Deck] = [
    Deck("part0_1", "파트0 1편 (OT형)",
         SCRIPT_DIR / "파트0_무료세트_OT+키_최종.md",
         "1편 (OT형)", r"## 1-3\. 원고", r"^# 2편 \(키형",
         SLIDE_DIR / "파트0_1편_OT형",
         "10년 동안 트레이너"),
    Deck("part0_2", "파트0 2편 (키형)",
         SCRIPT_DIR / "파트0_무료세트_OT+키_최종.md",
         "2편 (키형)", r"## 2-3\. 원고", r"^# 3\. 자체 반박",
         SLIDE_DIR / "파트0_2편_키형",
         "회원이 안 늘어서 고민"),
    Deck("orientation", "수강 오리엔테이션",
         SCRIPT_DIR / "수강오리엔테이션_원고_v2.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "수강오리엔테이션",
         "시작하기 전에 딱 하나만"),

    # ── 파트 1 (블로그) — 2026-08-25 촬영분부터 등록.
    #    여기 안 올려두면 deck_for() 가 None 을 돌려주고 장표가 통째로 안 붙는다.
    #    (실제로 1강 드래프트가 자막만 들어간 채로 나왔다)
    Deck("part1_01", "파트1 1강 — 회원은 이 순서로 찾아옵니다",
         SCRIPT_DIR / "파트1_블로그" / "파트1_01강_회원은_이_순서로_찾아옵니다.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_01강",
         "회원은 블로그에서 발견하고",
         SCRIPT_DIR / "파트1_블로그" / "파트1 1강 촬영 원고.txt"),
    Deck("part1_02", "파트1 2강 — 블로그를 잘하려고 하지 마세요",
         SCRIPT_DIR / "파트1_블로그" / "파트1_02강_블로그를_잘하려고_하지_마세요.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_02강",
         "제가 교육한 선생님들",
         SCRIPT_DIR / "파트1_블로그" / "파트1 2강 촬영원고.txt"),
    Deck("part1_03", "파트1 3강 — 내가 쓸 키워드 20개",
         SCRIPT_DIR / "파트1_블로그" / "파트1_03강_내가_쓸_키워드_20개.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_03강",
         "블로그 글을 쓰면서 키워드를",
         SCRIPT_DIR / "파트1_블로그" / "파트1 3강 촬영원고.txt"),
    Deck("part1_04", "파트1 4강 — 한 글에 하나, 스무 개를 순서대로",
         SCRIPT_DIR / "파트1_블로그" / "파트1_04강_한_글에_하나_스무_개를_순서대로.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_04강",
         "키워드에 대한 기본",
         SCRIPT_DIR / "파트1_블로그" / "파트1 4강 촬영원고.txt"),

    # ── 파트 1 · 5~7강 — 2026-09-10 등록 (대표님 완성 원고 기준, 촬영 전)
    Deck("part1_05", "파트1 5강 — 제목: 맨 앞, 짧게, 안 겹치게",
         SCRIPT_DIR / "파트1_블로그" / "파트1_05강_제목_맨_앞_짧게_안_겹치게.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_05강",
         "글을 정말 잘 썼는데",
         SCRIPT_DIR / "파트1_블로그" / "파트1 5강 촬영원고.txt"),
    Deck("part1_06", "파트1 6강 — 확정 타겟: 한 명만 생각하고 씁니다",
         SCRIPT_DIR / "파트1_블로그" / "파트1_06강_확정_타겟_한_명만_생각하고_씁니다.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_06강",
         "블로그를 백 개 넘게",
         SCRIPT_DIR / "파트1_블로그" / "파트1 6강 촬영원고.txt"),
    Deck("part1_07", "파트1 7강 — 천오백 자와 키워드 다섯 번의 진짜 이유",
         SCRIPT_DIR / "파트1_블로그" / "파트1_07강_천오백_자와_키워드_다섯_번의_진짜_이유.md",
         "슬라이드 큐 시트", r"^## 원고", r"^## 자체 반박",
         SLIDE_DIR / "파트1_07강",
         "블로그 배우신 분들한테",
         SCRIPT_DIR / "파트1_블로그" / "파트1 7강 촬영원고.txt"),
]


def deck_for(tr) -> Deck | None:
    """대본 첫마디로 어느 강의인지 알아낸다."""
    head = norm(" ".join(s.text for s in tr.segs[:4]))
    for d in DECKS:
        if norm(d.opening) in head:
            return d
    return None


def get_deck(key: str) -> Deck | None:
    return next((d for d in DECKS if d.key == key), None)


def build_clips(plans: list[SlidePlan], media, outdir: Path) -> list:
    """SlidePlan → draft.CardClip. 캡컷 오버레이 트랙에 그대로 얹힌다."""
    from .draft import CardClip

    outdir.mkdir(parents=True, exist_ok=True)
    clip_us = int(CLIP_LEN_S * US)
    pad_us = int(CLIP_PAD_S * US)
    clips = []
    for p in plans:
        # 장표 한 장에 소재 하나. 전체화면 토막과 패널 토막이 같은 파일을
        # 나눠 쓴다 — 정지 화면이라 어디를 써도 같은 그림이다.
        mp4 = outdir / f"slide{p.cue.index:02d}.mp4"
        to_clip(p.image, clip_us, media.width, media.height, media.fps, mp4)
        # 소재 가운데에서 잘라 쓴다 → 앞뒤로 늘릴 여유가 생긴다
        start = min(pad_us, max(0, clip_us - p.dur))
        clips.append(CardClip(start=p.start, dur=p.dur, path=mp4,
                              source_dur=clip_us, source_start=start,
                              fullscreen=p.fullscreen,
                              scale=p.scale, pos_x=p.pos_x, pos_y=p.pos_y))
    return clips
