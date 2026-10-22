"""효과음 — **전환음만** 자동으로 깐다.

대표님 완성본(2026-08-18)을 역산해보니 효과음이 두 층으로 나뉘어 있었다.

  전환 층   휙 · 훅 · Pop Whoosh · Punch (22개) — 장면이 바뀌는 자리
  의미 층   오답 · 땡 · Bell · 정답 (약 10개) — "그만두게 되는 거예요" 같은
            **부정 판단**에 붙는다

의미 층은 빨강/파랑 강조와 **같은 축**이다. 어디가 부정이고 어디가 전환점인지
글 전체를 읽고 판단해야 하는 일이라, 규칙으로 고르면 어긋난다 — 강조 자막에서
이미 겪었다. 그래서 여기서는 **전환 층만** 깔고 의미 층은 대표님께 남긴다.

밀도도 절제한다. 대표님은 평균 6.5초에 하나를 쓰시는데 그중 절반이 의미 층이다.
자동으로 까는 건 그보다 성기게 두고, 대표님이 얹을 자리를 비워둔다.

**소리는 귀가 아니라 스펙트럼으로 고른다.** 처음 넣었던 '转场 E8' 을 대표님이
"안 들린다" 하셔서 재보니, 평균 음량은 오히려 큰 축(-19dB)인데 에너지의 **87.5%
가 300Hz 아래** 초저역이었다. 폰·노트북 스피커가 못 내는 대역이라 커도 안 들린다.
그래서 말대역(300~3.5k)이나 고역에 실린 소리만 쓴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

US = 1_000_000
PALETTE = Path(__file__).parent / "templates" / "sfx_palette.json"

# 컷 하나를 "눈에 띄는 점프" 로 볼 최소 삭제량.
# 0.3초를 지운 컷은 티가 잘 안 나고, 2초를 지운 컷은 확 튄다.
MIN_JUMP_US = 900_000
MIN_APART_US = 9_000_000     # 효과음끼리 이만큼은 떨어뜨린다
OPENING_GUARD_US = 8_000_000
MAX_PER_MIN = 5.0            # 1분에 이보다 많이 깔지 않는다


@dataclass
class SfxClip:
    """타임라인(배속 적용 뒤) 좌표에 놓이는 효과음."""
    at_us: int
    name: str
    dur_us: int


def load_palette() -> dict[str, dict]:
    if not PALETTE.exists():
        return {}
    pal = json.loads(PALETTE.read_text(encoding="utf-8"))
    # 파일이 실제로 있는 것만 쓴다. 없는 걸 걸면 캡컷에서 빨간 클립이 된다.
    return {k: v for k, v in pal.items() if Path(v.get("path", "")).exists()}


def plan(keeps, speed: float, total_us: int,
         palette: dict[str, dict] | None = None) -> list[SfxClip]:
    """컷 사이에 지워진 양이 큰 자리부터 전환음을 깐다.

    keeps 는 원본 좌표의 남긴 구간. 앞 구간 끝과 다음 구간 시작 사이가
    **실제로 지워진 길이**이고, 그게 클수록 화면이 튄다.
    """
    pal = palette if palette is not None else load_palette()
    if not pal or len(keeps) < 2:
        return []

    # 이름을 고정 순서로 돌려써서 같은 소리가 연달아 나오지 않게 한다
    names = [n for n in ("휙", "훅", "Pop Whoosh", "Punch sound_1") if n in pal]
    if not names:
        names = sorted(pal)

    out_at = 0
    cands: list[tuple[int, int]] = []          # (지워진 양, 타임라인 위치)
    for i in range(len(keeps) - 1):
        out_at += keeps[i].end - keeps[i].start
        gap = keeps[i + 1].start - keeps[i].end
        if gap < MIN_JUMP_US:
            continue
        at = int(round(out_at / speed))
        if at < OPENING_GUARD_US or at >= total_us:
            continue
        cands.append((gap, at))

    budget = int(total_us / US / 60 * MAX_PER_MIN)
    cands.sort(reverse=True)                    # 크게 튀는 컷부터
    picked: list[int] = []
    for _, at in cands:
        if len(picked) >= budget:
            break
        if any(abs(at - p) < MIN_APART_US for p in picked):
            continue
        picked.append(at)

    picked.sort()
    return [SfxClip(at_us=at, name=names[i % len(names)],
                    dur_us=int(pal[names[i % len(names)]]["duration"]))
            for i, at in enumerate(picked)]
