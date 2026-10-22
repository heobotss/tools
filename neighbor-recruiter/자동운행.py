# -*- coding: utf-8 -*-
"""자동운행 — 컴퓨터만 켜 두면 알아서 돈다. 작업 스케줄러가 평일 10시에 이 파일을 부른다.

하는 일 (순서대로, 전부 자동):
  ① 시작 시각 흔들림 0~90분  — 매일 같은 시각에 도는 기계 패턴을 감춘다
  ② 후보가 30명 밑이면        → 후보뽑기 --자동  (점수 3+ 자동 승인)
  ③ 신청정리를 7일 넘게 안 했으면 → 신청정리       (수락 확인·21일 취소·추이 기록)
  ④ 신청실행                  → 오늘 목표만큼 신청 (램프 10→20→30→40 자동)
  ⑤ 결과를 기록/자동운행로그.txt 에 남기고, 사람이 봐야 할 일이 생기면 메일

메일이 가는 경우만 (평소엔 조용하다):
  · 로그인이 풀렸다 (로그인.bat 을 한 번 실행해야 한다)
  · 보안문자·이용제한으로 쿨다운에 들어갔다
  · 후보가 바닥났다 / 연속 실패로 멈췄다 / 스크립트 오류
  · 월요일: 주간 요약 (보낸 수·수락률·이웃수·방문수)

사용법:
  python 자동운행.py          (스케줄러가 부르는 방식 — 흔들림 대기 포함)
  python 자동운행.py --지금    흔들림 없이 바로
  python 자동운행.py --시험    신청은 「확인만」(제출 안 함) — 배선 점검용
"""
import json
import os
import random
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("NR_DATA") or BASE)     # 계정별 자리 (다계정) — 공용.py 와 같은 규칙
REC = DATA / "기록"
REC.mkdir(parents=True, exist_ok=True)
LOG = REC / "자동운행로그.txt"
STATE = REC / "자동운행상태.json"
CAND = DATA / "후보명단.csv"
PY = sys.executable

sys.path.insert(0, str(BASE.parent / "키워드순위추적"))     # 메일발송.py + _메일설정.json 재사용 (비밀번호는 거기 한 곳에만)
try:
    import 메일발송
except Exception:
    메일발송 = None


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def run(script, *args):
    """스크립트 하나를 돌리고 (출력, 경고줄 목록) 을 돌려준다.
    출력은 한 줄씩 바로 로그·화면에 흘린다 (2026-09-22) — 트레이너가 더블클릭으로 돌릴 때
    「로그인하세요」 같은 안내가 끝날 때까지 안 보이던 문제를 막는다. stdin 은 그대로 물려준다."""
    import threading
    cmd = [PY, "-X", "utf8", "-u", str(BASE / script), *args]
    log(f"▶ {script} {' '.join(args)}")
    lines = []
    p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    killed = []

    def watchdog():
        killed.append(True)
        p.kill()

    timer = threading.Timer(3 * 3600, watchdog)
    timer.start()
    try:
        for ln in p.stdout:
            ln = ln.rstrip("\r\n")
            lines.append(ln)
            log("   " + ln)
        p.wait()
    finally:
        timer.cancel()
    if killed:
        lines.append("[!] 3시간 제한 초과 — 강제 종료")
        log("   " + lines[-1])
    out = "\n".join(lines)
    warns = [ln.strip() for ln in lines if ln.strip().startswith("[!]") or "Traceback" in ln]
    return out, warns


def approved_left():
    import csv
    cand = CAND
    if not cand.exists():
        return 0
    sys.path.insert(0, str(BASE))
    from 공용 import blocked_ids, excluded_ids, assigned, load_cfg
    cfg = load_cfg()
    known = blocked_ids() | excluded_ids(cfg)
    with open(cand, encoding="utf-8-sig") as f:
        return sum(1 for r in csv.DictReader(f)
                   if (r.get("승인") or "").strip().upper() == "O" and r["블로그ID"] not in known
                   and assigned(r["블로그ID"], cfg))


def warehouse_left():
    """창고후보 중 아직 안 간 내 몫 (후보명단과 겹치는 사람은 한 번만 센다). 센터판은 0."""
    import csv
    sys.path.insert(0, str(BASE))
    from 공용 import blocked_ids, excluded_ids, assigned, load_cfg, warehouse_rows
    wh = warehouse_rows()
    if not wh:
        return 0
    cfg = load_cfg()
    known = blocked_ids() | excluded_ids(cfg)
    cand = CAND
    if cand.exists():
        with open(cand, encoding="utf-8-sig") as f:
            known |= {r["블로그ID"] for r in csv.DictReader(f) if (r.get("승인") or "").strip().upper() == "O"}
    return sum(1 for r in wh if r["블로그ID"] not in known and assigned(r["블로그ID"], cfg))


TEST = "--시험" in sys.argv


def mail_on():
    """설정 「메일알림」(기본 켜짐). 트레이너 배포판은 꺼서 메일 대신 화면에 보여 준다."""
    try:
        sys.path.insert(0, str(BASE))
        from 공용 import load_cfg
        return load_cfg().get("메일알림", True)
    except Exception:
        return True


def 텔레그램(subject, lines):
    """프로그램 개발\\카톡알림 공용 모듈로 대표님 텔레그램에 한 줄 (2026-09-25).
    트레이너 배포판 PC에는 그 폴더가 없다 — 조용히 넘어간다. 실패해도 운행은 계속한다"""
    try:
        import importlib
        import re
        폴더 = str(BASE.parent / "카톡알림")
        if not os.path.isdir(폴더):
            return False
        if 폴더 not in sys.path:
            sys.path.insert(0, 폴더)
        본문 = "\n".join(re.sub(r"<[^>]+>", "", ln) for ln in lines)
        return importlib.import_module("카톡").보내기(
            f"{subject.replace('[비브레] ', '')}\n\n{본문}".strip()[:1200], 제목="이웃모집기")
    except Exception as e:
        log(f"(텔레그램 알림 실패 {type(e).__name__}: {str(e)[:80]})")
        return False


def mail(subject, lines):
    if TEST:
        log(f"(시험 — 메일 대신 로그) {subject}: " + " / ".join(lines))
        return
    if not mail_on():
        import re
        log("")
        log(f"■ {subject.replace('[비브레] ', '')}")
        for ln in lines:
            log("   " + re.sub(r"<[^>]+>", "", ln))
        log("")
        return
    텔레그램(subject, lines)          # 메일과 함께 (센터 PC에서만 — 배포판은 위에서 이미 돌아갔다)
    if 메일발송 is None:
        log("(메일 부품 없음 — 로그만 남김)")
        return
    body = "<br>".join(lines)
    html = (f"<div style='font-family:Malgun Gothic,sans-serif;max-width:560px;padding:20px;"
            f"background:#0D1929;color:#EAF1FA;border-radius:12px'>"
            f"<div style='color:#47A3FF;font-weight:800;font-size:13px'>비브레 이웃모집기</div>"
            f"<p style='font-size:14px;line-height:1.8'>{body}</p>"
            f"<p style='color:#566d8a;font-size:11px'>기록: {LOG}</p></div>")
    try:
        메일발송.send(메일발송.CFG["smtp_user"], subject, html)
        log(f"✉ 메일 발송: {subject}")
    except Exception as e:
        log(f"(메일 실패 {type(e).__name__}: {e})")


def weekly_summary():
    """월요일 요약 — 장부와 추이에서 숫자를 뽑는다."""
    import csv
    rows = list(csv.DictReader(open(REC / "장부.csv", encoding="utf-8-sig"))) if (REC / "장부.csv").exists() else []
    sent = sum(1 for r in rows if r["상태"].startswith(("신청", "수락", "소멸", "기한취소")))
    acc = sum(1 for r in rows if r["상태"] == "수락")
    week_ago = (date.today().toordinal() - 7)
    sent7 = sum(1 for r in rows if r["신청일"] and date.fromisoformat(r["신청일"]).toordinal() >= week_ago
                and r["상태"].startswith(("신청", "수락", "소멸", "기한취소")))
    trend = list(csv.DictReader(open(REC / "추이.csv", encoding="utf-8-sig"))) if (REC / "추이.csv").exists() else []
    now = trend[-1] if trend else {}
    try:                                                     # 시작값은 기준선.json (추이.csv 는 하루 한 줄이라 첫날 값이 덮인다)
        first = json.loads((REC / "기준선.json").read_text(encoding="utf-8"))
    except Exception:
        first = trend[0] if trend else {}
    lines = [f"이번 주 보낸 신청 <b>{sent7}건</b> · 누적 {sent}건 · 수락 {acc}건"
             + (f" (수락률 {acc / sent * 100:.0f}%)" if sent else ""),
             f"이웃 <b>{now.get('이웃수', '-')}</b>명 (시작 {first.get('이웃수', '-')}) · 방문 {now.get('방문수', '-')} (시작 {first.get('방문수', '-')})",
             f"수락한 이웃 글에 공감 한 번씩 — 목록은 기록/장부.csv 에서 상태=수락"]
    return lines


def main():
    now_flag = "--지금" in sys.argv
    test = "--시험" in sys.argv
    st = load_state()
    log("═" * 40)
    log(f"자동운행 시작 (오늘 {date.today():%m-%d %a}){'  ※ 시험' if test else ''}")

    if not now_flag and not test:
        wait = random.randint(0, 90 * 60)
        log(f"시작 흔들림 {wait // 60}분 대기")
        time.sleep(wait)

    alerts = []

    # ② 후보 보충
    left = approved_left()
    wh = warehouse_left()                      # 트레이너 배포판의 창고 몫 — 센터판은 0
    log(f"남은 승인 후보 {left}명" + (f" + 창고 {wh}명" if wh else ""))
    last_pick = st.get("후보뽑기일", "2000-01-01")
    recent_pick = (date.today() - date.fromisoformat(last_pick)).days < 7
    if left < 30 and not (wh >= 30 and recent_pick):
        # 창고가 있어도 자기 발굴은 계속한다(각자 추가 모집). 다만 창고가 넉넉하면 주 1회만 —
        # 발굴 한 번이 20분 가까이 걸려서, 새로 나올 사람이 없는데 매일 돌면 창만 오래 떠 있다
        st["후보뽑기일"] = date.today().isoformat()
        save_state(st)
        _, w = run("후보뽑기.py", "--자동")
        alerts += w
        left = approved_left()
        wh = warehouse_left()
        log(f"보충 뒤 남은 후보 {left}명" + (f" + 창고 {wh}명" if wh else ""))
        if left + wh == 0:
            alerts.append("[!] 후보가 바닥났습니다 — 검색키워드·확장지역을 넓혀야 합니다")
    left += wh

    # ③ 주 1회 정리
    last = st.get("정리일", "2000-01-01")
    if (date.today() - date.fromisoformat(last)).days >= 7:
        _, w = run("신청정리.py")
        alerts += w
        st["정리일"] = date.today().isoformat()
        save_state(st)
        if date.today().weekday() == 0 or "주간요약" not in st or \
                (date.today() - date.fromisoformat(st.get("주간요약", "2000-01-01"))).days >= 7:
            mail("[비브레] 이웃모집기 주간 요약", weekly_summary())
            st["주간요약"] = date.today().isoformat()
            save_state(st)

    # ④ 신청 — 이웃 그룹이 꽉 차면 스스로 새 그룹을 만들고 이어서 한다 (2026-10-13)
    #    한 그룹은 322명에서 찼다. 사람이 끼면 그날 작업이 통째로 멈추므로 자동으로 뚫는다.
    if left > 0:
        sent = 0
        for 회차 in range(1, 4):                       # 그룹 때문에 끊기면 최대 두 번까지 새 그룹 만들고 재개
            extra = []
            if 회차 > 1:                               # 재개할 땐 오늘 목표를 다시 뽑지 않는다 —
                try:                                   # 흔들림이 낮게 나오면 「이미 달성」으로 끝나 버린다
                    sys.path.insert(0, str(BASE))
                    from 공용 import cap_today, load_cfg as _cfg
                    extra = ["--한도", str(cap_today(_cfg()))]
                except Exception:
                    extra = []
            out, w = run("신청실행.py", *(["--확인만"] if test else []), *extra)
            sent += sum(1 for ln in out.splitlines() if ln.strip().endswith("→ 신청"))
            막힘 = "그룹이 가득 찼습니다" in out or "실패-그룹한도" in out
            if not 막힘:
                alerts += w
                break
            if test:
                log("(시험 — 그룹이 찼지만 만들지 않음)")
                alerts += w
                break
            log("이웃 그룹이 꽉 찼습니다 — 새 그룹을 만들고 이어서 신청합니다.")
            g_out, g_w = run("그룹만들기.py", "--자동")
            if "[✓] 그룹" not in g_out:
                log("[!] 새 그룹 만들기에 실패했습니다 — 오늘은 여기까지. 캡처는 기록/그룹/ 에 있습니다.")
                alerts += w + g_w + ["[!] 이웃 그룹을 자동으로 만들지 못했습니다 — 확인이 필요합니다"]
                break
        log(f"오늘 신청 {sent}건")

    # ⑤ 사람이 봐야 할 일
    alerts = [a for a in alerts if a]
    if alerts:
        need_login = any("로그인" in a for a in alerts)
        cool = any(("쿨다운" in a or "보안문자" in a or "이용제한" in a) for a in alerts)
        subject = "[비브레] 이웃모집기 — " + ("로그인 필요" if need_login else "쿨다운 진입" if cool else "확인 필요")
        lines = [a.replace("[!]", "").strip() for a in alerts]
        if need_login:
            lines.append("→ 이웃모집기 폴더의 <b>" + ("2_로그인" if not mail_on() else "로그인.bat")
                         + "</b> 을 한 번 눌러 「로그인 상태 유지」를 켜고 로그인하세요.")
        mail(subject, lines)
    log("자동운행 끝")
    if not mail_on():
        total = int(os.environ.get("NR_ACCOUNT_COUNT", 1))
        idx = int(os.environ.get("NR_ACCOUNT_INDEX", 0))
        who = os.environ.get("NR_ACCOUNT_ID", "")
        if alerts:
            log(f"{who} 끝 — 위의 ■ 확인 필요 내용을 캡처해서 대표님께 보내 주세요.")
        elif idx + 1 < total:
            log(f"{who} 끝. 다음 블로그가 남았습니다 — 창을 닫지 마세요.")
        else:
            log("끝났습니다. 이 창은 닫으셔도 됩니다.")


if __name__ == "__main__":
    main()
