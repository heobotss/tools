"""Pexels 에서 대본에 맞는 사진·영상을 가져온다.

대본은 한국어인데 Pexels 검색은 영어라야 쓸 만하다. 그래서 문장을 그대로
넘기지 않고 **주제어를 영어로 옮겨** 찾는다. ("헬스장" → gym 처럼)

라이선스 (2026-08 확인): 상업적 사용 무료, 출처 표기 의무 없음.
금지 — 등장인물을 나쁘게 묘사, 미변형 재판매, 다른 스톡 사이트에 재배포,
상표로 사용. 이 도구는 영상 안에 삽입하는 용도라 전부 허용 범위다.

키는 코드에 박지 않고 프로젝트 루트의 .env 에서 읽는다.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.pexels.com/v1/search"
VIDEO_API = "https://api.pexels.com/videos/search"
ROOT = Path(__file__).parent.parent


def api_key() -> str:
    key = os.environ.get("PEXELS_API_KEY", "")
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PEXELS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


# 이 채널 주제(트레이너 마케팅)에서 자주 나오는 말 → 영어 검색어.
# 없는 말은 그냥 건너뛴다. 어설프게 번역해서 엉뚱한 그림을 붙이느니
# 아무것도 안 붙이는 쪽이 낫다.
TOPIC_MAP = {
    "헬스장": "modern gym interior",
    "센터": "fitness studio interior",
    "트레이너": "personal trainer coaching",
    "회원": "gym member workout",
    "운동": "workout training",
    "수업": "personal training session",
    "자격증": "certificate document desk",
    "세미나": "seminar audience presentation",
    "마케팅": "marketing strategy desk",
    "블로그": "writing blog laptop",
    "인스타": "smartphone social media",
    "인스타그램": "smartphone social media",
    "리뷰": "five star review phone",
    "sns": "social media phone",
    "통장": "calculator finance desk",
    "잔고": "calculator finance desk",
    "고민": "thinking worried person",
    "상담": "consultation meeting",
    "예약": "appointment calendar",
    "신규": "handshake welcome",
    "재등록": "calendar planning",
}


@dataclass
class StockHit:
    query: str
    url: str
    photographer: str
    page: str
    path: Path | None = None


# Pexels 앞단의 Cloudflare 가 기본 User-Agent(Python-urllib)를 막는다.
# 키가 멀쩡해도 403 error code 1010 이 돌아온다 — 브라우저처럼 보여야 통과한다.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": key, "User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def load_image_plan(path: str | Path) -> dict[str, str]:
    """장면별 검색어 계획을 불러온다.

    낱말 사전만으로는 "헬스장" 이 나오면 헬스장 사진을 붙이는 수준에 그친다.
    "밑 빠진 독", "몇 년이 그냥 지나가버리는" 같은 대목에 무엇이 보여야 하는지는
    **글 전체를 읽어야** 안다. 그래서 읽고 정한 결과를 파일로 받아 쓴다.

    형식: {"문장 앞부분": "english search query", ...}
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {_key(k): v for k, v in data.items() if isinstance(v, str) and v}


def _key(text: str) -> str:
    return "".join(text.split())[:24]


def pick_queries(text: str, limit: int = 2,
                 plan: dict[str, str] | None = None) -> list[str]:
    """문장에서 검색어를 뽑는다. 계획에 있으면 그걸 먼저 쓴다."""
    if plan:
        q = plan.get(_key(text))
        return [q] if q else []
    flat = text.replace(" ", "")
    hits = [q for k, q in TOPIC_MAP.items() if k in flat]
    seen, out = set(), []
    for q in hits:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:limit]


def search_photo(query: str, key: str, orientation: str = "landscape",
                 verbose: bool = False) -> StockHit | None:
    url = f"{API}?{urllib.parse.urlencode({'query': query, 'per_page': 3, 'orientation': orientation})}"
    try:
        data = _get(url, key)
    except Exception as e:                      # 조용히 삼키면 원인을 못 찾는다
        if verbose:
            print(f"    [검색 실패] {query}: {type(e).__name__} {e}")
        return None
    for p in data.get("photos", []):
        src = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if src:
            return StockHit(query=query, url=src,
                            photographer=p.get("photographer", ""),
                            page=p.get("url", ""))
    return None


def download(hit: StockHit, dest: Path) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        hit.path = dest
        return dest
    try:
        req = urllib.request.Request(hit.url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            f.write(r.read())
    except Exception:
        return None
    hit.path = dest
    return dest
