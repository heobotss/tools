"""대본에서 그래픽 카드를 만든다 — 소재 없이.

앉아서 말하는 화면만 계속 이어지면 지루하다. 그렇다고 아무 스톡 영상을 끼우면
말과 겉돈다. 대신 **하는 말 자체를 화면으로 만든다.**

카드는 PNG 로 그린 뒤 짧은 mp4 로 굽는다. 캡컷 드래프트에서 이미지 소재는
형식을 확인한 적이 없지만 영상 소재는 이미 179건 검증을 통과했다 — 확인된
길을 쓴다.

만드는 종류
  keyword  한 문장을 크게 (핵심 문구)
  list     "세 가지입니다" 같은 열거를 1·2·3 으로
  ratio    "10명 중 1명" 같은 비율을 아이콘으로
  quote    한 문장 인용
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import korean
from .style import StyleProfile, emphasis_font_for_pillow, hex_to_rgb

BG = (10, 10, 10)
FG = (255, 255, 255)


@dataclass
class Card:
    kind: str          # keyword | list | ratio | quote
    at_us: int         # 원본 기준 등장 시각
    dur_us: int
    title: str
    items: list[str]
    accent: str        # 강조색 (#ec1d1d / #247bff)
    weight: int = 0    # 고를 때 쓰는 우선순위
    fullscreen: bool = True


# ── 대본에서 후보 뽑기 ────────────────────────────────────────────
_LIST_HINT = re.compile(r"(두|세|네|다섯|여러|몇)\s*가지|(첫|두|세|네)\s*번째")
# "10명 중 1명", "10명 중에 한두 명" 둘 다 잡는다.
# 한국어는 숫자를 말로 쓰는 일이 흔해서 숫자만 찾으면 절반을 놓친다.
_RATIO = re.compile(r"(\d+)\s*명?\s*중(?:에)?\s*(\d+|한두|한|두|세|서너|대여섯)")
_KO_NUM = {"한": 1, "두": 2, "세": 3, "한두": 2, "서너": 3, "대여섯": 5}


def _clauses(text: str) -> list[str]:
    """문장을 절 단위로 나눈다.

    자막용 줄바꿈을 그대로 쓰면 안 된다. 자막은 앞뒤가 이어서 읽히지만
    **카드는 그 자체로 완결돼야** 한다. 자막 단위로 자르면 "데 훨씬 빠르고"
    같은 토막이 화면에 뜬다.

    절은 연결어미나 쉼표에서 끝난다 — 거기서 끊으면 혼자서도 말이 된다.
    """
    out, cur = [], []
    for w in text.split():
        cur.append(w)
        bare = re.sub(r"[^가-힣A-Za-z0-9]+$", "", w)
        ends_clause = (w.endswith((",", ".", "!", "?"))
                       or (len(bare) >= 3
                           and bare.endswith(korean.CONNECTIVE_ENDINGS))
                       or (len(bare) >= 3
                           and bare.endswith(korean.FINAL_ENDINGS)))
        if ends_clause:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return [c.strip() for c in out if c.strip()]


def _key_phrase(text: str, max_chars: int) -> str:
    """긴 문장에서 화면에 띄울 핵심 구절만 뽑는다.

    문장 전체를 넣으면 글씨가 깨알같아지고, 길다고 버리면 좋은 문장이 다
    날아간다 — 이 대본은 문장 중앙 길이가 47자다.
    """
    from .style import score

    text = " ".join(text.split())
    if len(text) <= max_chars * 1.6:
        return text

    cands = [c for c in _clauses(text) if 6 <= len(c) <= max_chars * 1.8]
    if not cands:
        return text[:int(max_chars * 1.8)].rsplit(" ", 1)[0]
    best = max(cands, key=lambda c: (score(c)[1], -abs(len(c) - max_chars)))
    return best


def find_cards(segs, profile: StyleProfile, max_cards: int = 12,
               min_gap_s: float = 25.0) -> list[Card]:
    """대본 문장들에서 카드로 만들 만한 것을 고른다.

    segs: kind/reason 이 채워진 Seg 목록 (컷된 것은 알아서 건너뛴다)
    min_gap_s: 카드끼리 최소 간격. 몰아서 넣으면 흐름이 끊긴다.
    """
    from .style import score

    out: list[Card] = []
    min_gap_us = int(min_gap_s * 1_000_000)

    for s in segs:
        if s.dropped or not s.text.strip():
            continue
        t = s.text.strip()
        kind, strength = score(t)
        accent = profile.color_neg if kind == "neg" else profile.color_pos

        m = _RATIO.search(t)
        if m:
            total = int(m.group(1))
            part = _KO_NUM.get(m.group(2)) or int(m.group(2) or 1)
            out.append(Card("ratio", s.start, s.dur,
                            f"{total}명 중 {part}명", [str(total), str(part)],
                            accent, strength + 6))
            continue

        if _LIST_HINT.search(t):
            out.append(Card("list", s.start, s.dur, _key_phrase(t, 16), [],
                            accent, strength + 4))
            continue

        # 긴 문장은 버리지 않고 핵심 구절만 뽑아 쓴다
        if strength >= 3:
            out.append(Card("keyword", s.start, s.dur, _key_phrase(t, 13), [],
                            accent, strength))

    # 센 것부터 남기되, 한곳에 몰리지 않게 최소 간격을 둔다
    out.sort(key=lambda c: -c.weight)
    picked: list[Card] = []
    for c in out:
        if len(picked) >= max_cards:
            break
        if all(abs(c.at_us - p.at_us) >= min_gap_us for p in picked):
            picked.append(c)
    picked.sort(key=lambda c: c.at_us)
    return picked


# ── 그리기 ───────────────────────────────────────────────────────
def _font(size: int) -> ImageFont.FreeTypeFont:
    # 캡컷에 넘기는 경로와 달리 여기서는 **진짜로 열려야** 한다.
    # 캐시 파일명이 NFD 라 그대로 열면 실패한다 (style.resolve_on_disk 참고).
    p = emphasis_font_for_pillow()
    try:
        if p:
            return ImageFont.truetype(p, size)
        raise OSError
    except OSError:
        for c in (r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf"):
            if Path(c).exists():
                return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _wrap(text: str, max_chars: int) -> list[str]:
    ws = text.split()
    return [" ".join(ws[i] for i in g) for g in korean.chunk(ws, max_chars)]


def render(card: Card, w: int, h: int, out: Path) -> Path:
    """카드 한 장을 PNG 로."""
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    acc = tuple(int(v * 255) for v in hex_to_rgb(card.accent))

    if card.kind == "ratio" and len(card.items) == 2:
        total, part = int(card.items[0]), int(card.items[1])
        total = min(total, 20)
        part = min(part, total)
        r = int(h * 0.055)
        gap = int(r * 0.9)
        cols = min(total, 10)
        rows = (total + cols - 1) // cols
        tw = cols * (r * 2 + gap) - gap
        x0 = (w - tw) // 2
        y0 = int(h * 0.42) - (rows * (r * 2 + gap)) // 2
        for i in range(total):
            cx = x0 + (i % cols) * (r * 2 + gap) + r
            cy = y0 + (i // cols) * (r * 2 + gap) + r
            fill = acc if i < part else (48, 48, 48)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        f = _font(int(h * 0.09))
        _center(d, card.title, f, w, int(h * 0.68), FG)

    elif card.kind == "list":
        f = _font(int(h * 0.075))
        lines = _wrap(card.title, 16)
        y = int(h * 0.30)
        for i, ln in enumerate(lines[:4]):
            bar_h = int(h * 0.085)
            d.rounded_rectangle([int(w * 0.14), y, int(w * 0.86), y + bar_h],
                                radius=bar_h // 4, fill=(24, 24, 24))
            d.rectangle([int(w * 0.14), y, int(w * 0.155), y + bar_h], fill=acc)
            d.text((int(w * 0.20), y + bar_h // 2), ln, font=f, fill=FG,
                   anchor="lm")
            y += bar_h + int(h * 0.03)

    else:  # keyword / quote
        f = _font(int(h * 0.10))
        lines = _wrap(card.title, 13)
        total_h = len(lines) * int(h * 0.13)
        y = (h - total_h) // 2
        for ln in lines[:4]:
            _center(d, ln, f, w, y, acc if len(lines) == 1 else FG)
            y += int(h * 0.13)
        d.rectangle([int(w * 0.42), int(h * 0.80), int(w * 0.58), int(h * 0.806)],
                    fill=acc)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _center(d: ImageDraw.ImageDraw, text: str, f, w: int, y: int, color) -> None:
    d.text((w // 2, y), text, font=f, fill=color, anchor="ma")


# 도입부에는 아무것도 얹지 않는다. 시작하자마자 그림이 튀어나오면
# 무슨 영상인지 파악하기 전에 시선을 뺏긴다.
OPENING_GUARD_S = 8.0


def find_stock(segs, keeps, media, speed: float, outdir: Path,
               show_s: float = 2.6, min_gap_s: float = 22.0,
               max_items: int = 14, plan_path: str = "") -> list:
    """대본 주제에 맞는 스톡 사진을 받아 오버레이 클립으로 만든다.

    말과 겉도는 그림은 안 넣느니만 못하다. 주제어 사전에 걸리는 문장만
    쓰고, 억지 번역은 하지 않는다 (stock.TOPIC_MAP 참고).
    """
    from .draft import CardClip
    from .stock import (api_key, download, load_image_plan, pick_queries,
                        search_photo)

    key = api_key()
    if not key:
        return []
    plan = load_image_plan(plan_path) if plan_path else {}

    offs, acc = [], 0
    for k in keeps:
        offs.append(acc)
        acc += k.dur

    def to_timeline(src_us: int) -> int | None:
        for k, off in zip(keeps, offs):
            if k.start <= src_us < k.end:
                return int((off + (src_us - k.start)) / speed)
        return None

    outdir.mkdir(parents=True, exist_ok=True)
    dur_us = int(show_s * 1_000_000)
    gap_us = int(min_gap_s * 1_000_000)
    used: dict[str, Path] = {}
    out: list = []

    for s in segs:
        if s.dropped or not s.text.strip():
            continue
        start = to_timeline(s.start)
        if start is not None and start < OPENING_GUARD_S * 1_000_000:
            continue
        if start is None:
            continue
        if out and start - out[-1].start < gap_us:
            continue                       # 너무 붙으면 정신없다
        qs = [q for q in pick_queries(s.text, plan=plan) if q not in used]
        if not qs:
            continue
        q = qs[0]
        hit = search_photo(q, key)
        if not hit:
            continue
        jpg = download(hit, outdir / (q.replace(" ", "_") + ".jpg"))
        if not jpg:
            continue
        mp4 = outdir / (q.replace(" ", "_") + ".mp4")
        if not mp4.exists():
            _photo_to_clip(jpg, dur_us, media.width, media.height, media.fps, mp4)
        used[q] = jpg
        # 전체화면으로 띄운다. 구석에 작게 얹으면 뭔지 알아보기도 전에
        # 지나가고, 얼굴을 가려서 시선만 분산된다. 볼 값어치가 있는
        # 그림이면 잠깐이라도 화면을 내주는 게 낫다.
        out.append(CardClip(start=start, dur=dur_us, path=mp4,
                            source_dur=dur_us, fullscreen=True))
        if len(out) >= max_items:
            break
    return out


def _photo_to_clip(jpg: Path, dur_us: int, w: int, h: int, fps: float,
                   out: Path) -> Path:
    """사진을 살짝 확대되는 짧은 mp4 로. 정지 이미지는 화면이 죽어 보인다."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-i", str(jpg), "-t", f"{dur_us / 1_000_000:.3f}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
        "-vf", (f"scale={w * 2}:-2,crop={w}:{h},"
                f"zoompan=z='min(zoom+0.0008,1.12)':d={int(dur_us/1e6*fps)}"
                f":s={w}x{h}:fps={fps}"),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(out),
    ], check=True)
    return out


def render_cards(cards: list[Card], keeps, media, speed: float,
                 outdir: Path, show_s: float = 2.4) -> list:
    """카드를 그려 mp4 로 굽고, 타임라인 좌표를 붙여 돌려준다.

    카드가 뜰 자리는 대본에서 정한 **원본 시각**이다. 컷과 배속을 거치면
    그 시각이 타임라인 어디로 갔는지 다시 찾아야 한다. 안 그러면 카드가
    엉뚱한 말 위에 뜬다.

    C(섞어쓰기): 비율·목록처럼 정보량이 있는 건 전체화면, 한 문장짜리
    강조는 오버레이로 띄운다. 얼굴이 계속 보이는 편이 자연스럽다.
    """
    from .draft import CardClip

    # 원본시간 → 타임라인시간 표 만들기
    offs, acc = [], 0
    for k in keeps:
        offs.append(acc)
        acc += k.dur

    def to_timeline(src_us: int) -> int | None:
        for k, off in zip(keeps, offs):
            if k.start <= src_us < k.end:
                return int((off + (src_us - k.start)) / speed)
        return None

    outdir.mkdir(parents=True, exist_ok=True)
    dur_us = int(show_s * 1_000_000)
    out: list = []
    for i, c in enumerate(cards):
        start = to_timeline(c.at_us)
        if start is None:                    # 그 대사가 통째로 잘렸다면 카드도 뺀다
            continue
        if start < OPENING_GUARD_S * 1_000_000:
            continue                         # 도입부는 비워둔다
        png = outdir / f"card{i:02d}.png"
        mp4 = outdir / f"card{i:02d}.mp4"
        render(c, media.width, media.height, png)
        if not mp4.exists():
            to_clip(png, dur_us, media.width, media.height, media.fps, mp4)
        # 카드는 전부 전체화면. 구석에 얹으면 글씨가 안 읽힌다.
        out.append(CardClip(start=start, dur=dur_us, path=mp4,
                            source_dur=dur_us, fullscreen=True))
    out.sort(key=lambda x: x.start)

    # 겹치면 캡컷에서 로드가 깨진다
    for i in range(len(out) - 1):
        over = (out[i].start + out[i].dur) - out[i + 1].start
        if over > 0:
            out[i].dur -= over
    return [c for c in out if c.dur > 0]


def to_clip(png: Path, dur_us: int, w: int, h: int, fps: float, out: Path) -> Path:
    """PNG 를 짧은 mp4 로. 캡컷에서 영상 소재로 다루면 스키마 위험이 없다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-i", str(png), "-t", f"{dur_us / 1_000_000:.3f}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
        "-shortest", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(int(round(fps))),
        "-vf", f"scale={w}:{h}", "-c:a", "aac", str(out),
    ], check=True)
    return out
