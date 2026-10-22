"""받는 PC 에서 도는 설치 스크립트. pack_draft.py 가 만든 묶음 안에 들어간다.

자리표시자로 바꿔 둔 경로를 **이 PC 의 경로**로 되돌리고, 캡컷 드래프트 폴더에
넣는다. 이 파일은 묶음 안에서 혼자 돌기 때문에 capcut_agent 를 쓰지 않는다.
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

PH_VIDEO = "@@원본영상@@"
PH_SLIDES = "@@장표폴더@@"
PH_FONT = "@@글꼴@@"
PH_FOLD = "@@드래프트폴더@@"
PH_ROOT = "@@드래프트루트@@"

JSON_FILES = ("draft_content.json", "draft_meta_info.json", "draft_virtual_store.json")
HERE = Path(__file__).resolve().parent


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def capcut_running() -> bool:
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30).stdout
    except Exception:
        return False
    return "capcut" in out.lower()


def draft_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", ""))
    p = base / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    if not p.is_dir():
        raise FileNotFoundError(
            "캡컷 드래프트 폴더를 못 찾았습니다.\n"
            "  캡컷이 설치되어 있고 한 번은 실행한 적이 있어야 합니다.\n"
            f"  찾은 자리: {p}")
    return p


def capcut_font() -> str:
    """이 PC 의 캡컷 기본 글꼴. 버전마다 경로가 달라 찾아서 쓴다."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
    cands = sorted(base.glob("*/Resources/Font/SystemFont/en.ttf"), reverse=True)
    if cands:
        return fwd(cands[0])
    for c in (r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf"):
        if Path(c).exists():
            return fwd(c)
    return ""


def find_project() -> Path | None:
    """캡컷 에이전트 폴더를 찾는다 (대본 캐시·강의 자료를 여기 합친다)."""
    home = Path.home()
    cands = [
        home / "Desktop" / "클로드" / "클로드 코드 캡컷",
        home / "OneDrive" / "바탕 화면" / "클로드" / "클로드 코드 캡컷",
        home / "OneDrive" / "Desktop" / "클로드" / "클로드 코드 캡컷",
        HERE.parent / "클로드 코드 캡컷",
    ]
    for c in cands:
        if (c / "capcut_agent").is_dir():
            return c
    # 바탕화면 아래를 한 겹만 더 훑는다
    for desk in (home / "Desktop", home / "OneDrive" / "바탕 화면", home / "OneDrive" / "Desktop"):
        if not desk.is_dir():
            continue
        for p in desk.glob("*/클로드 코드 캡컷"):
            if (p / "capcut_agent").is_dir():
                return p
    return None


def unique_name(root: Path, name: str) -> str:
    if not (root / name).exists():
        return name
    for i in range(2, 100):
        cand = f"{name} ({i})"
        if not (root / cand).exists():
            return cand
    raise RuntimeError("이름이 너무 많이 겹칩니다")


def main() -> int:
    print()
    print("  ── 캡컷에 드래프트 넣기 ──")
    print()

    src_draft = HERE / "드래프트"
    src_media = HERE / "소재"
    if not src_draft.is_dir():
        print("  [오류] 「드래프트」 폴더가 없습니다. 압축을 덜 푼 것 같습니다.")
        return 1

    if capcut_running():
        print("  [멈춤] 캡컷이 켜져 있습니다.")
        print("         캡컷을 완전히 종료한 뒤 다시 실행해 주세요.")
        print("         (켜둔 채로 넣으면 캡컷이 끌 때 덮어써 버립니다)")
        return 1

    root = draft_root()
    proj = find_project()
    print(f"  캡컷 드래프트 폴더  {root}")
    print(f"  캡컷 에이전트 폴더  {proj if proj else '못 찾음 (장표 다시 깔기는 못 씁니다)'}")

    # ── 1) 소재를 둘 자리를 정한다
    if proj is not None:
        dest_media = proj / ".work" / "transfer" / HERE.name
    else:
        dest_media = HERE / "소재"          # 제자리에서 쓴다
    video_src = next((src_media / "원본").glob("*"), None)
    if video_src is None:
        print("  [오류] 원본 영상이 없습니다.")
        return 1
    slide_srcs = sorted((src_media / "장표").glob("*")) if (src_media / "장표").is_dir() else []

    if dest_media != src_media:
        (dest_media / "원본").mkdir(parents=True, exist_ok=True)
        (dest_media / "장표").mkdir(parents=True, exist_ok=True)
        print(f"  소재 옮기는 중… ({video_src.stat().st_size/1048576:.0f} MB + 장표 {len(slide_srcs)}개)")
        shutil.copy2(video_src, dest_media / "원본" / video_src.name)
        for p in slide_srcs:
            shutil.copy2(p, dest_media / "장표" / p.name)
    video = dest_media / "원본" / video_src.name
    slide_dir = dest_media / "장표"

    # ── 2) 대본 캐시 — 이게 있으면 「장표 다시 깔기」가 음성 인식을 건너뛴다
    cache_src = sorted((src_media / "대본캐시").glob("*.json")) if (src_media / "대본캐시").is_dir() else []
    if cache_src and proj is not None:
        d = proj / ".work" / "asr"
        d.mkdir(parents=True, exist_ok=True)
        for c in cache_src:
            shutil.copy2(c, d / c.name)
        print(f"  대본 캐시 넣음 ({len(cache_src)}개) — 음성 인식을 다시 하지 않습니다")

    # ── 3) 강의 자료 — 장표 다시 깔기에 필요
    vod_src = HERE / "VOD자료"
    if vod_src.is_dir() and proj is not None:
        n = 0
        for p in vod_src.rglob("*"):
            if p.is_file():
                t = proj / "VOD자료" / p.relative_to(vod_src)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, t)
                n += 1
        print(f"  강의 자료 넣음 ({n}개)")

    # ── 4) 드래프트 JSON 의 자리표시자를 이 PC 경로로 되돌린다
    name = unique_name(root, HERE.name.replace(" 편집묶음", ""))
    dest = root / name
    dest.mkdir(parents=True)
    font = capcut_font()
    table = {
        PH_VIDEO: fwd(video),
        PH_SLIDES: fwd(slide_dir),
        PH_FONT: font,
        PH_FOLD: fwd(dest),
        PH_ROOT: fwd(root),
    }
    changed = 0
    for f in sorted(src_draft.iterdir()):
        if not f.is_file():
            continue
        if f.name in JSON_FILES:
            raw = f.read_text(encoding="utf-8")
            for ph, real in table.items():
                changed += raw.count(ph)
                raw = raw.replace(ph, real)
            # 캡컷은 draft_root_path 만 역슬래시로 적는다.
            # 이름과 id 도 새 폴더에 맞춘다 — 이름이 어긋나면 캡컷 목록에
            # 옛 이름으로 뜨고, id 가 같으면 원본과 같은 드래프트로 취급된다.
            if f.name == "draft_meta_info.json":
                try:
                    j = json.loads(raw)
                    j["draft_root_path"] = str(root).replace("/", "\\")
                    j["draft_fold_path"] = fwd(dest)
                    j["draft_name"] = dest.name
                    j["draft_id"] = str(uuid.uuid4()).upper()
                    raw = json.dumps(j, ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
            (dest / f.name).write_text(raw, encoding="utf-8")
        else:
            shutil.copy2(f, dest / f.name)
    print(f"  경로 되돌림 {changed}곳")
    if not font:
        print("  [주의] 캡컷 글꼴을 못 찾아 자막 글꼴이 기본값으로 보일 수 있습니다.")

    # ── 5) 확인
    left = 0
    for f in dest.iterdir():
        if f.suffix == ".json":
            t = f.read_text(encoding="utf-8")
            left += sum(t.count(ph) for ph in table)
    ok = left == 0 and video.exists() and (not slide_srcs or slide_dir.is_dir())
    print()
    if ok:
        print(f"  끝났습니다. 드래프트 이름: {name}")
        print()
        print("  캡컷을 완전히 종료했다가 다시 켜면 목록에 뜹니다.")
    else:
        print(f"  [확인 필요] 안 바뀐 자리 {left}곳")
        return 1
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                      # noqa: BLE001
        print()
        print(f"  [오류] {type(e).__name__}: {e}")
        print("  이 화면을 캡처해서 보내 주세요.")
        sys.exit(1)
