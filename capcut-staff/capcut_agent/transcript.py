"""대본 데이터 모델.

핵심 원칙: **전체 대본(script)을 먼저 만들고, 그걸로 NG/자막을 판단한다.**
단어 단위로 잘라 판단하면 문맥이 사라져서 "그"(지시어)와 "그…"(잔말)를 구분 못 한다.

시간 단위는 전부 µs 정수.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

US = 1_000_000


@dataclass
class Word:
    start: int
    end: int
    text: str

    @property
    def dur(self) -> int:
        return self.end - self.start


@dataclass
class Seg:
    """한 문장/호흡 단위. ASR 세그먼트 하나에 대응."""
    start: int
    end: int
    text: str
    words: list[Word] = field(default_factory=list)
    # 후처리에서 채워지는 것들
    kind: str = "speech"       # speech | filler | ng
    reason: str = ""           # 왜 잘렸는지 (사용자에게 보여줄 근거)
    # 세그먼트 안에서 부분적으로 파낼 구간 (앞뒤 잔말). 원본시간 기준.
    # 여기 든 낱말은 **자막에서도 빠진다** — 안 한 말이니까.
    holes: list[tuple[int, int]] = field(default_factory=list)
    # 영상만 잘라내고 **자막 글자는 남기는** 구간.
    #
    # 말더듬(재테이크)이 여기 해당한다. "아무리 좋은… 아무리 좋은 물을 부어도"
    # 에서 앞 시도를 잘라내도, 살아남은 테이크가 같은 말을 하므로 자막은
    # 온전해야 한다. holes 로 처리하면 자막이 "물을 부어도" 로 시작해버린다.
    media_holes: list[tuple[int, int]] = field(default_factory=list)

    @property
    def dur(self) -> int:
        return self.end - self.start

    @property
    def dropped(self) -> bool:
        return self.kind != "speech"

    def effective_text(self) -> str:
        """가장자리 잔말을 떼어낸 뒤 실제로 자막에 나갈 문장."""
        if not self.holes or not self.words:
            return self.text.strip()
        kept = [w for w in self.words
                if not any(a <= w.start < b for a, b in self.holes)]
        return " ".join(w.text for w in kept).strip() or self.text.strip()


@dataclass
class Transcript:
    segs: list[Seg]
    language: str = "ko"
    duration_us: int = 0
    model: str = ""

    @property
    def script(self) -> str:
        """전체 대본. NG 판정과 사용자 확인의 기준이 되는 원문."""
        return "\n".join(s.text.strip() for s in self.segs if s.text.strip())

    @property
    def kept_script(self) -> str:
        """컷 이후 실제로 남는 대본."""
        return "\n".join(t for s in self.segs if not s.dropped
                         for t in [s.effective_text()] if t)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "duration_us": self.duration_us,
            "model": self.model,
            "segs": [
                {"start": s.start, "end": s.end, "text": s.text, "kind": s.kind,
                 "reason": s.reason, "holes": [list(h) for h in s.holes],
                 "media_holes": [list(h) for h in s.media_holes],
                 "eff_text": s.effective_text(),
                 "words": [[w.start, w.end, w.text] for w in s.words]}
                for s in self.segs
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        segs = [
            Seg(start=s["start"], end=s["end"], text=s["text"],
                kind=s.get("kind", "speech"), reason=s.get("reason", ""),
                holes=[(h[0], h[1]) for h in s.get("holes", [])],
                media_holes=[(h[0], h[1]) for h in s.get("media_holes", [])],
                words=[Word(w[0], w[1], w[2]) for w in s.get("words", [])])
            for s in d["segs"]
        ]
        return cls(segs=segs, language=d.get("language", "ko"),
                   duration_us=d.get("duration_us", 0), model=d.get("model", ""))


# ── 한국어 정규화 ────────────────────────────────────────────────
_PUNCT = re.compile(r"[.,!?~…\-–—\"'’“”()\[\]{}·:;]+")
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """비교용 정규화 — 구두점/공백 제거. NG(재테이크) 중복 판정에 쓴다."""
    t = _PUNCT.sub(" ", text)
    t = _SPACE.sub(" ", t)
    return t.strip().lower()


def normalize_nospace(text: str) -> str:
    """띄어쓰기까지 무시. ASR 은 같은 말도 띄어쓰기를 다르게 뱉는다."""
    return normalize(text).replace(" ", "")
