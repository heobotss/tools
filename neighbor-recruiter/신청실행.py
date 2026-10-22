# -*- coding: utf-8 -*-
"""신청실행 — 승인(O)된 후보에게 서로이웃 신청을 보낸다. 속도보다 사람다움이 우선.

사용법:
  python 신청실행.py               오늘 목표만큼 신청 (워밍업 램프 + 그날그날 흔들림)
  python 신청실행.py --한도 5      오늘은 정확히 5명까지만 (램프·흔들림 무시)
  python 신청실행.py --확인만      신청 직전 화면까지만 가고 제출 안 함 (첫 검증용)
  python 신청실행.py --강행        활동 시간대 밖에서도 실행 (테스트용)

사람답게 움직이는 방식 (전부 자동):
  · 폰(갤럭시)으로 위장한 브라우저 — m.blog 동선이 지문과 일치한다
  · 블로그에 들러 글을 읽고(체류·스크롤) 나서 신청한다. 가끔은 읽기만 하고
    신청 안 하는 「구경」도 한다 — 사람은 모두에게 신청하지 않는다
  · 간격은 로그정규분포(사람의 행동 간격), 3~6건마다 몇 분씩 쉰다
  · 하루 목표도 매일 다르다 (한도의 70~100%)
  · 워밍업 램프: 1주차 5 → 2주차 10 → 3주차 20 → 30. 급발진 금지
  · 메시지는 인사×본문×맺음 조합(수십 가지) — 복붙 문구를 안 남긴다
  · 심야·새벽에는 안 돈다 (기본 09~23시)
  · 보안문자·이용제한을 만나면 즉시 멈추고 3일 쿨다운을 스스로 건다
"""
import csv
import math
import random
import sys
import time
from datetime import date, datetime

from playwright.sync_api import sync_playwright

from 공용 import (CAND_FILE, load_cfg, launch_ctx, ensure_login, snap,
                  blocked_ids, ledger_append, sent_today,
                  cooldown_active, set_cooldown, cap_today, excluded_ids, assigned,
                  warehouse_rows)


# ──────────────────────────── 후보·메시지 ────────────────────────────

def load_approved(cfg=None):
    """승인(O) 후보. 창고후보.csv 가 있으면(트레이너 배포판) 창고 원주 거주자를 합쳐
    계정마다 고정된 무작위 순서로 섞는다 — 매일 같은 순서라 재현되고, 계정끼리는 순서가 다르다.
    센터판(창고 파일 없음)은 예전 그대로 점수 순."""
    own = []
    if CAND_FILE.exists():
        with open(CAND_FILE, encoding="utf-8-sig") as f:
            own = [r for r in csv.DictReader(f) if (r.get("승인") or "").strip().upper() == "O"]
    wh = warehouse_rows()
    if not wh:
        return own
    seen = {r["블로그ID"] for r in own}
    merged = own + [r for r in wh if r["블로그ID"] not in seen]
    random.Random((cfg or {}).get("계정ID", "")).shuffle(merged)
    return merged


def pick_message(cfg, row):
    """인사×본문×맺음 조합 — 같은 문장이 반복 축적되지 않게 한다."""
    지역 = (cfg.get("지역단어") or ["동네"])[0]
    first = (row.get("발굴키워드") or "").split(" ")[0]            # 「횡성 둔내 카페」→ 횡성. 옆 동네엔 그 동네 이름으로
    known = set(cfg.get("지역단어", [])) | {r for st in cfg.get("확장지역", []) for r in st.get("지역단어", [])}
    if first in known and first != 지역 and first in ("횡성",):
        지역 = first
    별명 = (row.get("별명") or "").strip()[:12] or "이웃"
    조각 = cfg.get("메시지조각")
    if 조각:
        msg = " ".join([random.choice(조각["인사"]),
                        random.choice(조각["본문"]),
                        random.choice(조각["맺음"])])
    else:
        msg = random.choice(cfg["메시지템플릿"])
    return msg.replace("{별명}", 별명).replace("{지역}", 지역)[:100]


def human_gap(cfg):
    """사람의 행동 간격 — 균등분포가 아니라 로그정규분포. 중앙값 약 60초."""
    lo, hi = cfg.get("신청간격초", [45, 90])
    g = random.lognormvariate(math.log(max(lo, 30) * 1.3), 0.45)
    return max(35.0, min(g, 240.0))


# ──────────────────────────── 화면 판정·조작 ────────────────────────────

ALERT_SEL = '#_alertLayerClose'          # 실측(2026-09-08): 예외는 이 알림층 (예: 「나 자신은 이웃으로 추가할 수 없습니다」)
CONFIRM_SEL = '#_confirmLayerOk'         # 실측(2026-09-08): 이미 보낸 신청 → 「서로이웃 신청 진행중입니다. 취소하시겠습니까?」 확인창. 확인=취소!


def page_state(page):
    """BuddyAddForm 화면을 구조로 판정한다. 글자는 알림·확인창 안 문구만 본다 —
    본문 전체로 보면 상대 블로그명·별명에 든 단어(「제한」「완료」…)가 오판을 만든다. (2026-09-08 자체 검증)
    반환: ("폼", "") | ("대기확인창", 문구) | ("알림", 문구) | ("보안문자", 문구) | ("모름", 본문)"""
    try:
        if page.query_selector(CONFIRM_SEL):
            return "대기확인창", page.inner_text("body")
        al = page.query_selector(ALERT_SEL)
        if al and al.is_visible():
            return "알림", page.inner_text("body")                 # 알림 상태에선 폼이 숨어 본문 = 알림 문구 (실측)
        if page.is_visible("input#bothBuddyRadio") or page.is_visible("input#buddyRadio"):
            return "폼", ""
        body = page.inner_text("body")
        t = body.replace(" ", "")
        if "보안문자" in t or "자동입력방지" in t:
            return "보안문자", body
        return "모름", body
    except Exception as e:
        return "모름", f"({type(e).__name__})"


def classify_alert(text):
    """알림·확인창 문구 → 상태. 「신청 진행중」만 실측, 나머지는 추정 키워드(첫 사례 스냅으로 보정)."""
    t = text.replace(" ", "")
    if "보안문자" in t or "자동입력방지" in t:
        return "보안문자"
    if "하루에신청할수" in t or ("신청" in t and "초과" in t and "하루" in t) or "일일" in t:
        return "일일한도"
    if "신청진행중" in t or "신청중" in t or "수락대기" in t or "이미신청" in t:
        return "대기중(기존)"
    if "이미추가한이웃" in t or "이미서로이웃" in t or "서로이웃인" in t or "이웃으로추가되어" in t or "이미이웃" in t:
        return "이미이웃"                    # 실측(2026-09-08): 「이미 추가한 이웃입니다.」
    if "받지않" in t:
        return "서이추거부설정"
    if "모든그룹" in t or ("그룹" in t and ("초과" in t or "만들어주세요" in t)):
        # 실측(2026-10-13, 이웃 290명): 「모든 그룹의 이웃수가 초과되어 이웃을 추가할 수 없습니다
        # PC에서 새 그룹을 만들어주세요」 — 신청이 안 나간 상태다. 실패-* 로 둬서 나중에 다시 간다
        return "실패-그룹한도"
    if "제한" in t and "이용" in t:
        return "이용제한"
    if "나자신은" in t:
        return "실패-자기자신"
    if "5,000" in t or "5000" in t or "한도" in t:
        return "이웃한도(상대)"
    return None


def click_first(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                return sel
        except Exception:
            pass
    return None


def browse_blog(page, bid, read_post):
    """블로그에 들러 사람처럼 머문다. read_post=True면 글 하나를 열어 읽는다."""
    page.goto(f"https://m.blog.naver.com/{bid}",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(random.randint(1500, 3500))
    page.mouse.wheel(0, random.randint(500, 1500))
    page.wait_for_timeout(random.randint(800, 2000))
    if not read_post:
        return
    try:
        import re
        for a in page.query_selector_all(f'a[href*="{bid}/"]'):
            href = a.get_attribute("href") or ""
            m = re.search(rf"{bid}/(\d{{8,}})", href)
            if m:
                page.goto(f"https://m.blog.naver.com/{bid}/{m.group(1)}",
                          wait_until="domcontentloaded", timeout=30000)
                for _ in range(random.randint(2, 4)):        # 읽는 시늉 — 체류+스크롤
                    page.wait_for_timeout(random.randint(2500, 6000))
                    page.mouse.wheel(0, random.randint(700, 2000))
                page.wait_for_timeout(random.randint(1000, 2500))
                break
    except Exception:
        pass                                                 # 글 읽기는 덤 — 실패해도 무시


def request_one(page, row, msg, dry, cfg=None):
    """한 명에게 신청. 반환: 상태 문자열."""
    bid = row["블로그ID"]
    browse_blog(page, bid, read_post=random.random() < 0.6)
    page.goto(f"https://m.blog.naver.com/BuddyAddForm.naver?blogId={bid}",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1800)
    if "nidlogin" in page.url:
        return "실패-로그인풀림"

    kind, text = page_state(page)
    if kind == "대기확인창":
        return "대기중(기존)"                                    # 이 화면의 「확인」은 취소다. 아무것도 누르지 않는다
    if kind == "보안문자":
        return "보안문자"
    if kind == "알림":
        st = classify_alert(text)
        if st:
            return st
        snap(page, f"알림모름_{bid}")
        return "실패-알림모름"
    if kind == "모름":
        snap(page, f"화면모름_{bid}")
        return "실패-화면모름"
    if not page.query_selector("input#bothBuddyRadio"):            # 이웃 라디오만 있다 = 서로이웃을 안 받는 설정
        snap(page, f"거부설정_{bid}")
        return "서이추거부설정"
    try:                                                            # 실측 09-21: 라디오는 있는데 회색으로 잠김 = 같은 뜻
        if page.is_disabled("input#bothBuddyRadio"):
            snap(page, f"거부설정_잠김_{bid}")
            return "서이추거부설정"
    except Exception:
        pass

    # ① 「서로이웃」 선택 — 실측: 라디오 name=relation, #buddyRadio(value 2)=이웃(기본 선택),
    #    #bothBuddyRadio(value 1)=서로이웃. 서로이웃을 골라야 메시지 칸(textarea)이 나타난다
    picked = click_first(page, [
        'input#bothBuddyRadio', 'label[for="bothBuddyRadio"]',
        'label:has-text("서로이웃을 신청")'])
    page.wait_for_timeout(random.randint(500, 1200))
    try:
        if not page.is_checked('input#bothBuddyRadio'):
            snap(page, f"서로이웃미선택_{bid}")
            return "실패-서로이웃미선택"                       # 이웃(단방향)으로 보내면 안 된다
    except Exception:
        pass

    # ①-2 담을 그룹 고르기 (실측 DOM: select#buddyGroupSelect · 기본값 「새그룹」)
    #   설정에 「이웃그룹」 이름이 있으면 그 그룹을 고른다. 2026-10-13 이웃 290명에서
    #   「모든 그룹의 이웃수가 초과」로 신청이 막혔다 — 자리가 남은 그룹을 직접 골라야 한다.
    want_group = ((cfg or {}).get("이웃그룹") or "").strip()
    if want_group:
        try:
            sel = page.query_selector("select#buddyGroupSelect")
            if sel:
                labels = [(o.inner_text() or "").strip() for o in sel.query_selector_all("option")]
                if want_group in labels:
                    sel.select_option(label=want_group)
                    page.wait_for_timeout(random.randint(300, 700))
                else:
                    print(f"      [i] 설정의 그룹 「{want_group}」 이 목록에 없습니다 (목록: {', '.join(labels)})")
        except Exception:
            pass
    # 그룹이 하나도 없는 계정(배포용)은 그룹 이름 입력칸이 뜬다 — 비어 있으면 채운다 (실측 DOM: #groupAddText)
    try:
        g = page.query_selector("input#groupAddText")
        if g and g.is_visible() and not (g.input_value() or "").strip():
            g.type(want_group or "이웃", delay=random.randint(60, 140))
    except Exception:
        pass

    # ② 메시지 입력 — 실측: textarea.textarea_t1 (#inviteMessage, 자리글 「우리 서로이웃해요~」)
    #    붙여넣기가 아니라 한 글자씩 친다 (사람의 입력 리듬)
    filled = False
    for sel in ('textarea#inviteMessage', 'textarea.textarea_t1', 'textarea'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(random.randint(400, 900))
                el.fill("")                                      # 기본 문구 「우리 서로이웃해요~」가 값으로 들어 있다 — 먼저 비운다 (2026-09-08 스냅에서 발견)
                el.type(msg, delay=random.randint(45, 110))
                if el.input_value().strip() != msg.strip():      # 친 내용이 정확한지 확인하고 아니면 붙여넣기로 대체
                    el.fill(msg)
                filled = True
                break
        except Exception:
            pass

    if not picked or not filled:
        snap(page, f"모름_{bid}")
        return "실패-화면모름"

    if dry:
        snap(page, f"확인만_{bid}")
        return "확인만(미제출)"

    page.wait_for_timeout(random.randint(700, 1500))         # 제출 전 한 박자

    # ③ 제출 — 실측: 오른쪽 위 a.btn_ok「확인」 (a.btn_close 는 취소)
    ok = click_first(page, ['a.btn_ok', 'a:text-is("확인")', 'button:has-text("확인")'])
    if not ok:
        snap(page, f"제출버튼모름_{bid}")
        return "실패-제출버튼"
    page.wait_for_timeout(1800)

    after = page.inner_text("body").replace(" ", "")
    if "신청완료" in after or "신청하였습니다" in after:        # 실측(2026-09-08): 「서로이웃 신청 완료 / …서로이웃을 신청하였습니다.」
        return "신청"
    kind, text = page_state(page)
    if kind in ("알림", "보안문자"):
        st = classify_alert(text)
        if st in ("일일한도", "보안문자", "이용제한", "이웃한도(상대)", "실패-그룹한도"):
            if st == "실패-그룹한도":
                snap(page, f"그룹한도_{bid}")
            return st
    body_t = after                                            # 알림층이 안 잡혀도 본문에 문구가 남는 경우가 있다
    if "모든그룹" in body_t or ("그룹" in body_t and "초과" in body_t):
        snap(page, f"그룹한도_{bid}")
        return "실패-그룹한도"
    snap(page, f"제출후_{bid}")
    return "신청(확인필요)"          # 제출은 눌렀는데 완료 문구를 못 읽음


# ──────────────────────────── 본체 ────────────────────────────

def main():
    cfg = load_cfg()
    dry = "--확인만" in sys.argv
    force = "--강행" in sys.argv

    cool = cooldown_active()
    if cool and not dry:
        print(f"[!] 쿨다운 중({cool['reason']}) — {cool['until']}까지 신청을 쉽니다.")
        return

    h0, h1 = cfg.get("활동시간", [9, 23])
    now = datetime.now()
    if not force and not dry and not (h0 <= now.hour < h1):
        print(f"[i] 지금은 활동 시간대({h0}~{h1}시)가 아닙니다. 사람은 이 시간에 서이추를 안 합니다.")
        print("    (테스트로 강행하려면 --강행)")
        return

    # 오늘 목표: 워밍업 램프 × 그날그날 흔들림(70~100%). --한도는 그대로 존중
    if "--한도" in sys.argv:
        cap = int(sys.argv[sys.argv.index("--한도") + 1])
        target = cap
    else:
        cap = cap_today(cfg)
        target = max(1, int(cap * random.uniform(0.7, 1.0)))

    already = sent_today()
    remain = max(0, target - already)
    if remain == 0:
        print(f"[i] 오늘 목표({target}) 달성 (이미 {already}건). 내일 다시.")
        return

    known = blocked_ids() | excluded_ids(cfg)
    targets = [r for r in load_approved(cfg)
               if r["블로그ID"] not in known and assigned(r["블로그ID"], cfg)][:remain]
    if not targets:
        print("[!] 신청할 후보가 없습니다. 후보뽑기 → CSV 검토(승인 O) 먼저.")
        return

    shop_p = cfg.get("윈도쇼핑확률", 0.08)
    b_lo, b_hi = cfg.get("배치크기", [3, 6])
    r_lo, r_hi = cfg.get("배치휴식초", [180, 480])
    est = int(len(targets) * 2.2)
    print(f"[i] 오늘 목표 {target}건 (램프 한도 {cap} · 이미 {already}건) → 지금 {len(targets)}명, 약 {est}분"
          + ("  ※ 확인만 모드 — 제출 안 함" if dry else ""))

    done, unknown_streak, fail_streak = [], 0, 0
    batch_left = random.randint(b_lo, b_hi)
    with sync_playwright() as pw:
        ctx = launch_ctx(pw, cfg, 용도="신청")
        page = ctx.new_page()
        if not ensure_login(page, cfg):
            ctx.close()
            return
        for i, row in enumerate(targets, 1):
            if random.random() < shop_p and not dry:
                # 구경만 — 사람은 모두에게 신청하지 않는다. 후보는 다음 실행에 남는다
                try:
                    browse_blog(page, row["블로그ID"], read_post=True)
                    print(f"  ({i}/{len(targets)}) {row['블로그ID']} → 구경만 (신청 안 함)")
                except Exception:
                    pass
                time.sleep(human_gap(cfg))
                continue

            msg = pick_message(cfg, row)
            try:
                st = request_one(page, row, msg, dry, cfg)
            except Exception as e:
                st = f"실패-{type(e).__name__}"
            print(f"  ({i}/{len(targets)}) {row['블로그ID']} → {st}")

            if not dry and not st.startswith("확인만"):
                rec = {"신청일": date.today().isoformat(),
                       "블로그ID": row["블로그ID"], "별명": row.get("별명", ""),
                       "발굴키워드": row.get("발굴키워드", ""),
                       "상태": st, "갱신일": date.today().isoformat(),
                       "메모": msg if st.startswith("신청") else ""}
                ledger_append([rec])                              # 즉시 기록 — 중간에 꺼져도 같은 사람에게 두 번 안 보낸다
                done.append(rec)

            if st == "실패-로그인풀림":
                print("[!] 로그인이 풀렸습니다 — 멈춥니다. 다시 실행하면 로그인 창이 뜹니다.")
                break

            if st in ("보안문자", "이용제한"):
                print(f"[!] 네이버 경고 신호({st}) — 즉시 중단하고 쿨다운을 겁니다.")
                if not dry:
                    set_cooldown(3, st)
                break
            if st == "일일한도":
                print("[!] 네이버 쪽 일일 한도 — 오늘은 여기까지.")
                break
            if st == "실패-그룹한도":
                print("[!] 이웃 그룹이 가득 찼습니다 — 신청이 나가지 않습니다.")
                print("    PC 네이버 블로그 → 관리 → 열린이웃 → 이웃·그룹 관리에서 새 그룹을 하나 만들어 주세요.")
                print("    만든 뒤 이웃그룹.txt 에 그 이름을 적으면 그 그룹으로 담습니다 (자동운행은 알아서 만듭니다).")
                break
            unknown_streak = unknown_streak + 1 if ("모름" in st or "확인필요" in st) else 0
            fail_streak = fail_streak + 1 if st.startswith("실패") else 0
            if unknown_streak >= 2:
                print("[!] 화면을 연속으로 못 알아봤습니다 — 기록/스냅/ 캡처 보정이 필요합니다.")
                break
            if fail_streak >= 3:
                print("[!] 연속 실패 3회 — 상태가 이상하니 멈춥니다.")
                break

            if i < len(targets):
                batch_left -= 1
                if batch_left <= 0:
                    rest = random.uniform(r_lo, r_hi)
                    print(f"    ☕ {int(rest // 60)}분 {int(rest % 60)}초 휴식 (배치 리듬)")
                    time.sleep(rest)
                    batch_left = random.randint(b_lo, b_hi)
                else:
                    time.sleep(human_gap(cfg))
        ctx.close()

    if done:
        sent = sum(1 for d in done if d["상태"].startswith("신청"))
        print(f"[✓] 장부 기록 {len(done)}건 (실제 신청 {sent}건) → 기록/장부.csv")
    if dry:
        print("[i] 확인만 모드였습니다. 기록/스냅/ 캡처로 화면이 맞는지 본 뒤 실제 실행하세요.")


if __name__ == "__main__":
    main()
