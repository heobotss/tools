"""강의 한 편을 직원 PC 로 보낼 묶음(zip)으로 싼다.

직원 PC 에는 G 드라이브가 없다. 프로그램이 G 에서 읽는 건 세 가지뿐이다 —
원고(.md) · 촬영 대본(.txt) · 장표 이미지 폴더. 그 셋을 「VOD자료\\」 아래
같은 상대 구조로 넣고, 프로그램 폴더(capcut_agent · tools · bat)도 함께 넣는다.

zip 이름은 항상 「클로드 코드 캡컷.zip」이다. 윈도우 「압축 풀기」가 zip 이름으로
폴더를 만들어 그 안에 풀기 때문에, 내용물은 최상위에 바로 두고(top 폴더 없음)
이름을 강의마다 같게 둔다 — 그래야 다음 zip 을 같은 자리에 풀면 덮어써진다.
어느 강의 묶음인지는 안의 「이 묶음은.txt」에 적힌다.

    python -m tools.pack_lecture            # 번호 고르는 메뉴
    python -m tools.pack_lecture part1_03   # 바로
"""
from __future__ import annotations
import os, sys, zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
from capcut_agent import slides as sl  # noqa: E402

OUT_DIR = Path.home() / "Desktop" / "클로드 코드 작업물" / "VOD 편집 인계 문서"
ZIP_NAME = "클로드 코드 캡컷.zip"

# 직원에게 보내는 것만 담는다.
#   - 「강의 묶음 만들기.bat」 는 대표님 전용(G 드라이브가 필요하다) → 넣지 않는다
#   - 「사용법.md」 는 옛 문서로, 강조·확대를 대표님 몫이라고 적어 두어 강의 규칙과
#     어긋난다 → 넣지 않는다. 대신 인계 문서를 넣는다
PROGRAM_FILES = ["캡컷 에이전트 실행.bat", "장표 다시 깔기.bat",
                 "바탕화면에 바로가기 만들기.bat"]
DOC_FILES = ["VOD 강의 편집 인계 문서.txt", "VOD 강의 편집 인계 문서.html"]
SKIP_FILES = ("_보관_",)    # G 드라이브 경로가 박힌 구버전 파일


def _add_tree(z: zipfile.ZipFile, src: Path, arc_root: str, skip=("__pycache__",)):
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d not in skip]
        for f in fn:
            if f.endswith(".pyc") or f.startswith(SKIP_FILES):
                continue
            p = Path(dp) / f
            rel = p.relative_to(src).as_posix()
            z.write(p, f"{arc_root}/{rel}" if arc_root else rel)


def pack(deck: sl.Deck, out_dir: Path = OUT_DIR) -> Path:
    missing = [p for p in (deck.script_md, deck.slide_dir, deck.sub_script) if p and not p.exists()]
    if missing:
        raise FileNotFoundError("없는 파일: " + ", ".join(str(m) for m in missing))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ZIP_NAME
    rel = lambda p: p.relative_to(sl.VOD_ROOT).as_posix()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # 강의 자료 셋 — G 드라이브 기준 상대 경로 그대로 VOD자료/ 아래에
        z.write(deck.script_md, "VOD자료/" + rel(deck.script_md))
        if deck.sub_script:
            z.write(deck.sub_script, "VOD자료/" + rel(deck.sub_script))
        _add_tree(z, deck.slide_dir, "VOD자료/" + rel(deck.slide_dir))
        # 프로그램 — 덱 등록이 바뀌었을 수 있으니 매번 같이 보낸다
        _add_tree(z, PROJ / "capcut_agent", "capcut_agent")
        _add_tree(z, PROJ / "tools", "tools")
        for f in PROGRAM_FILES:
            if not (PROJ / f).exists():
                continue
            if f == "캡컷 에이전트 실행.bat":
                # 직원 PC 는 강의만 편집한다. 화면이 열릴 때부터 용도가 「VOD 유료
                # 본편」으로 골라져 있게 한다 — 유튜브로 둔 채 돌려 장표가 안 붙고
                # 비트 카드가 들어가는 사고가 실제로 났다 (2026-09-30).
                # 대표님 폴더의 bat 은 그대로 두고 zip 안에서만 한 줄을 더한다.
                txt = (PROJ / f).read_bytes().decode("ascii")
                mark = "set PYTHONIOENCODING=utf-8\r\n"
                assert mark in txt, "실행 bat 형식이 바뀌었다"
                txt = txt.replace(mark, mark + "set CAPCUT_PRESET=vod\r\n", 1)
                z.writestr(f, txt.encode("ascii"))
            else:
                z.write(PROJ / f, f)
        # 편집 안내 문서도 같이 넣어 폴더 안에서 바로 열 수 있게 한다
        for f in DOC_FILES:
            if (out_dir / f).exists():
                z.write(out_dir / f, f)
        note = (
            f"이 묶음: {deck.label}\r\n\r\n"
            "이 폴더가 「클로드 코드 캡컷」 입니다.\r\n"
            "편집 방법은 같은 폴더의 「VOD 강의 편집 인계 문서.txt」 를 보세요.\r\n\r\n"
            "다음 강의 zip 을 받으면 이 폴더와 같은 자리에 풀고 「모두 바꾸기」 를 누르세요.\r\n"
        )
        z.writestr("이 묶음은.txt", note.encode("utf-8-sig"))
    return out


def main(argv: list[str]) -> int:
    decks = sl.DECKS
    if argv:
        deck = sl.get_deck(argv[0])
        if deck is None:
            print(f"모르는 덱: {argv[0]}"); return 1
    else:
        print("\n  어느 강의를 보낼까요?\n")
        for i, d in enumerate(decks, 1):
            print(f"  {i:2d}. {d.label}")
        try:
            n = int(input("\n  번호: ").strip() or "-1")
        except ValueError:
            n = -1
        if not 1 <= n <= len(decks):
            print("  취소"); return 1
        deck = decks[n - 1]
    out = pack(deck)
    print(f"\n  만들었습니다 → {out}")
    print(f"  [{deck.label}] · {out.stat().st_size // 1024 // 1024} MB")
    print("  직원에게 보내고, 바탕화면\\클로드\\ 안에서 압축을 풀라고 하세요.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
