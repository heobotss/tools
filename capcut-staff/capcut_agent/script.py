"""대본 구성 — 자막을 만들기 전에 **먼저** 전체 스크립트를 문장 단위로 세운다.

whisper 가 내주는 세그먼트는 문장이 아니다. 호흡이나 침묵을 기준으로 잘려서
한 문장이 둘로 갈라지거나 두 문장이 한 덩어리로 붙어 나온다. 그 상태로 자막을
만들면 문장 중간에서 끊긴 자막이 나온다.

그래서 순서를 이렇게 잡는다.

    ASR 세그먼트  →  문장 재구성(여기)  →  SRT  →  잔말/NG 판정  →  자막 끊기

문장이 서 있어야 그 다음이 전부 제대로 선다. NG 판정도 문장끼리 비교해야
맞고, 자막 끊는 자리도 문장 안에서 따져야 의미가 산다.
"""

from __future__ import annotations

import re
from pathlib import Path

from .transcript import Seg, Transcript, Word

US = 1_000_000

# 문장이 끝났다고 볼 표시
_SENT_END = re.compile(r"[.!?…]+$")
# 종결어미로 끝나는가 (구두점을 안 찍고 넘어간 경우)
_FINAL_ENDING = re.compile(
    r"(습니다|ㅂ니다|입니다|됩니다|합니다|어요|아요|에요|예요|네요|세요|"
    r"지요|죠|는데요|거든요|잖아요|더라고요|드립니다|십시오|하세요)$")


def _looks_finished(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return bool(_SENT_END.search(t)) or bool(_FINAL_ENDING.search(t))


def _split_sentences(seg: Seg) -> list[Seg]:
    """한 세그먼트 안에 문장이 여럿이면 나눈다.

    단어 타임스탬프가 있어야 시간을 제대로 가를 수 있다. 없으면 그대로 둔다.
    """
    text = seg.text.strip()
    if not seg.words:
        return [seg]

    # 문장이 끝나는 단어 위치를 찾는다
    cuts: list[int] = []
    for i, w in enumerate(seg.words[:-1]):
        if _SENT_END.search(w.text.strip()):
            cuts.append(i)
    if not cuts:
        return [seg]

    out: list[Seg] = []
    start_i = 0
    for c in cuts + [len(seg.words) - 1]:
        ws = seg.words[start_i:c + 1]
        if not ws:
            continue
        out.append(Seg(
            start=ws[0].start if out else seg.start,
            end=ws[-1].end,
            text=" ".join(w.text for w in ws).strip(),
            words=ws,
        ))
        start_i = c + 1
    if out:
        out[-1].end = seg.end
    return out or [seg]


def snap_to_audio(tr: Transcript, video, max_shift_s: float = 0.6,
                  onset_db: float = -34.0) -> float:
    # max_shift 를 1.2초까지 넓혀봤지만 나아지지 않았고, 넓힐수록 엉뚱한
    # 발화 시작점에 붙을 위험만 커진다. 0.6초가 실측으로 검증된 값이다
    # (문장의 67% → 22% 로 개선).
    """대본 시각을 **실제 소리**에 맞춰 당긴다.

    whisper 의 낱말 시각은 생각보다 많이 어긋난다. 이 영상 실측으로
    문장 첫 낱말이 실제 발화 시작과 **중앙 0.12초, 최대 1.08초** 차이가 났고
    69문장 중 46개(67%)가 0.1초를 넘겼다. 그대로 쓰면 자막이 말보다 먼저
    뜨거나 늦게 뜬다.

    낱말 하나씩 옮기면 문장 안 리듬이 망가지므로, **문장 단위로 통째로**
    민다. 문장 첫 낱말을 가장 가까운 발화 시작점에 맞추고 그 차이만큼
    문장 전체를 옮긴다.

    돌려주는 값: 평균 보정량(초).
    """
    import numpy as np

    from .align import features

    trk = features(video)
    loud = trk.db > onset_db
    if loud.size < 2:
        return 0.0
    # 조용→소리로 바뀌는 지점 = 발화 시작
    onsets = np.flatnonzero((~loud[:-1]) & loud[1:]) + 1
    if onsets.size == 0:
        return 0.0

    limit = int(max_shift_s * 100)          # features 는 100fps
    shifts = []
    for s in tr.segs:
        if not s.words:
            continue
        f = s.words[0].start // 10_000
        near = onsets[np.argmin(np.abs(onsets - f))]
        d = int(near - f)
        if abs(d) > limit:
            continue                        # 너무 멀면 엉뚱한 곳이다
        shift = d * 10_000
        s.start += shift
        s.end += shift
        for w in s.words:
            w.start += shift
            w.end += shift
        s.holes = [(a + shift, b + shift) for a, b in s.holes]
        s.media_holes = [(a + shift, b + shift) for a, b in s.media_holes]
        shifts.append(abs(d) / 100)
    return float(sum(shifts) / len(shifts)) if shifts else 0.0


def redistribute_words(tr: Transcript, video, voice_db: float = -34.0) -> int:
    """낱말을 문장 안 **실제 소리 구간**에 다시 배치한다.

    whisper 는 낱말 시각을 대충 나눠주기 때문에, 침묵 한복판에 낱말이 찍히는
    일이 생긴다 (실측 914개 중 9개가 소리 없는 구간에 있었다). 그러면
    자막이 말보다 먼저 뜨고, 낱말 보호가 엉뚱한 곳을 지키고, 무음컷이
    그 낱말을 지워버린다.

    문장 구간에서 소리 나는 부분만 추려 이어 붙인 뒤, **음절 수에 비례해**
    낱말을 나눠 담는다. 완벽한 강제정렬은 아니지만 "낱말이 침묵에 있다" 는
    문제는 원천적으로 없앤다 — 소리 있는 곳에만 배치하니까.

    돌려주는 값: 위치를 옮긴 낱말 수.
    """
    import re

    import numpy as np

    from .align import features

    han = re.compile(r"[가-힣A-Za-z0-9]")
    trk = features(video)
    loud = trk.db > voice_db
    moved = 0

    for s in tr.segs:
        if len(s.words) < 2:
            continue
        f0, f1 = s.start // 10_000, min(s.end // 10_000 + 1, len(loud))
        if f1 - f0 < len(s.words):
            continue
        voiced = np.flatnonzero(loud[f0:f1])
        if voiced.size < len(s.words) * 2:
            continue                       # 소리가 너무 적으면 손대지 않는다

        weights = [max(1, len(han.findall(w.text))) for w in s.words]
        total = sum(weights)
        acc = 0
        for w, k in zip(s.words, weights):
            a = int(acc / total * voiced.size)
            b = int((acc + k) / total * voiced.size)
            b = max(b, a + 1)
            new_s = int((f0 + voiced[a]) * 10_000)
            new_e = int((f0 + voiced[min(b, voiced.size - 1)]) * 10_000)
            if abs(new_s - w.start) > 60_000:      # 0.06초 넘게 움직이면
                moved += 1
            w.start, w.end = new_s, max(new_e, new_s + 10_000)
            acc += k
    return moved


def build_sentences(tr: Transcript, max_join_gap_s: float = 0.7) -> Transcript:
    """ASR 세그먼트를 문장 단위로 다시 세운다.

    · 끝나지 않은 세그먼트는 다음 것과 잇는다 (사이가 짧을 때만)
    · 한 세그먼트에 문장이 여럿이면 나눈다
    """
    # 1) 여러 문장이 붙어 있으면 먼저 나눈다
    flat: list[Seg] = []
    for s in tr.segs:
        flat.extend(_split_sentences(s))

    # 2) 안 끝난 문장은 다음과 잇는다
    gap_limit = int(max_join_gap_s * US)
    merged: list[Seg] = []
    for s in flat:
        if merged:
            prev = merged[-1]
            gap = s.start - prev.end
            if not _looks_finished(prev.text) and 0 <= gap <= gap_limit:
                prev.end = s.end
                prev.text = f"{prev.text} {s.text}".strip()
                prev.words = prev.words + s.words
                continue
        merged.append(Seg(start=s.start, end=s.end, text=s.text,
                          words=list(s.words)))

    return Transcript(segs=merged, language=tr.language,
                      duration_us=tr.duration_us, model=tr.model)


# ── SRT ──────────────────────────────────────────────────────────
def _ts(us: int) -> str:
    us = max(0, int(us))
    ms = us // 1000
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(items: list[tuple[int, int, str]]) -> str:
    """(시작µs, 끝µs, 텍스트) 목록을 SRT 문자열로."""
    out = []
    for i, (a, b, text) in enumerate(items, 1):
        if b <= a:
            b = a + 1000
        out.append(f"{i}\n{_ts(a)} --> {_ts(b)}\n{text.strip()}\n")
    return "\n".join(out)


def transcript_to_srt(tr: Transcript, only_kept: bool = False) -> str:
    """대본 전체를 SRT 로. 원본 영상 시간축 기준이다."""
    items = []
    for s in tr.segs:
        if only_kept and s.dropped:
            continue
        text = s.effective_text() if not s.dropped else s.text.strip()
        if not text:
            continue
        mark = "" if s.kind == "speech" else f"  [{s.reason or s.kind}]"
        items.append((s.start, s.end, text + mark))
    return to_srt(items)


def write_srt(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # SRT 는 BOM 이 있어야 알아보는 프로그램이 많다
    path.write_text(content, encoding="utf-8-sig")
    return path
