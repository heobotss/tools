# -*- coding: utf-8 -*-
"""이웃 그룹 만들기 — PC 블로그 관리 화면에서 새 이웃 그룹을 추가한다.

왜: 2026-10-13 이웃 322명에서 신청 폼이 「모든 그룹의 이웃수가 초과되어 이웃을 추가할 수 없습니다
    PC에서 새 그룹을 만들어주세요」를 띄웠다. 한 그룹에 담기는 수가 꽉 찬 것이고, 모바일로는 못 만든다.

  python 그룹만들기.py --목록                  지금 그룹과 인원만 읽는다 (아무것도 안 바꾼다)
  python 그룹만들기.py --만들기 "원주이웃2"      그룹 추가 + 계정 자리의 이웃그룹.txt 에 이름 저장 (10-22~)
  python 그룹만들기.py --자동                  이름을 알아서 정해(원주이웃2 → 3 → 4) 추가. 자동운행이 이걸 부른다
  python 그룹만들기.py --만들기 "원주이웃2" --설정안함   설정은 건드리지 않는다

실측 구조 (2026-10-13):
  관리 화면 https://admin.blog.naver.com/<블로그ID>/buddy/manage 안의 **papermain 프레임**이 진짜 화면이다.
  「이웃그룹」 탭 → BuddyGroupManage.naver · 「그룹추가」 button.btn_addgroup · 그룹명 input[name=name]
  로그인 프로필을 그대로 쓰되 PC 화면으로 연다 (관리 화면은 모바일 위장으로 안 열린다).
"""
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

from 공용 import REC, PROFILE_DIR, BROWSER_ARGS, CFG_FILE, GROUP_FILE, load_cfg, save_cfg, save_group, naver_logged_in

OUT = REC / "그룹"
OUT.mkdir(parents=True, exist_ok=True)
MANAGE = "https://admin.blog.naver.com/{bid}/buddy/manage"


def desktop_ctx(pw, cfg):
    opts = dict(headless=False, locale="ko-KR",
                viewport={"width": 1440, "height": 950}, args=BROWSER_ARGS)
    want = cfg.get("브라우저", "chrome")
    for ch in [want] + [c for c in ("chrome", "msedge", "chromium") if c != want]:
        try:
            if ch == "chromium":
                return pw.chromium.launch_persistent_context(PROFILE_DIR, **opts)
            return pw.chromium.launch_persistent_context(PROFILE_DIR, channel=ch, **opts)
        except Exception:
            continue
    raise RuntimeError("브라우저 실행 실패")


def dump(page, frame, tag):
    st = datetime.now().strftime("%m%d_%H%M%S")
    try:
        page.screenshot(path=str(OUT / f"{st}_{tag}.png"), full_page=True)
    except Exception:
        pass
    try:
        (OUT / f"{st}_{tag}.html").write_text(frame.content(), encoding="utf-8")
    except Exception:
        pass
    print(f"    화면 저장: {st}_{tag}")


def group_frame(page):
    """papermain 프레임에서 「이웃그룹」 탭을 연다."""
    for f in page.frames:
        try:
            if f.name == "papermain" and f.query_selector('a:has-text("이웃그룹")'):
                f.click('a:has-text("이웃그룹")')
                page.wait_for_timeout(2500)
                return next((x for x in page.frames if x.name == "papermain"), f)
        except Exception:
            continue
    return None


def read_groups(frame):
    """[(그룹명, 이웃수)] — 표에서 긁는다."""
    try:
        return frame.evaluate("""() => [...document.querySelectorAll('tr')]
            .map(tr => (tr.innerText || '').replace(/\\s+/g, ' ').trim())
            .map(t => t.match(/^(.+?)\\s*\\|?\\s*수정\\s*(\\d+)명/))
            .filter(Boolean).map(m => [m[1].trim(), Number(m[2])])""")
    except Exception:
        return []


def next_name(groups, base):
    """이미 있는 그룹에서 다음 이름을 정한다 — 원주이웃2 가 있으면 원주이웃3.
    「새그룹」처럼 틀과 다른 이름은 세지 않는다."""
    import re
    n = 1
    for g, _ in groups:
        m = re.match(rf"^{re.escape(base)}(\d*)$", g.strip())
        if m:
            n = max(n, int(m.group(1) or 1))
    return f"{base}{n + 1}"


def add_group(page, frame, name):
    """그룹추가 → 이름 입력 → 「만들기」. 성공하면 True.

    실측 DOM (2026-10-13): 「그룹추가」를 누르면 층이 열리고 그 안에 form#addGroupForm 이 있다.
      · 이름칸 input[name=name] — 기본값 「그룹명을 써주세요」가 들어 있고 onfocus 로 비워진다
      · 공개/비공개 라디오 input[name=open] (기본 공개)
      · 저장 버튼은 button 이 아니라 **input[type=image] alt="만들기"**  ← 「확인」으로 찾으면 못 찾는다
    """
    frame.click("button.btn_addgroup")
    page.wait_for_timeout(1200)
    box = frame.query_selector('#addGroupForm input[name="name"]') or frame.query_selector('input[name="name"]')
    if not box:
        print("[!] 그룹명 입력칸을 못 찾았습니다.")
        return False
    box.click()                                     # onfocus 가 기본 문구를 지운다
    page.wait_for_timeout(300)
    box.fill("")
    box.type(name, delay=90)
    page.wait_for_timeout(400)
    if (box.input_value() or "").strip() != name:
        box.fill(name)
    btn = (frame.query_selector('#addGroupForm input[type="image"]')
           or frame.query_selector('input[alt="만들기"]'))
    if not btn:
        print("[!] 「만들기」 버튼을 못 찾았습니다.")
        return False
    btn.click()
    page.wait_for_timeout(2500)
    return True


def main():
    cfg = load_cfg()
    bid = cfg["계정ID"]
    name = None
    auto = "--자동" in sys.argv
    if "--만들기" in sys.argv:
        i = sys.argv.index("--만들기")
        name = sys.argv[i + 1].strip() if len(sys.argv) > i + 1 else ""
        if not name or name.startswith("--"):
            print('[!] 이름을 같이 주세요:  python 그룹만들기.py --만들기 "원주이웃2"')
            return 1
    with sync_playwright() as pw:
        ctx = desktop_ctx(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(MANAGE.format(bid=bid), wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(3500)
            if not naver_logged_in(page):
                print("[!] 로그인이 안 돼 있습니다. 먼저 로그인.bat 을 실행하세요.")
                return 1
            frame = group_frame(page)
            if not frame:
                print("[!] 이웃그룹 탭을 못 찾았습니다 — 화면 구조가 바뀌었을 수 있습니다.")
                dump(page, page.main_frame, "탭못찾음")
                return 1
            before = read_groups(frame)
            print("  지금 그룹:", ", ".join(f"{n} {c}명" for n, c in before) or "(못 읽음)")
            if auto and not name:
                base = (cfg.get("그룹이름틀") or "원주이웃").strip()
                name = next_name(before, base)
                print(f"  [i] 이름 자동 결정: {name}")
            if not name:
                dump(page, frame, "그룹목록")
                return 0
            if any(n == name for n, _ in before):
                print(f"  [i] 「{name}」 그룹이 이미 있습니다.")
            else:
                if not add_group(page, frame, name):
                    dump(page, frame, "추가실패")
                    return 1
                frame = next((x for x in page.frames if x.name == "papermain"), frame)
                after = read_groups(frame)
                dump(page, frame, "추가후")
                if not any(n == name for n, _ in after):
                    print(f"[!] 「{name}」 이 목록에 안 보입니다 — 캡처를 확인하세요.")
                    print("    지금 목록:", ", ".join(f"{n} {c}명" for n, c in after))
                    return 1
                print(f"  [✓] 그룹 「{name}」 추가됨")
                print("  지금 그룹:", ", ".join(f"{n} {c}명" for n, c in after))
            if "--설정안함" not in sys.argv:
                save_group(name)          # 계정별 파일 — 설정.json 에 쓰면 계정끼리 섞이고 배포판 덮을 때 지워졌다
                print(f"  [✓] 이웃그룹 = {name}  ({GROUP_FILE})")
        finally:
            ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
