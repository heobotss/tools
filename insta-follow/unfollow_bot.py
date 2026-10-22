# -*- coding: utf-8 -*-
"""
인스타그램 언팔 프로그램 (혼자 돌아감)

작업목록.csv를 위에서부터 읽어, 프로필을 열어보고 판단한 뒤 언팔합니다.
진행상황.csv에 매 건 기록하므로 껐다 켜도 이어서 갑니다.

    python unfollow_bot.py --dry-run     실제로 누르지 않고 판단만 (처음엔 이걸로)
    python unfollow_bot.py               실제 실행
    python unfollow_bot.py --limit 20    이번 실행에서 20명만

처음 한 번은 브라우저가 뜨면 직접 인스타에 로그인하세요.
로그인 정보는 크롬프로필/ 폴더에 남아서 다음부터는 자동입니다.
"""

import sys
import os
import csv
import time
import random
import argparse
import datetime
import safe_file

if hasattr(sys.stdout, "reconfigure"):
    # 콘솔이 쓰는 인코딩을 그대로 두고, 표현 못 하는 글자만 ? 로 바꾼다.
    # utf-8로 강제하면 한국어 Windows cmd 창(CP949)에서 한글이 깨진다.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

# playwright가 없으면 개발자용 오류 화면 대신 설치 안내를 보여준다.
# (설치.bat을 건너뛰고 시작.bat부터 누르는 분이 실제로 있었다)
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "\n" + "=" * 56 + "\n"
        "  아직 설치가 안 된 상태입니다\n"
        + "=" * 56 + "\n"
        "  프로그램에 필요한 부품(playwright)이 없습니다.\n"
        "\n"
        "  이 폴더의 '설치.bat' 을 두 번 클릭해서 설치를 먼저\n"
        "  해주세요. 처음 한 번만 하면 됩니다. (5~10분)\n"
        "\n"
        "  설치.bat 을 이미 돌렸는데도 이 화면이 나온다면,\n"
        "  설치가 중간에 실패한 것입니다. 인터넷 연결을 확인하고\n"
        "  '설치.bat' 을 한 번 더 실행해 주세요.\n"
        + "=" * 56)

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "작업목록.csv")
LOG = os.path.join(HERE, "진행상황.csv")
CONF = os.path.join(HERE, "설정.txt")
PROFILE_DIR = os.path.join(HERE, "크롬프로필")


# ---------------------------------------------------------------- 설정

def load_config():
    cfg = {"시간당_최대": 20, "하루_최대": 150, "대기_최소": 15, "대기_최대": 35,
           "연속실패_한도": 3, "큰계정_기준": 50000, "브라우저": "chrome",
           "팔로우_하루_최대": 100, "팔로우_대기_최소": 25, "팔로우_대기_최대": 45,
           "수집_목표": 300, "최소_팔로워": 50,
           "제외계정": set(), "트레이너단어": [], "공부단어": [], "해시태그": []}
    if not os.path.exists(CONF):
        return cfg

    section = None
    # utf-8-sig: 메모장으로 저장하면 BOM이 붙는데, 그래도 읽히도록
    with open(CONF, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            # '이름 = 값' 이고 아는 설정 이름이면, 섹션 안이든 밖이든 설정으로 읽는다.
            # (섹션 뒤에 설정을 써도 해시태그 같은 목록으로 오해하지 않도록)
            k = line.split("=", 1)[0].strip() if "=" in line else None
            if k and (k in cfg or k == "브라우저"):
                v = line.split("=", 1)[1].strip()
                if k == "브라우저":
                    cfg[k] = v.lower()
                else:
                    try:
                        cfg[k] = int(v)
                    except ValueError:
                        pass
            elif section == "제외계정":
                cfg["제외계정"].add(line.lower().lstrip("@"))
            elif section in ("트레이너단어", "공부단어"):
                cfg[section].append(line.lower())
            elif section == "해시태그":
                cfg["해시태그"].append(line.lstrip("#"))
    return cfg


def warn_if_cloud_folder():
    """OneDrive 같은 동기화 폴더에서 돌리면 파일 접근이 막히는 일이 잦다.

    클라우드 폴더는 파일을 '실제로는 없는 상태'로 두었다가 필요할 때 받아오는데,
    그 과정에서 프로그램이 파일을 읽고 쓰지 못해 '액세스할 수 없습니다' 가 뜬다.
    카카오톡으로 받은 파일이 OneDrive\\문서 아래에 들어가는 경우가 특히 많다.
    """
    low = HERE.lower()
    for key, name in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"),
                      ("google drive", "구글 드라이브"), ("googledrive", "구글 드라이브"),
                      ("icloud", "iCloud")):
        if key in low:
            print("")
            print("!" * 56)
            print("  주의:  %s 폴더 안에서 실행하고 있습니다" % name)
            print("!" * 56)
            print("  이 상태로는 '액세스할 수 없습니다' 오류가 날 수 있습니다.")
            print("")
            print("  이 폴더를 통째로 아래 같은 곳으로 옮긴 뒤 다시 실행하세요.")
            print("")
            print("      C:\\인스타관리")
            print("")
            print("  (바탕화면도 OneDrive 와 연결돼 있으면 같은 문제가 납니다.")
            print("   C 드라이브 바로 아래에 폴더를 만드는 것이 가장 확실합니다.)")
            print("")
            print("  현재 위치: %s" % HERE)
            print("!" * 56)
            print("")
            return True
    return False


def get_start():
    p = os.path.join(HERE, "시작지점.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip().isdigit():
                    return int(line.strip())
    return 1


# ---------------------------------------------------------------- 기록

def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_log(name, result, memo=""):
    exists = os.path.exists(LOG)
    with safe_file.open_write(LOG, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["시각", "아이디", "결과", "메모"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), name, result, memo])


def last_results():
    """아이디(소문자) -> 가장 마지막 결과"""
    out = {}
    for r in read_log():
        out[r["아이디"].lower()] = r["결과"]
    return out


def load_targets(cfg, phase=1):
    if not os.path.exists(QUEUE):
        raise SystemExit(
            "\n" + "=" * 56 + "\n"
            "  먼저 '내 데이터 불러오기' 를 해야 합니다\n"
            + "=" * 56 + "\n"
            "  아직 누구를 언팔할지 목록이 만들어지지 않았습니다.\n"
            "\n"
            "  1) 인스타에서 내 데이터를 내려받으세요.\n"
            "     설정 - 계정 센터 - 내 정보 및 권한 - 내 정보 다운로드\n"
            "     * '팔로워 및 팔로잉' 만 체크\n"
            "     * 형식은 반드시 JSON  (HTML 아님)\n"
            "\n"
            "  2) 받은 ZIP 파일을 이 폴더에 넣으세요. (압축 안 풀어도 됩니다)\n"
            "\n"
            "  3) 메뉴로 돌아가서 [ 2번 - 내 데이터 불러오기 ] 를 누르세요.\n"
            + "=" * 56)
    with open(QUEUE, encoding="utf-8-sig") as f:
        q = list(csv.DictReader(f))

    seen = last_results()
    start = get_start()

    if phase >= 2:
        # 2단계는 1단계에서 보류해 둔 트레이너 계정만 순서대로 다시 본다
        return [r for r in q
                if seen.get(r["아이디"].lower()) == "deferred_trainer"
                and r["아이디"].lower() not in cfg["제외계정"]]

    return [r for r in q
            if int(r["순번"]) >= start
            and r["아이디"].lower() not in seen
            and r["아이디"].lower() not in cfg["제외계정"]]


def today_count():
    """오늘 '실제로 언팔한' 건수.

    보류·남김·계정없음은 인스타 입장에서 아무 행동도 하지 않은 것이므로
    하루 한도에 넣지 않는다. 한도는 실제 언팔 건수 기준이다.
    """
    today = datetime.date.today().strftime("%Y-%m-%d")
    return sum(1 for r in read_log()
               if r["시각"].startswith(today) and r["결과"] == "unfollowed")


# ---------------------------------------------------------------- 판단

def parse_followers(header_text):
    """헤더 텍스트에서 '팔로워 1,828' 같은 숫자를 뽑는다."""
    import re
    # 단위는 숫자 바로 뒤에서만 인정한다. 줄바꿈을 넘어가면 안 된다.
    # (안 그러면 '팔로워 325' 다음 줄의 'KR'을 K(천)으로 읽어버린다)
    unit = r"(만|천|[KkMm](?![A-Za-z0-9]))?"
    m = re.search(r"팔로워[ \t]*([\d,\.]+)[ \t]*" + unit, header_text)
    if not m:
        m = re.search(r"([\d,\.]+)[ \t]*" + unit + r"[ \t]*followers", header_text, re.I)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    if unit == "만":
        num *= 10000
    elif unit == "천":
        num *= 1000
    elif unit == "k":
        num *= 1000
    elif unit == "m":
        num *= 1000000
    return int(num)


def hit(word, text):
    """짧은 영문 단어(pt, gym 등)는 앞뒤가 글자면 오탐이므로 경계를 본다."""
    if not word:
        return False
    if word.isascii() and len(word) <= 3:
        import re
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(word), text) is not None
    return word in text


def judge(name, header_text, cfg, phase=1):
    """(판정, 사유) 반환

    판정은 넷 중 하나다.
      unfollow          언팔한다
      kept_study        공부용 계정. 단계와 무관하게 영구 제외
      deferred_trainer  트레이너 계정. 1단계에서는 보류, 2단계에서 언팔
      kept_big          큰 계정. 영구 제외

    검사 순서가 곧 우선순위다. 공부용을 먼저 보므로,
    '해부학 교육하는 트레이너'는 트레이너가 아니라 공부용으로 분류돼 계속 남는다.

    판단은 '소개글과 이름'으로 한다. 첫 줄(아이디)은 빼고 본다.
    아이디에 gym이 들어갔다고 트레이너인 건 아니기 때문이다.
    """
    lines = header_text.split("\n")
    body = "\n".join(lines[1:]) if len(lines) > 1 else header_text
    low = body.lower()

    for word in cfg["공부단어"]:
        if hit(word, low):
            return "kept_study", "공부용 계정 ('%s')" % word

    for word in cfg["트레이너단어"]:
        if hit(word, low):
            if phase >= 2:
                return "unfollow", "트레이너 계정 ('%s') · 2단계" % word
            return "deferred_trainer", "트레이너 계정 ('%s') · 2단계로 보류" % word

    n = parse_followers(header_text)
    if n is not None and n >= cfg["큰계정_기준"]:
        return "kept_big", "팔로워 %d명 (큰 계정)" % n

    return "unfollow", "팔로워 %s" % (n if n is not None else "?")


# ---------------------------------------------------------------- 브라우저

def open_profile(page, name):
    page.goto("https://www.instagram.com/%s/" % name,
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)


def header_text(page):
    for sel in ("header", "main header", "main"):
        try:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text()
                if t and len(t) > 10:
                    return t[:1500]
        except Exception:
            pass
    return ""


def find_following_button(page):
    """프로필 상단의 '팔로잉' 버튼을 찾는다."""
    for sel in ('header button:has-text("팔로잉")',
                'main button:has-text("팔로잉")',
                'header button:has-text("Following")',
                'main button:has-text("Following")'):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                return btn
        except Exception:
            pass
    return None


def already_unfollowed(page):
    for sel in ('header button:has-text("팔로우"), header button:has-text("Follow")',):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                txt = (btn.inner_text() or "").strip()
                if txt in ("팔로우", "Follow"):
                    return True
        except Exception:
            pass
    return False


def click_unfollow_in_dialog(page):
    """열린 대화상자에서 '팔로우 취소'를 누른다. 다른 항목은 절대 누르지 않는다."""
    page.wait_for_timeout(900)
    for sel in ('div[role="dialog"] button:has-text("팔로우 취소")',
                'div[role="dialog"] :text-is("팔로우 취소")',
                'div[role="dialog"] button:has-text("Unfollow")',
                'div[role="dialog"] :text-is("Unfollow")'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                return True
        except Exception:
            pass
    return False


def close_dialog(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception:
        pass


def launch_ctx(pw, cfg):
    """설정한 브라우저로 띄운다. 없으면 Playwright 기본 Chromium으로 물러선다.

    chrome  : 설치된 구글 크롬 (권장 - 지문이 평범함)
    msedge  : 설치된 엣지
    chromium: Playwright 내장 브라우저 (항상 있음)
    """
    # 뒤쪽 세 개는 창이 가려지거나 최소화돼도 작업이 느려지지 않게 한다.
    # 돌려두고 다른 창에서 문서 작업을 해도 되도록.
    opts = dict(headless=False, locale="ko-KR",
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-backgrounding-occluded-windows",
                      "--disable-renderer-backgrounding",
                      "--disable-background-timer-throttling"])

    want = cfg.get("브라우저", "chrome")
    order = [want] if want in ("chrome", "msedge", "chromium") else ["chrome"]
    for fb in ("chrome", "msedge", "chromium"):
        if fb not in order:
            order.append(fb)

    last = None
    for ch in order:
        try:
            if ch == "chromium":
                ctx = pw.chromium.launch_persistent_context(PROFILE_DIR, **opts)
            else:
                ctx = pw.chromium.launch_persistent_context(PROFILE_DIR, channel=ch, **opts)
            name = {"chrome": "구글 크롬", "msedge": "엣지", "chromium": "Chromium(내장)"}[ch]
            if ch != want:
                print("[i] %s를 못 찾아 %s로 실행합니다." % (want, name))
            else:
                print("[i] 브라우저: %s" % name)
            return ctx
        except Exception as e:
            last = e
    raise SystemExit("[!] 브라우저를 띄우지 못했습니다: %s" % last)


def is_logged_in(ctx):
    """로그인 여부는 sessionid 쿠키로 판단한다. 화면 모양보다 확실하다."""
    try:
        for c in ctx.cookies("https://www.instagram.com"):
            if c.get("name") == "sessionid" and c.get("value"):
                return True
    except Exception:
        pass
    return False


def wait_for_login(ctx, page, minutes=15):
    """브라우저를 띄워두고 사용자가 로그인할 때까지 기다린다."""
    print("")
    print("=" * 56)
    print("  로그인이 필요합니다")
    print("=" * 56)
    print("  방금 뜬 브라우저 창에서 인스타그램에 직접 로그인하세요.")
    print("  (회원님이 평소 쓰시는 Chrome과는 별개인 전용 창입니다.")
    print("   한 번만 로그인하면 크롬프로필/ 에 저장돼 다음부터는 자동입니다.)")
    print("")
    print("  로그인이 확인되면 자동으로 작업이 시작됩니다. 최대 %d분 기다립니다." % minutes)
    print("=" * 56)
    print("")

    try:
        page.goto("https://www.instagram.com/accounts/login/",
                  wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass

    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        if is_logged_in(ctx):
            print("[+] 로그인 확인됐습니다. 작업을 시작합니다.\n")
            time.sleep(2)
            return True
        left = int(deadline - time.time())
        print("    ...로그인 대기 중 (%d분 %02d초 남음)" % (left // 60, left % 60))
        time.sleep(10)

    print("[!] 시간 안에 로그인이 확인되지 않았습니다. 종료합니다.")
    return False


def check_blocked(page, expect_user=None):
    """차단/보안확인 화면이면 사유 문자열, 아니면 None

    주의: 아이디에 challenge가 들어간 계정(@challenger_bongkiru 등)을
    보안 화면으로 오해하면 안 된다. 경로를 정확히 맞춰서 본다.
    """
    url = (page.url or "").lower()
    path = url.split("instagram.com", 1)[-1] if "instagram.com" in url else url

    # 목표 프로필에 정상 도착했으면 주소만으로 차단이라 볼 수 없다
    on_target = bool(expect_user) and ("/%s/" % expect_user.lower()) in url

    if not on_target:
        if path.startswith("/challenge/") or path.startswith("/accounts/suspended"):
            return "인스타 보안 확인 화면 (challenge)"
        if path.startswith("/accounts/login"):
            return "로그아웃됨 - 다시 로그인 필요"

    # 차단은 보통 대화상자로 뜬다. 본문 전체를 훑으면 게시물 문구에 오탐이 난다.
    try:
        dlg = page.query_selector('div[role="dialog"]')
        text = dlg.inner_text() if dlg else ""
    except Exception:
        text = ""
    if not text:
        return None

    low = text.lower()
    for kw in ("잠시 후 다시 시도", "try again later", "작업이 차단",
               "action blocked", "일시적으로 차단", "일시적으로 제한"):
        if kw in low:
            return "인스타 차단 메시지: %s" % kw
    return None


# ---------------------------------------------------------------- 본체

def do_login():
    """브라우저만 띄워 로그인 세션을 저장하고 끝낸다."""
    cfg = load_config()
    with sync_playwright() as pw:
        ctx = launch_ctx(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        if is_logged_in(ctx):
            print("[+] 이미 로그인돼 있습니다. 바로 실행하시면 됩니다.")
            print("    python unfollow_bot.py --dry-run --limit 30")
            ctx.close()
            return 0

        ok = wait_for_login(ctx, page)
        ctx.close()
        if not ok:
            return 1
        print("[+] 로그인이 크롬프로필/ 에 저장됐습니다.")
        print("    이제 실행하세요:  python unfollow_bot.py --dry-run --limit 30")
        return 0


def run(dry_run, limit, phase=1):
    cfg = load_config()
    targets = load_targets(cfg, phase)
    if not targets:
        if phase >= 2:
            print("[i] 2단계로 보류된 트레이너 계정이 없습니다.")
            print("    1단계를 먼저 끝내세요: python unfollow_bot.py")
        else:
            print("[i] 처리할 대상이 없습니다.")
            n_def = sum(1 for v in last_results().values() if v == "deferred_trainer")
            if n_def:
                print("    1단계가 끝났습니다. 보류된 트레이너 계정 %d명이 있습니다." % n_def)
                print("    2단계로 넘어가려면: python unfollow_bot.py --phase 2")
        return 0

    done_today = today_count()
    room_today = max(0, cfg["하루_최대"] - done_today)
    if room_today == 0:
        print("[i] 오늘 %d명을 이미 처리했습니다. 하루 한도(%d)에 도달해 종료합니다."
              % (done_today, cfg["하루_최대"]))
        return 0

    # 목표는 '실제 언팔 건수'다. 보류·남김이 섞이므로 방문해야 할 계정은 이보다 많다.
    goal = min(room_today, (limit if limit is not None else 10 ** 9))

    print("=" * 52)
    print("  단계        : %d단계 %s" % (phase,
          "(일반 계정 정리 · 트레이너는 보류)" if phase == 1 else "(보류했던 트레이너 정리)"))
    print("  남은 대상   : %d 명" % len(targets))
    print("  오늘 언팔함 : %d 명 (하루 한도 %d · 실제 언팔 기준)"
          % (done_today, cfg["하루_최대"]))
    print("  이번 목표   : %d 명 언팔" % goal)
    print("  모드        : %s" % ("판단만 (실제로 안 누름)" if dry_run else "실제 언팔"))
    print("=" * 52)
    print("")

    # 진행상황.csv 가 엑셀 등에 열려 있으면 브라우저를 띄우기 전에 알려 주고 멈춘다
    # (2026-10-22 실제 사례 — 열린 채로 돌리다 첫 기록에서 죽었다)
    if not dry_run:
        safe_file.check_writable(LOG)

    processed = 0   # 방문한 계정 수
    did = 0         # 실제로 언팔한 수 (하루 한도의 기준)
    fails = 0
    hour_mark = time.time()
    hour_count = 0

    with sync_playwright() as pw:
        ctx = launch_ctx(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 로그인 확인 - 쿠키로 판단한다
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        if not is_logged_in(ctx):
            if not wait_for_login(ctx, page):
                ctx.close()
                return 1

        for row in targets:
            if did >= goal:
                break
            name = row["아이디"]
            seq = row["순번"]

            # 시간당 한도
            if hour_count >= cfg["시간당_최대"]:
                wait = 3600 - (time.time() - hour_mark)
                if wait > 0:
                    print("\n[대기] 시간당 한도 %d명 도달. %d분 쉽니다.\n"
                          % (cfg["시간당_최대"], int(wait / 60) + 1))
                    time.sleep(wait)
                hour_mark = time.time()
                hour_count = 0

            try:
                open_profile(page, name)

                # 로그아웃 상태로 계속 도는 걸 막는다
                if not is_logged_in(ctx):
                    print("\n[중단] 로그인이 풀렸습니다. 로그아웃 상태로는 언팔할 수 없습니다.")
                    print("       python unfollow_bot.py --login  으로 다시 로그인하세요.")
                    break

                blocked = check_blocked(page, name)
                if blocked:
                    print("\n[중단] %s" % blocked)
                    print("       여기서 멈춥니다. 한참 쉬었다가 다시 실행하세요.")
                    break

                head = header_text(page)

                if not head or "페이지를 사용할 수 없습니다" in head or "Sorry, this page" in head:
                    if not dry_run:
                        write_log(name, "notfound", "계정 없음/비활성")
                    print("%5s @%-24s  없음" % (seq, name))
                    processed += 1
                    fails = 0
                    continue

                if already_unfollowed(page):
                    # 실제로 누른 게 없으므로 하루 한도에는 넣지 않는다
                    if not dry_run:
                        write_log(name, "already", "이미 언팔 상태였음")
                    print("%5s @%-24s  이미 언팔됨" % (seq, name))
                    processed += 1
                    fails = 0
                    continue

                verdict, reason = judge(name, head, cfg, phase)
                if verdict != "unfollow":
                    label = {"kept_study": "남김(공부용)",
                             "deferred_trainer": "보류(트레이너)",
                             "kept_big": "남김(큰계정)"}[verdict]
                    if not dry_run:
                        write_log(name, verdict, reason)
                    print("%5s @%-24s  %s %s" % (seq, name, label, reason))
                    processed += 1
                    fails = 0
                    if dry_run:
                        time.sleep(random.uniform(1.0, 2.0))
                    continue

                if dry_run:
                    print("%5s @%-24s  [판단만] 언팔 대상 (%s)" % (seq, name, reason))
                    processed += 1
                    did += 1
                    time.sleep(random.uniform(1.5, 3.0))
                    continue

                btn = find_following_button(page)
                if not btn:
                    write_log(name, "failed", "팔로잉 버튼 못 찾음")
                    print("%5s @%-24s  실패 (버튼 없음)" % (seq, name))
                    fails += 1
                    processed += 1
                    if fails >= cfg["연속실패_한도"]:
                        print("\n[중단] 연속 실패 %d회. 화면 구조가 바뀌었을 수 있습니다." % fails)
                        break
                    continue

                btn.click()
                if not click_unfollow_in_dialog(page):
                    close_dialog(page)
                    write_log(name, "failed", "팔로우 취소 항목 못 찾음")
                    print("%5s @%-24s  실패 (메뉴 없음)" % (seq, name))
                    fails += 1
                    processed += 1
                    if fails >= cfg["연속실패_한도"]:
                        print("\n[중단] 연속 실패 %d회." % fails)
                        break
                    continue

                page.wait_for_timeout(2500)

                blocked = check_blocked(page, name)
                if blocked:
                    write_log(name, "failed", blocked)
                    print("\n[중단] %s" % blocked)
                    break

                write_log(name, "unfollowed", reason)
                print("%5s @%-24s  언팔 완료 (%s)" % (seq, name, reason))
                processed += 1
                did += 1
                hour_count += 1
                fails = 0

                time.sleep(random.uniform(cfg["대기_최소"], cfg["대기_최대"]))

            except PWTimeout:
                write_log(name, "failed", "페이지 시간 초과")
                print("%5s @%-24s  실패 (시간 초과)" % (seq, name))
                fails += 1
                processed += 1
                if fails >= cfg["연속실패_한도"]:
                    print("\n[중단] 연속 실패 %d회." % fails)
                    break
            except KeyboardInterrupt:
                print("\n[중단] 사용자가 중지했습니다.")
                break
            except Exception as e:
                write_log(name, "failed", "오류: %s" % str(e)[:120])
                print("%5s @%-24s  실패 (%s)" % (seq, name, str(e)[:60]))
                fails += 1
                processed += 1
                if fails >= cfg["연속실패_한도"]:
                    print("\n[중단] 연속 실패 %d회." % fails)
                    break

        ctx.close()

    print("")
    print("=" * 52)
    print("  이번 실행: %d명 언팔 (프로필 %d개 확인)" % (did, processed))
    print("  진행 현황은 'python queue.py stat'으로 확인하세요.")
    print("=" * 52)
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--dry-run", action="store_true", help="실제로 누르지 않고 판단만")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행 최대 인원")
    ap.add_argument("--phase", type=int, default=1, choices=[1, 2],
                    help="1=일반 계정 정리(기본), 2=보류했던 트레이너 정리")
    ap.add_argument("--login", action="store_true",
                    help="브라우저만 띄워서 로그인 세션을 저장하고 끝냄 (처음 한 번)")
    ap.add_argument("--confirm-phase2", action="store_true",
                    help="2단계 실제 실행에 반드시 필요한 잠금 해제 옵션")
    a = ap.parse_args()
    warn_if_cloud_folder()
    if a.login:
        return do_login()

    # 2단계는 보류해 둔 트레이너를 실제로 지우는 되돌리기 어려운 작업이다.
    # 실수로 눌리는 일이 없도록 별도 옵션 없이는 절대 돌지 않게 한다.
    if a.phase >= 2 and not a.dry_run and not a.confirm_phase2:
        print("=" * 56)
        print("  2단계는 잠겨 있습니다")
        print("=" * 56)
        print("  2단계는 1단계에서 '보류'해 둔 트레이너 계정을 실제로 언팔합니다.")
        print("  실수 방지를 위해 잠금 옵션을 붙여야만 실행됩니다.")
        print("")
        print("  먼저 무엇이 지워질지 확인하세요:")
        print("      python unfollow_bot.py --phase 2 --dry-run")
        print("")
        print("  확인 후 정말 실행하려면:")
        print("      python unfollow_bot.py --phase 2 --confirm-phase2")
        print("=" * 56)
        return 1

    return run(a.dry_run, a.limit, a.phase)


if __name__ == "__main__":
    sys.exit(safe_file.run_main(main))
