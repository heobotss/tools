"""캡컷 드래프트를 다른 PC 로 옮길 수 있게 통째로 묶는다.

왜 필요한가 — 캡컷 드래프트는 소재를 **절대경로**로 가리킨다. 3강 드래프트
하나에 내 PC 경로가 711곳 박혀 있다(원본 영상 207 · 장표 클립 80 · 글꼴 422 ·
드래프트 폴더 2). 폴더만 복사하면 받는 PC 에서 전부 깨진다.

그래서 ① 참조하는 소재를 전부 모으고 ② 경로를 자리표시자로 바꿔 두고
③ 받는 PC 에서 그 PC 의 경로로 되돌리는 설치 스크립트를 같이 넣는다.

대본 캐시도 같이 넣는다. 그래야 받는 쪽에서 「장표 다시 깔기」를 돌릴 때
음성 인식을 다시 하지 않는다 (모델 1.5GB 가 없어도 된다).

    python -m tools.pack_draft "파트1 3강 완성"
    python -m tools.pack_draft "파트1 3강 완성" --deck part1_03
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from capcut_agent import asr as asr_mod          # noqa: E402
from capcut_agent import slides as sl            # noqa: E402
from capcut_agent.config import WORK_DIR, find_draft_root   # noqa: E402

OUT_DIR = Path.home() / "Desktop" / "클로드 코드 작업물" / "VOD 편집 인계 문서"

# 받는 PC 에서 되돌릴 자리표시자. 캡컷 JSON 안에서 절대 안 겹칠 모양으로 둔다.
PH_VIDEO = "@@원본영상@@"
PH_SLIDES = "@@장표폴더@@"
PH_FONT = "@@글꼴@@"
PH_FOLD = "@@드래프트폴더@@"
PH_ROOT = "@@드래프트루트@@"

JSON_FILES = ("draft_content.json", "draft_meta_info.json", "draft_virtual_store.json")

INSTALLER_PY = "install_draft.py"      # ASCII 여야 한다 (bat 이 이 이름을 부른다)


def _fwd(p) -> str:
    return str(p).replace("\\", "/")


def read_refs(draft: Path) -> dict:
    """드래프트가 가리키는 바깥 경로를 종류별로 모은다."""
    content = json.loads((draft / "draft_content.json").read_text(encoding="utf-8"))
    videos, slides, fonts = set(), set(), set()
    for m in content["materials"].get("videos", []):
        p = m.get("path") or ""
        if not p:
            continue
        (slides if "/slides/" in _fwd(p) else videos).add(_fwd(p))
    for t in content["materials"].get("texts", []):
        f = t.get("font_path") or ""
        if f:
            fonts.add(_fwd(f))
    return {"videos": sorted(videos), "slides": sorted(slides), "fonts": sorted(fonts)}


def pack(draft_name: str, deck_key: str | None = None, out_dir: Path = OUT_DIR) -> Path:
    root = find_draft_root()
    draft = root / draft_name
    if not draft.is_dir():
        raise FileNotFoundError(f"그런 드래프트가 없습니다: {draft}")

    refs = read_refs(draft)
    if len(refs["videos"]) != 1:
        raise RuntimeError(f"원본 영상이 1개여야 합니다 (지금 {len(refs['videos'])}개)")
    video = Path(refs["videos"][0])
    if not video.exists():
        raise FileNotFoundError(f"원본 영상이 없습니다: {video}")

    slide_files = [Path(p) for p in refs["slides"]]
    missing = [p for p in slide_files if not p.exists()]
    if missing:
        raise FileNotFoundError(f"장표 클립 {len(missing)}개가 없습니다: {missing[0]}")
    slide_dir = slide_files[0].parent if slide_files else None

    # 덱 — 장표 다시 깔기에 필요한 원고·대본·장표 이미지
    if deck_key is None and slide_dir is not None:
        deck_key = slide_dir.name          # .work/slides/<덱키>/
    deck = sl.get_deck(deck_key) if deck_key else None

    # 대본 캐시 — 받는 쪽이 음성 인식을 다시 안 하도록
    cache = asr_mod._cache_path(asr_mod.content_hash(video), asr_mod.DEFAULT_MODEL)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{draft_name} 편집묶음.zip"

    print(f"  드래프트  {draft_name}")
    print(f"  원본 영상 {video.name}  {video.stat().st_size/1048576:.0f} MB")
    print(f"  장표 클립 {len(slide_files)}개"
          f"  {sum(p.stat().st_size for p in slide_files)/1048576:.0f} MB")
    print(f"  글꼴 경로 {len(refs['fonts'])}종")
    print(f"  대본 캐시 {'있음' if cache.exists() else '없음 (받는 쪽에서 다시 뽑아야 함)'}")
    print(f"  강의 자료 {deck.label if deck else '덱을 못 찾음 — 장표 다시 깔기는 못 씀'}")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        # ── 1) 드래프트 JSON — 경로를 자리표시자로 바꿔 담는다
        subs = [
            (_fwd(video), PH_VIDEO),
            (_fwd(draft), PH_FOLD),
            (_fwd(root), PH_ROOT),
        ]
        if slide_dir is not None:
            subs.insert(1, (_fwd(slide_dir), PH_SLIDES))
        for f in refs["fonts"]:
            subs.append((f, PH_FONT))
        # 긴 경로부터 바꿔야 짧은 경로가 먼저 먹어치우지 않는다
        subs.sort(key=lambda kv: len(kv[0]), reverse=True)

        changed = 0
        for name in JSON_FILES:
            f = draft / name
            if not f.exists():
                continue
            raw = f.read_text(encoding="utf-8")
            for src, ph in subs:
                # 캡컷은 슬래시와 역슬래시를 섞어 쓴다. 둘 다 바꾼다.
                for form in (src, src.replace("/", "\\"), src.replace("/", "\\\\")):
                    if form in raw:
                        changed += raw.count(form)
                        raw = raw.replace(form, ph)
            z.writestr(f"드래프트/{name}", raw.encode("utf-8"))
        # 나머지 부속 파일 (표지·자막 txt 등)
        for f in sorted(draft.iterdir()):
            if f.is_file() and f.name not in JSON_FILES:
                z.write(f, f"드래프트/{f.name}")
        print(f"  경로 바꿔 담음 {changed}곳")

        # ── 2) 소재
        z.write(video, f"소재/원본/{video.name}")
        for p in slide_files:
            z.write(p, f"소재/장표/{p.name}")
        if cache.exists():
            z.write(cache, f"소재/대본캐시/{cache.name}")

        # ── 3) 강의 자료 (장표 다시 깔기용)
        if deck is not None:
            rel = lambda p: p.relative_to(sl.VOD_ROOT).as_posix()
            if deck.script_md.exists():
                z.write(deck.script_md, "VOD자료/" + rel(deck.script_md))
            if deck.sub_script and deck.sub_script.exists():
                z.write(deck.sub_script, "VOD자료/" + rel(deck.sub_script))
            if deck.slide_dir.exists():
                for p in sorted(deck.slide_dir.iterdir()):
                    if p.is_file():
                        z.write(p, "VOD자료/" + rel(deck.slide_dir) + "/" + p.name)

        # ── 4) 받는 쪽에서 돌릴 설치 스크립트
        # 파일 이름은 반드시 ASCII 로 둔다. bat 은 순수 ASCII 여야 하는데(코드 페이지
        # 문제) 그 안에서 한글 파일 이름을 부를 수 없기 때문이다.
        # (2026-10-11: 이름을 「넣기.py」로 담고 bat 은 install_draft.py 를 불러 실패했다)
        z.writestr(INSTALLER_PY, (PROJ / "tools" / "_install_draft.py")
                   .read_text(encoding="utf-8").encode("utf-8"))
        z.writestr("캡컷에 넣기.bat", _installer_bat().encode("ascii"))
        z.writestr("먼저 읽어주세요.txt", _readme(draft_name, deck).encode("utf-8-sig"))
        # 편집 안내문도 같이 넣는다 — 직원이 묶음 하나만 받으면 되게
        doc = out_dir / "VOD 강의 편집 인계 문서.txt"
        if doc.exists():
            z.write(doc, doc.name)

    return out


def _installer_bat() -> str:
    lines = [
        "@echo off",
        'cd /d "%~dp0"',
        "title Install Draft",
        "",
        "REM ASCII only. Korean text inside a .bat is read with the PC's code page",
        "REM (949 on a Korean Windows) and turns into garbage. Keep this pure ASCII.",
        "",
        "chcp 65001 > nul",
        "set PYTHONUTF8=1",
        "set PYTHONIOENCODING=utf-8",
        "",
        "where python > nul 2>&1",
        "if errorlevel 1 (",
        "  echo.",
        '  echo   [ERROR] Python not found. Reinstall Python with "Add Python to PATH".',
        "  echo.",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        f"python {INSTALLER_PY}",
        "pause",
    ]
    out = "\r\n".join(lines) + "\r\n"
    assert all(ord(c) < 128 for c in out), "설치 bat 에 한글이 들어가면 안 된다"
    return out


def _readme(draft_name: str, deck) -> str:
    return (
        f"■ {draft_name} — 캡컷에 넣기\r\n\r\n"
        f"강의: {deck.label if deck else '(알 수 없음)'}\r\n\r\n"
        "1. 이 폴더의 「캡컷에 넣기.bat」 을 더블클릭하세요.\r\n"
        "2. 검은 창이 뜨고, 영상과 장표를 제자리에 놓은 뒤 캡컷에 드래프트를 넣습니다.\r\n"
        "   몇 분 걸립니다. 창을 끄지 마세요.\r\n"
        "3. 「끝났습니다」 가 나오면 캡컷을 완전히 종료했다가 다시 켜세요.\r\n"
        "4. 캡컷 목록에 드래프트가 뜹니다. 열어서 편집하시면 됩니다.\r\n\r\n"
        "※ 캡컷이 켜져 있으면 스스로 멈춥니다. 먼저 끄고 돌리세요.\r\n"
        "※ 편집 방법은 「VOD 강의 편집 인계 문서.txt」 를 보세요.\r\n"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", nargs="?", help="캡컷 드래프트 이름")
    ap.add_argument("--deck", default=None, help="덱 키 (보통 자동으로 찾는다)")
    a = ap.parse_args(argv)

    if not a.draft:
        root = find_draft_root()
        names = sorted((p.name for p in root.iterdir()
                        if p.is_dir() and not p.name.startswith(".")),
                       key=lambda n: (root / n).stat().st_mtime, reverse=True)
        print("\n  어느 드래프트를 보낼까요?\n")
        for i, n in enumerate(names[:20], 1):
            print(f"  {i:2d}. {n}")
        try:
            k = int(input("\n  번호: ").strip() or "-1")
        except ValueError:
            k = -1
        if not 1 <= k <= min(20, len(names)):
            print("  취소")
            return 1
        a.draft = names[k - 1]

    out = pack(a.draft, a.deck)
    print(f"\n  만들었습니다 → {out}")
    print(f"  {out.stat().st_size/1048576:.0f} MB · 구글 드라이브로 보내세요.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
