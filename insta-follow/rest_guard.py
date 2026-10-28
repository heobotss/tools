# -*- coding: utf-8 -*-
"""
인스타가 막았을 때 멈추고 쉬게 하는 장치 (언팔·팔로우·원주 고객 팔로우 공용)

2026-10-27 대표님 지시 — 「인스타는 한 번에 크게 문제 삼지 않는다. 거부하면 바로 멈추기만 하면 된다.
스스로 영구 정지하지 말고 알려만 주면 된다.」

  - 차단 메시지·보안 확인 화면·로그아웃·「눌렀는데 반영 안 됨」이 나오면 그 자리에서 멈춘다
  - 쉬는 기간을 적어 둔다: 처음 3일, 7일 안에 두 번째면 14일
  - 쉬는 기간에 다시 켜면 알려 주고, 그래도 하겠다고 y 를 누르면 진행한다 (강제로 막지 않는다)
"""

import os
import csv
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REST = os.path.join(HERE, "_쉬는중.json")


def _read():
    if not os.path.exists(REST):
        return {"거부일들": [], "쉬는끝날": "", "마지막사유": ""}
    try:
        with open(REST, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"거부일들": [], "쉬는끝날": "", "마지막사유": ""}


def rest_until():
    """쉬는 기간이면 끝나는 날짜 문자열, 아니면 빈 문자열."""
    end = _read().get("쉬는끝날") or ""
    if end and datetime.date.today().isoformat() <= end:
        return end
    return ""


def record_refusal(why):
    """오늘을 「인스타가 거부한 날」로 적고 쉬는 기간을 정한다. 끝나는 날을 돌려준다."""
    st = _read()
    today = datetime.date.today()
    days = [d for d in st.get("거부일들", []) if d != today.isoformat()]
    days.append(today.isoformat())
    recent = [d for d in days
              if (today - datetime.date.fromisoformat(d)).days <= 7]
    rest = 14 if len(recent) >= 2 else 3
    end = (today + datetime.timedelta(days=rest)).isoformat()
    st = {"거부일들": days[-10:], "쉬는끝날": end, "마지막사유": why}
    try:
        tmp = REST + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REST)
    except Exception:
        pass

    print("")
    print("!" * 56)
    print("  인스타가 막았습니다 — 오늘은 여기서 멈춥니다")
    print("!" * 56)
    print("  사유: %s" % why)
    print("")
    print("  1) 휴대폰 인스타 앱을 열어 경고나 본인 확인이 떠 있으면 해 주세요.")
    print("  2) 로그인이 풀렸으면 메뉴 1번으로 다시 로그인하세요.")
    print("  3) %s 까지 %d일 쉬는 것을 권합니다." % (end, rest))
    print("     (7일 안에 두 번 막히면 14일을 권합니다)")
    print("")
    print("  계정이 정지된 것이 아닙니다. 쉬었다 하면 대부분 풀립니다.")
    print("!" * 56)
    return end


def ask_if_resting():
    """쉬는 기간이면 알려 주고 계속할지 묻는다. 진행해도 되면 True."""
    end = rest_until()
    if not end:
        return True
    st = _read()
    print("")
    print("=" * 56)
    print("  지금은 쉬는 기간입니다 (%s 까지)" % end)
    print("=" * 56)
    print("  최근 인스타가 막은 일: %s" % (st.get("마지막사유") or "-"))
    print("  쉬는 기간에 다시 하면 또 막히기 쉽습니다.")
    print("")
    try:
        ans = input("  그래도 지금 하려면 y 를 누르고 Enter: ").strip().lower()
    except EOFError:
        ans = ""
    print("")
    return ans == "y"


def _count(path, datecol, resultcol, values):
    if not os.path.exists(path):
        return 0
    today = datetime.date.today().strftime("%Y-%m-%d")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return sum(1 for r in csv.DictReader(f)
                       if (r.get(datecol) or "").startswith(today)
                       and r.get(resultcol) in values)
    except Exception:
        return 0


def actions_today():
    """오늘 이 계정이 실제로 한 행동 수 (언팔 + 팔로우 + 원주 고객 팔로우/언팔)."""
    return (_count(os.path.join(HERE, "진행상황.csv"), "시각", "결과", ("unfollowed",))
            + _count(os.path.join(HERE, "팔로우기록.csv"), "시각", "결과", ("followed",))
            + _count(os.path.join(HERE, "지역기록.csv"), "일시", "행동", ("팔로우", "언팔")))


def show_today_total(warn_over=150):
    """오늘 총 행동을 보여 주고, 많으면 주의만 준다 (막지는 않는다 — 한도는 설정.txt 몫)."""
    n = actions_today()
    print("  오늘 이 계정 총 행동 : %d 번 (언팔 + 팔로우 + 원주 고객)" % n)
    if n >= warn_over:
        print("  [주의] 하루 %d번이 넘으면 인스타가 막기 시작하는 계정이 많습니다." % warn_over)
        print("         막히면 프로그램이 알아서 멈춥니다.")
    return n
