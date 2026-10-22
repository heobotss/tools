# -*- coding: utf-8 -*-
"""기록 파일을 쓸 수 없을 때 개발자용 오류 대신 한국어 안내를 띄우고 멈춘다.

2026-10-22 실제 사례: 트레이너가 진행상황.csv 를 엑셀로 열어 둔 채 언팔을 돌려
PermissionError(접근 거부)로 프로그램이 죽었다. 엑셀은 연 파일을 잠가서
다른 프로그램이 쓰지 못하게 한다.

외부 부품(playwright 등)을 불러오지 않는다 — check.py·queue.py 도 쓸 수 있게.
"""
import os
import sys


def _guide(path):
    name = os.path.basename(path)
    return (
        "\n" + "=" * 56 + "\n"
        "  「%s」 파일에 기록할 수 없습니다\n" % name
        + "=" * 56 + "\n"
        "  이 파일이 엑셀 같은 다른 프로그램에 열려 있으면\n"
        "  프로그램이 기록을 쓰지 못합니다.\n"
        "\n"
        "  1) 엑셀을 완전히 닫으세요.\n"
        "     (화면 아래 작업 표시줄에 엑셀이 남아 있지 않게)\n"
        "  2) 시작.bat 에서 방금 누른 번호를 다시 실행하세요.\n"
        "     지금까지 한 것은 저장돼 있어서 이어서 진행됩니다.\n"
        "\n"
        "  엑셀을 닫았는데도 이 화면이 나오면:\n"
        "  「%s」 오른쪽 클릭 - 속성 - 「읽기 전용」 체크를 빼세요.\n" % name
        + "\n"
        "  진행 상황은 프로그램이 도는 동안 열지 말고\n"
        "  메뉴 8번(진행 현황 보기)으로 확인하세요.\n"
        + "=" * 56
    )


def open_write(path, mode="a", **kwargs):
    """open() 과 같지만, 잠긴 파일이면 안내를 띄우고 멈춘다."""
    try:
        return open(path, mode, **kwargs)
    except PermissionError:
        sys.exit(_guide(path))


def run_main(main):
    """프로그램 입구 — 어느 단계에서든(읽기·쓰기) 파일이 잠겨 있으면 같은 한국어 안내로 멈춘다.

    엑셀은 보통 쓰기만 막지만, 다른 프로그램은 읽기까지 막을 수 있다 (시험에서 확인).
    """
    try:
        return main()
    except PermissionError as e:
        sys.exit(_guide(e.filename or "기록 파일"))


def check_writable(*paths):
    """본작업(브라우저 띄우기) 전에 기록 파일이 쓸 수 있는 상태인지 미리 확인한다.

    아직 없는 파일은 건너뛴다 (처음 쓸 때 만들어진다). 내용은 건드리지 않는다.
    """
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "ab"):  # 이진 추가 모드 — 아무것도 쓰지 않으니 내용·인코딩 그대로
                pass
        except PermissionError:
            sys.exit(_guide(path))
