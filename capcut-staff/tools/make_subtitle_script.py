"""촬영대본을 **한 줄 = 자막 하나**로 나눈다.

2026-09-16 대표님 결정 — 풀링·키 촬영대본은 처음부터 자막 줄 단위로 만든다.
대표님은 그 줄을 프롬프터에서 그대로 읽고, 캡컷 에이전트는 그 줄바꿈을 그대로
자막으로 쓴다. 규칙으로 쪼개면 대표님 손질과 문장 통째 일치가 73~76% 에서 멈췄다
(강조를 위해 끊는 자리는 규칙이 못 맞춘다). 줄을 대표님 눈앞에 먼저 보여 드리면
고칠 곳은 촬영 전에 고치실 수 있다.

    python tools/make_subtitle_script.py 대본.txt              → 같은 파일을 자막 줄로 (원본은 .문장.txt 로 보관)
    python tools/make_subtitle_script.py 대본.txt --out 새.txt
    python tools/make_subtitle_script.py 대본.txt --check      → 긴 줄만 알려주고 안 고침

규칙
- 한 줄 **23자 안팎**(공백 포함). 문장이 짧으면 그대로, 길면 대표님 끊기 규칙
  (korean.chunk — 대표님 손질본 두 편으로 맞춘 점수표)으로 나눈다
- 쉼표는 지우지 않는다 — 대표님이 호흡을 표시한 자리라 끊는 점수가 높다
- 빈 줄(호흡·문단)은 그대로 둔다
- 한 줄에 문장이 둘 붙어 있으면 먼저 문장끼리 떼고 나서 나눈다
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent import korean  # noqa: E402

MAX = 23
HARD_MAX = 29          # subscript.SCRIPT_MAX_CHARS — 이걸 넘으면 프로그램이 다시 쪼갠다

_SENT = re.compile(r"(?<=[.?!])\s+")


def split_line(line: str, max_chars: int = MAX) -> list[str]:
    line = " ".join(line.split())
    if not line:
        return [""]
    out: list[str] = []
    for sent in _SENT.split(line):
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        words = sent.split()
        for g in korean.chunk(words, max_chars):
            out.append(" ".join(words[i] for i in g))
    return out


def convert(text: str, max_chars: int = MAX) -> str:
    rows: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            if rows and rows[-1] != "":
                rows.append("")
            continue
        rows.extend(split_line(raw, max_chars))
    while rows and rows[-1] == "":
        rows.pop()
    return "\n".join(rows) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out")
    ap.add_argument("--max", type=int, default=MAX)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    src = Path(a.path)
    text = src.read_text(encoding="utf-8-sig")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    long_ = [ln for ln in lines if len(ln.strip()) > a.max]
    if a.check:
        print(f"{src.name}: 줄 {len(lines)} · {a.max}자 넘는 줄 {len(long_)}")
        for ln in long_[:30]:
            print(f"  {len(ln.strip()):>2}자  {ln.strip()}")
        return 0

    new = convert(text, a.max)
    out = Path(a.out) if a.out else src
    if not a.out:
        backup = src.with_name(src.stem + ".문장.txt")
        if not backup.exists():
            shutil.copy2(src, backup)
    out.write_text(new, encoding="utf-8")
    nl = [ln for ln in new.splitlines() if ln.strip()]
    print(f"{src.name}: {len(lines)}줄 → {len(nl)}줄 · 최장 {max(len(x) for x in nl)}자 · "
          f"{HARD_MAX}자 넘는 줄 {sum(1 for x in nl if len(x) > HARD_MAX)}")
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
