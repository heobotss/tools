"""장표 다시 깔기 — 클릭해서 쓰는 메뉴.

캡컷에서 컷·자막을 손보신 뒤 이걸 돌리면, 그 작업은 그대로 두고
**장표만** 지금 형식으로 다시 깔아 새 드래프트를 만든다.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capcut_agent.config import find_draft_root
from tools.relay_slides import capcut_running, run


def main() -> int:
    print()
    print("  장표 다시 깔기")
    print("  " + "-" * 46)
    print("  컷은 그대로 두고, 자막을 컷에 맞춘 뒤 장표를 깝니다.")
    print("  (자막은 글자는 그대로, 시간만 평균 0.1초 안쪽으로 움직입니다)")
    print()

    if capcut_running():
        print("  [멈춤] 캡컷이 실행 중입니다.")
        print("         캡컷을 완전히 종료한 뒤 다시 실행해 주세요.")
        print("         (켜둔 채로 고치면 캡컷이 끌 때 옛 내용으로 덮어씁니다)")
        print()
        return 1

    root = find_draft_root()
    drafts = sorted((d for d in root.iterdir()
                     if d.is_dir() and (d / "draft_content.json").exists()
                     and not d.name.startswith(".")),
                    key=lambda d: (d / "draft_content.json").stat().st_mtime,
                    reverse=True)
    if not drafts:
        print("  드래프트가 없습니다.")
        return 1

    print("  최근에 손댄 순서입니다.")
    print()
    for i, d in enumerate(drafts[:12], 1):
        print(f"   {i:>2}. {d.name}")
    print()
    try:
        pick = input("  번호를 넣고 엔터 (그냥 엔터 = 취소): ").strip()
    except EOFError:
        return 1
    if not pick.isdigit() or not (1 <= int(pick) <= min(12, len(drafts))):
        print("  취소했습니다.")
        return 0

    name = drafts[int(pick) - 1].name
    out = f"{name}_v2"
    n = 2
    while (root / out).exists():
        n += 1
        out = f"{name}_v{n}"
    print()
    print(f"  {name}  ->  {out}")
    print("  (원본은 그대로 둡니다)")
    print()
    # 자막을 컷에 맞추는 것까지가 기본이다. 셋(영상·자막·장표)이 같은 자리에서
    # 바뀌어야 이음매가 깔끔하다 — 대표님이 확인하고 허락하신 방식.
    return asyncio.run(run(name, out, "", inplace=False, align_subs=True))


if __name__ == "__main__":
    code = main()
    print()
    try:
        input("  끝났습니다. 엔터를 누르면 창이 닫힙니다. ")
    except EOFError:
        pass
    raise SystemExit(code)
