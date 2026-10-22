"""경로 탐색 / 환경 설정."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 캡컷 드래프트 폴더 후보. 앞에서부터 존재하는 걸 쓴다.
_WIN_CANDIDATES = (
    r"{LOCALAPPDATA}\CapCut\User Data\Projects\com.lveditor.draft",
    r"{LOCALAPPDATA}\JianyingPro\User Data\Projects\com.lveditor.draft",
)
_MAC_CANDIDATES = (
    "{HOME}/Movies/CapCut/User Data/Projects/com.lveditor.draft",
    "{HOME}/Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
)


def find_draft_root() -> Path:
    """캡컷 드래프트 루트를 찾는다. 환경변수 CAPCUT_DRAFT_ROOT 가 최우선."""
    override = os.environ.get("CAPCUT_DRAFT_ROOT")
    if override:
        p = Path(override)
        if p.is_dir():
            return p
        raise FileNotFoundError(f"CAPCUT_DRAFT_ROOT 가 가리키는 폴더가 없습니다: {p}")

    templates = _MAC_CANDIDATES if sys.platform == "darwin" else _WIN_CANDIDATES
    env = {"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
           "HOME": os.path.expanduser("~")}
    tried = []
    for t in templates:
        p = Path(t.format(**env))
        tried.append(str(p))
        if p.is_dir():
            return p

    raise FileNotFoundError(
        "캡컷 드래프트 폴더를 못 찾았습니다. 캡컷을 설치하고 한 번 실행한 뒤 다시 시도하세요.\n"
        "찾아본 경로:\n  " + "\n  ".join(tried)
    )


TEMPLATE_DIR = Path(__file__).parent / "templates"

# 작업 산출물(업로드본, ASR 캐시)이 쌓이는 곳
WORK_DIR = Path(__file__).parent.parent / ".work"
