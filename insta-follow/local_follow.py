# -*- coding: utf-8 -*-
"""
원주 잠재고객 팔로우 — 원주 가게 계정의 「최근 팔로워」를 후보로 모아, 진짜 지역 사람만 팔로우한다.
3주가 지나도 맞팔하지 않은 사람은 조금씩 팔로우를 취소한다.

센터 계정(비브레)에서 10월부터 돌리며 다듬은 방식을 트레이너 계정용으로 옮긴 것이다 (2026-10-28).

    python local_follow.py              오늘 몫: 어제 팔로우 확인 → (부족하면) 후보 모으기 → 팔로우 → 3주 미맞팔 정리
    python local_follow.py --dry-run    판단만 (아무것도 누르지 않음)
    python local_follow.py --stat       현황

지키는 것
  - 이미 팔로우 중이거나, 나를 이미 팔로우하는 사람, 예전에 언팔한 사람은 건드리지 않는다
  - 가짜·빈 계정, 원주에 매장이 없는 온라인·전국 업체, 다른 트레이너·헬스장 계정은 팔로우하지 않는다
  - 원주 가게 사장님·직원 계정은 팔로우한다 (사람이고 PT 핵심 연령대 — 2026-10-13 대표님 결정)
  - 인스타가 막으면(차단 메시지·보안 확인·로그아웃·눌러도 반영 안 됨·다음 날 팔로우 증발) 바로 멈춘다
  - 후보 모으기는 가게 1곳에서 1페이지(최대 50명)만, 하루 한 번만 (많이 긁으면 인스타가 「정보 수집」으로 본다)
"""

import sys
import os
import re
import csv
import json
import time
import random
import argparse
import datetime
import collections
import safe_file

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "\n" + "=" * 56 + "\n"
        "  아직 설치가 안 된 상태입니다\n"
        + "=" * 56 + "\n"
        "  이 폴더의 '설치.bat' 을 두 번 클릭해서 설치를 먼저 해주세요.\n"
        + "=" * 56)

import unfollow_bot as U
import follow_bot as B
import rest_guard

HERE = U.HERE
IG = "https://www.instagram.com"
APP_ID = "936619743392459"
CAND = os.path.join(HERE, "지역후보.csv")
LOGF = os.path.join(HERE, "지역기록.csv")
MUTUAL = os.path.join(HERE, "지역맞팔.csv")
STATE = os.path.join(HERE, "_지역상태.json")

CAND_FIELDS = ["username", "pk", "출처", "상태", "메모", "수집일"]
LOG_FIELDS = ["일시", "username", "pk", "행동", "메모"]
MUTUAL_FIELDS = ["확인일", "username", "pk"]

# 원주 가게 계정 — 팔로워가 곧 원주 사람 명단이다. 센터 계정 실측 맞팔률 순 (2026-10-13).
# 설정.txt 에 [지역소스] 칸을 만들어 적으면 그쪽을 쓴다.
DEFAULT_SOURCES = ["momo_wonju", "kajawonju", "woodstock_wonju", "on_wonju",
                   "mmakjan_wonju", "wonjucoffee"]

ONLINE_BIO = ("전국배송", "전국 배송", "전국택배", "전국 택배", "무료배송", "해외배송",
              "스마트스토어", "smartstore", "온라인몰", "쇼핑몰", "쿠팡", "coupang", "공식몰",
              "구매는 링크", "구매링크", "공동구매", "공구 오픈", "본사", "가맹문의", "가맹 문의")
LOCAL_WORDS = ("원주", "wonju", "횡성", "제천", "영월", "춘천", "문막", "혁신도시", "기업도시")
BIZ_NAME = re.compile(
    r"(_official|official_|\.official|shop|store|cafe|coffee|studio|salon|beauty|nail|hair|"
    r"clinic|hospital|academy|pilates|yoga|gym|fitness|crossfit|bakery|kitchen|realty|"
    r"interior|flower|wedding|tattoo|waxing|market|company|corp|_wonju|wonju_|^wj_|_wj$)")
BIZ_BIO = ("영업시간", "예약문의", "예약 문의", "문의 dm", "문의dm", "주문", "택배", "휴무",
           "매장", "지점", "대표번호", "오시는 길", "오시는길", "상담문의", "상담 문의", "가맹")


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))


# ---------------------------------------------------------------- 기록

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    tmp = path + ".tmp"
    with safe_file.open_write(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def append_csv(path, row, fields):
    new = not os.path.exists(path)
    with safe_file.open_write(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception:
        pass


def today_by(action):
    today = datetime.date.today().strftime("%Y-%m-%d")
    return sum(1 for r in read_csv(LOGF)
               if (r.get("일시") or "").startswith(today) and r.get("행동") == action)


def set_status(name, status, memo=""):
    rows = read_csv(CAND)
    for r in rows:
        if r["username"] == name:
            r["상태"] = status
            if memo:
                r["메모"] = memo
    write_csv(CAND, rows, CAND_FIELDS)


def protected(name, cfg):
    n = (name or "").lower()
    return (not n) or n.startswith("vivre") or n == "heobotss" or n in cfg["제외계정"]


# ---------------------------------------------------------------- 인스타

def cookies(ctx):
    out = {}
    try:
        for c in ctx.cookies(IG):
            out[c.get("name")] = c.get("value")
    except Exception:
        pass
    return out


def api_get(page, ctx, url):
    """인스타 페이지 안에서 fetch 로 읽는다 (읽기 전용). (status, json 또는 글)"""
    ck = cookies(ctx)
    js = """
    async ([url, csrf, appid]) => {
      const r = await fetch(url, {credentials: 'include',
        headers: {'x-ig-app-id': appid, 'x-csrftoken': csrf,
                  'x-requested-with': 'XMLHttpRequest', 'x-asbd-id': '129477'}});
      return [r.status, await r.text()];
    }"""
    status, text = page.evaluate(js, [url, ck.get("csrftoken", ""), APP_ID])
    try:
        return status, json.loads(text)
    except Exception:
        return status, text


def page_pk(page):
    """지금 열린 프로필 페이지의 계정 번호 (2026-10-28 실측: profilePage_<번호>)."""
    try:
        html = page.content()
    except Exception:
        return ""
    m = re.search(r'"page_id":"profilePage_(\d+)"', html) or re.search(r'"profile_id":"(\d+)"', html)
    return m.group(1) if m else ""


def header_button(page):
    """프로필 맨 위 첫 버튼 글자 (팔로우 / 맞팔로우 / 팔로잉 / 요청됨 …)."""
    try:
        return (page.locator("header button").first.inner_text(timeout=6000) or "").strip()
    except Exception:
        return ""


FOLLOWING_WORDS = ("팔로잉", "Following", "요청됨", "Requested")


def parse_counts(head):
    """헤더 글 → (게시물, 팔로워, 팔로잉). 못 읽으면 None."""
    def grab(*keys):
        for k in keys:
            m = re.search(r"%s[ \t]*([\d.,]+)[ \t]*(만|천|[KkMm])?" % k, head)
            if m:
                v = float(m.group(1).replace(",", ""))
                unit = (m.group(2) or "").lower()
                return int(v * {"만": 10000, "천": 1000, "k": 1000, "m": 1000000}.get(unit, 1))
        return None
    p = grab("게시물")
    f = U.parse_followers(head)
    g = grab("팔로우", "팔로잉")
    if p is None or f is None:
        return None
    return p, f, (g or 0)


def judge_local(name, head, cfg):
    """(통과?, 사유, 업체/개인)."""
    counts = parse_counts(head)
    if not counts:
        return False, "숫자를 못 읽음", ""
    posts, followers, following = counts
    bio = U.bio_text(head).lower()
    if posts < 1:
        return False, "게시물 없음", ""
    if followers < 10 and following >= 500:
        return False, "가짜로 보임 (팔로워 %d · 팔로잉 %d)" % (followers, following), ""
    if posts <= 2 and following >= 300 and not bio.strip():
        return False, "빈 계정으로 보임", ""
    if followers >= cfg["큰계정_기준"]:
        return False, "큰 계정 (팔로워 %d)" % followers, ""
    # 다른 트레이너·헬스장·필라테스 계정은 고객이 아니다
    for w in cfg["트레이너단어"]:
        if U.hit(w, bio):
            return False, "트레이너·운동시설 계정 ('%s')" % w, ""
    if not any(w in bio for w in LOCAL_WORDS) and any(k in bio for k in ONLINE_BIO):
        return False, "온라인 업체 (원주 매장 없음)", ""
    kind = "업체" if (BIZ_NAME.search(name.lower()) or any(k in bio for k in BIZ_BIO)) else "개인"
    return True, "게시물 %d · 팔로워 %d" % (posts, followers), kind


# ---------------------------------------------------------------- 제외 명단

def zip_sets():
    """인스타 내보내기 ZIP 이 있으면 (내 팔로잉, 내 팔로워)."""
    import glob
    zips = sorted(glob.glob(os.path.join(HERE, "*.zip")))
    zips = [z for z in zips if "인스타-팔로우관리" not in os.path.basename(z)]
    if not zips:
        return set(), set()
    try:
        import check
        files, _k = check.load_sources(zips[0])
        fo = check.to_map([u for _p, d in check.pick(files, "following") for u in check.extract_users(d)])
        fr = check.to_map([u for _p, d in check.pick(files, "followers") for u in check.extract_users(d)])
        return set(fo), set(fr)
    except Exception as e:
        print("[!] 내보내기 ZIP 을 못 읽었습니다: %s" % str(e)[:80])
        return set(), set()


def known_names():
    """후보로 넣지 않을 아이디 — 이미 팔로우·언팔·시도했던 사람, 이미 나를 팔로우하는 사람."""
    out = set(B.blocklist())
    _fo, fr = zip_sets()
    out |= fr
    out |= {r["username"] for r in read_csv(CAND)}
    out |= {r["username"] for r in read_csv(LOGF)}
    return out


# ---------------------------------------------------------------- 1. 어제 팔로우 확인

def verify_yesterday(page, cfg):
    """지난 팔로우 날의 팔로우가 실제로 남아 있는지 표본으로 본다. 사라졌으면 False.

    센터 계정 실측(2026-10-13): 인스타는 화면엔 「팔로잉」을 보여 주고 실제로는 팔로우를 지운다.
    이 「증발」이 계정 경고보다 12일 먼저 나타났다 — 가장 이른 경보다.
    """
    st = read_state()
    checked_days = st.get("확인한날", [])
    today = datetime.date.today().isoformat()
    by_day = collections.defaultdict(list)
    for r in read_csv(LOGF):
        if r.get("행동") == "팔로우":
            d = (r.get("일시") or "")[:10]
            if d and d < today:
                by_day[d].append(r["username"])
    days = sorted(d for d in by_day if d not in checked_days)
    if not days:
        return True
    day = days[-1]
    sample = random.sample(by_day[day], min(6, len(by_day[day])))
    stay = gone = 0
    log("어제(%s) 팔로우가 실제로 남아 있는지 %d명 확인" % (day, len(sample)))
    for name in sample:
        U.open_profile(page, name)
        blocked = U.check_blocked(page, name)
        if blocked:
            rest_guard.record_refusal(blocked)
            return False
        txt = header_button(page)
        if any(w in txt for w in FOLLOWING_WORDS):
            stay += 1
            state = "남음"
        elif txt in ("팔로우", "Follow", "맞팔로우", "Follow Back"):
            gone += 1
            state = "사라짐"
        else:
            state = "모름"
        print("    @%-24s %s" % (name, state))
        time.sleep(random.uniform(6, 15))
    st.setdefault("확인한날", []).append(day)
    st["확인한날"] = st["확인한날"][-30:]
    st.setdefault("확인기록", []).append({"확인일": today, "대상일": day, "남음": stay, "사라짐": gone})
    st["확인기록"] = st["확인기록"][-60:]
    save_state(st)
    checked = stay + gone
    log("확인 결과: 남음 %d · 사라짐 %d" % (stay, gone))
    if checked >= 4 and gone / float(checked) >= 0.34:
        rest_guard.record_refusal("어제 누른 팔로우가 %d/%d명 사라짐 (인스타가 조용히 막는 중)" % (gone, checked))
        return False
    return True


# ---------------------------------------------------------------- 2. 후보 모으기 (소량)

def collect_small(page, ctx, cfg):
    """재고가 모자라면 가게 1곳의 최근 팔로워 1페이지(최대 50명)만 가져온다. 하루 한 번."""
    stock = sum(1 for r in read_csv(CAND) if r["상태"] == "대기")
    st = read_state()
    today = datetime.date.today().isoformat()
    if stock >= cfg["지역_후보_최소재고"]:
        return stock
    if st.get("마지막수집일") == today:
        log("오늘은 이미 후보를 모았습니다 (재고 %d명)" % stock)
        return stock
    sources = cfg["지역소스"] or DEFAULT_SOURCES
    if "다음소스" not in st:
        # 같은 센터 선생님들이 같은 날 같은 가게에서 모으지 않게 계정마다 출발점을 다르게
        try:
            st["다음소스"] = int(cookies(ctx).get("ds_user_id") or 0) % len(sources)
        except ValueError:
            st["다음소스"] = 0
    idx = int(st["다음소스"]) % len(sources)
    src = sources[idx]
    st["마지막수집일"] = today
    st["다음소스"] = idx + 1
    save_state(st)

    log("후보 모으기: @%s 의 최근 팔로워 (최대 50명)" % src)
    U.open_profile(page, src)
    blocked = U.check_blocked(page, src)
    if blocked:
        rest_guard.record_refusal(blocked)
        return -1
    pk = page_pk(page)
    if not pk:
        log("  @%s 계정 번호를 못 읽어 오늘은 건너뜁니다." % src)
        return stock
    status, data = api_get(page, ctx, "%s/api/v1/friendships/%s/followers/?count=50" % (IG, pk))
    if status == 429:
        rest_guard.record_refusal("후보 모으기에서 인스타가 「요청이 너무 많다」(429)고 함")
        return -1
    if status != 200 or not isinstance(data, dict) or "users" not in data:
        log("  응답 이상(HTTP %s) - 오늘은 건너뜁니다." % status)
        return stock
    known = known_names()
    got = 0
    for u in data["users"]:
        name = (u.get("username") or "").lower()
        if not name or name in known or protected(name, cfg):
            continue
        known.add(name)
        append_csv(CAND, {"username": name, "pk": str(u.get("pk") or u.get("id") or ""),
                          "출처": src, "상태": "대기", "메모": "", "수집일": today}, CAND_FIELDS)
        got += 1
    log("  새 후보 %d명 (재고 %d명)" % (got, stock + got))
    return stock + got


# ---------------------------------------------------------------- 3. 팔로우

def do_follow(page, ctx, cfg, dry_run, limit):
    room = cfg["지역_팔로우_하루"] - today_by("팔로우")
    if limit is not None:
        room = min(room, limit)
    if room <= 0:
        log("오늘 원주 고객 팔로우 %d명을 이미 했습니다 (한도 %d)." % (today_by("팔로우"), cfg["지역_팔로우_하루"]))
        return True
    waiting = [r for r in read_csv(CAND) if r["상태"] == "대기"]
    if not waiting:
        log("팔로우할 후보가 없습니다. 내일 다시 모읍니다.")
        return True
    log("팔로우 시작: 후보 %d명 중 오늘 %d명까지" % (len(waiting), room))
    n = fails = ignored = 0
    for r in waiting:
        if n >= room:
            break
        name, pk = r["username"], r["pk"]
        if protected(name, cfg):
            if not dry_run:
                set_status(name, "건너뜀", "보호계정")
            continue
        try:
            U.open_profile(page, name)
        except PWTimeout:
            print("  @%-24s 시간 초과 (다음에 다시)" % name)
            fails += 1
            if fails >= cfg["연속실패_한도"]:
                log("연속 %d번 실패 - 오늘은 멈춥니다." % fails)
                return False
            continue
        if not U.is_logged_in(ctx):
            rest_guard.record_refusal("로그인이 풀림 (인스타가 로그아웃시킴)")
            return False
        blocked = U.check_blocked(page, name)
        if blocked:
            rest_guard.record_refusal(blocked)
            return False
        st, head = U.read_profile(page)
        if st == "notfound":
            if not dry_run:
                set_status(name, "건너뜀", "계정 없음")
            continue
        if st == "unread":
            print("  @%-24s 못 읽음 (다음에 다시)" % name)
            fails += 1
            if fails >= cfg["연속실패_한도"]:
                log("프로필을 연속 %d번 못 읽었습니다 - 오늘은 멈춥니다." % fails)
                return False
            continue
        fails = 0
        btn_txt = header_button(page)
        if any(w in btn_txt for w in FOLLOWING_WORDS):
            if not dry_run:
                set_status(name, "건너뜀", "이미 팔로우 중")
            continue
        if btn_txt in ("맞팔로우", "Follow Back"):
            if not dry_run:
                set_status(name, "건너뜀", "이미 나를 팔로우함")
            print("  @%-24s 건너뜀 (이미 나를 팔로우)" % name)
            continue
        ok, why, kind = judge_local(name, head, cfg)
        if not ok:
            if not dry_run:
                set_status(name, "건너뜀", why)
            print("  @%-24s 건너뜀 (%s)" % (name, why))
            time.sleep(random.uniform(2, 4))
            continue
        if dry_run:
            n += 1
            print("  @%-24s [판단만] 팔로우 대상 (%s · %s · %s)" % (name, r["출처"], kind, why))
            time.sleep(random.uniform(2, 4))
            continue
        btn = B.find_follow_button(page)
        if not btn:
            set_status(name, "건너뜀", "팔로우 버튼 없음: %s" % btn_txt[:20])
            continue
        btn.click()
        page.wait_for_timeout(2500)
        blocked = U.check_blocked(page, name)
        if blocked:
            rest_guard.record_refusal(blocked)
            return False
        if not B.already_following(page):
            page.wait_for_timeout(2000)
        if not B.already_following(page):
            set_status(name, "실패", "눌렀는데 반영 안 됨")
            print("  @%-24s 실패 (눌렀는데 그대로)" % name)
            ignored += 1
            if ignored >= 2:
                rest_guard.record_refusal("팔로우를 눌러도 반영이 안 됨 %d번 (조용한 차단)" % ignored)
                return False
            continue
        ignored = 0
        n += 1
        set_status(name, "팔로우")
        append_csv(LOGF, {"일시": now(), "username": name, "pk": pk, "행동": "팔로우",
                          "메모": "%s·%s" % (r["출처"], kind)}, LOG_FIELDS)
        print("  [%2d/%d] @%-24s 팔로우 (%s · %s)" % (n, room, name, r["출처"], kind))
        s = random.uniform(cfg["지역_대기_최소"], cfg["지역_대기_최대"])
        print("    ...다음 계정까지 %.0f초" % s)
        time.sleep(s)
    log("원주 고객 팔로우 %d명%s" % (n, " (판단만)" if dry_run else ""))
    return True


# ---------------------------------------------------------------- 4. 3주 미맞팔 정리

def friendship(page, ctx, pk):
    status, data = api_get(page, ctx, "%s/api/v1/friendships/show/%s/" % (IG, pk))
    if status == 429:
        return "429"
    if status == 200 and isinstance(data, dict) and "followed_by" in str(data):
        d = data.get("friendship_status") or data
        return {"followed_by": bool(d.get("followed_by")), "following": bool(d.get("following")),
                "outgoing_request": bool(d.get("outgoing_request"))}
    return None


def do_clean(page, ctx, cfg, dry_run, limit):
    room = cfg["지역_언팔_하루"] - today_by("언팔")
    if limit is not None:
        room = min(room, limit)
    if room <= 0:
        return True
    cutoff = (datetime.date.today() - datetime.timedelta(days=cfg["지역_유예_일수"])).strftime("%Y-%m-%d")
    mutual = {r["username"] for r in read_csv(MUTUAL)}
    rows = read_csv(LOGF)
    undone = {r["username"] for r in rows if r.get("행동") == "언팔"}
    first = {}
    for r in rows:
        if r.get("행동") == "팔로우":
            first.setdefault(r["username"], (r["일시"], r["pk"]))
    due = [(u, v[1]) for u, v in first.items()
           if v[0][:10] <= cutoff and u not in mutual and u not in undone and not protected(u, cfg)]
    if not due:
        log("정리할 계정이 없습니다 (팔로우 후 %d일 지난 미맞팔 없음)." % cfg["지역_유예_일수"])
        return True
    log("3주 미맞팔 정리: 대상 %d명 중 오늘 %d명까지" % (len(due), room))
    n = fails = ignored = 0
    for name, pk in due:
        if n >= room:
            break
        fs = friendship(page, ctx, pk) if pk else None
        time.sleep(random.uniform(2, 4))
        if fs == "429":
            rest_guard.record_refusal("맞팔 확인에서 인스타가 「요청이 너무 많다」(429)고 함")
            return False
        if fs is None:
            fails += 1
            if fails >= cfg["연속실패_한도"]:
                log("맞팔 확인이 연속 %d번 안 됩니다 - 정리는 다음에." % fails)
                return True
            continue
        fails = 0
        if fs["followed_by"]:
            if not dry_run:
                append_csv(MUTUAL, {"확인일": datetime.date.today().isoformat(),
                                    "username": name, "pk": pk}, MUTUAL_FIELDS)
            print("  @%-24s 맞팔 - 남김" % name)
            continue
        if not fs["following"] and not fs["outgoing_request"]:
            if not dry_run:
                append_csv(LOGF, {"일시": now(), "username": name, "pk": pk,
                                  "행동": "정리", "메모": "이미 팔로우 아님"}, LOG_FIELDS)
            continue
        if dry_run:
            n += 1
            print("  @%-24s [판단만] 언팔 대상 (미맞팔 %d일)" % (name, cfg["지역_유예_일수"]))
            continue
        U.open_profile(page, name)
        blocked = U.check_blocked(page, name)
        if blocked:
            rest_guard.record_refusal(blocked)
            return False
        btn = U.find_following_button(page)
        if not btn:
            print("  @%-24s 실패 (팔로잉 버튼 없음)" % name)
            continue
        btn.click()
        if not U.click_unfollow_in_dialog(page):
            U.close_dialog(page)
            print("  @%-24s 실패 (팔로우 취소 메뉴 없음)" % name)
            continue
        page.wait_for_timeout(2500)
        if not U.already_unfollowed(page):
            page.wait_for_timeout(2000)
        if not U.already_unfollowed(page):
            print("  @%-24s 실패 (눌렀는데 그대로)" % name)
            ignored += 1
            if ignored >= 2:
                rest_guard.record_refusal("언팔을 눌러도 반영이 안 됨 %d번 (조용한 차단)" % ignored)
                return False
            continue
        ignored = 0
        n += 1
        append_csv(LOGF, {"일시": now(), "username": name, "pk": pk, "행동": "언팔",
                          "메모": "미맞팔 %d일" % cfg["지역_유예_일수"]}, LOG_FIELDS)
        print("  [%2d/%d] @%-24s 언팔 (미맞팔)" % (n, room, name))
        time.sleep(random.uniform(cfg["지역_대기_최소"], cfg["지역_대기_최대"]))
    log("미맞팔 정리 %d명%s" % (n, " (판단만)" if dry_run else ""))
    return True


# ---------------------------------------------------------------- 현황·본체

def do_stat(cfg):
    cand = read_csv(CAND)
    rows = read_csv(LOGF)
    followed = {r["username"] for r in rows if r.get("행동") == "팔로우"}
    unf = sum(1 for r in rows if r.get("행동") == "언팔")
    mutual = len(read_csv(MUTUAL))
    st = read_state()
    print("=" * 52)
    print("  원주 잠재고객 팔로우 - 현황")
    print("=" * 52)
    print("  후보 재고(대기) : %d명 / 모은 후보 %d명"
          % (sum(1 for r in cand if r["상태"] == "대기"), len(cand)))
    print("  지금까지 팔로우 : %d명 (그중 미맞팔 언팔 %d · 맞팔 확인 %d)" % (len(followed), unf, mutual))
    print("  오늘 팔로우     : %d / 한도 %d" % (today_by("팔로우"), cfg["지역_팔로우_하루"]))
    print("  오늘 언팔       : %d / 한도 %d" % (today_by("언팔"), cfg["지역_언팔_하루"]))
    rec = (st.get("확인기록") or [])[-3:]
    for x in rec:
        print("  팔로우 남음 확인: %s분 - 남음 %d · 사라짐 %d" % (x["대상일"], x["남음"], x["사라짐"]))
    print("  후보를 모으는 가게 : %s" % ", ".join(cfg["지역소스"] or DEFAULT_SOURCES))
    end = rest_guard.rest_until()
    if end:
        print("  [쉬는 기간] %s 까지" % end)
    print("=" * 52)
    return 0


def run(cfg, dry_run, limit):
    print("=" * 56)
    print("  원주 잠재고객 팔로우 - %s" % ("판단만 (실제로 안 누름)" if dry_run else "오늘 몫 실행"))
    print("  오늘 팔로우 %d / %d · 오늘 언팔 %d / %d"
          % (today_by("팔로우"), cfg["지역_팔로우_하루"], today_by("언팔"), cfg["지역_언팔_하루"]))
    rest_guard.show_today_total()
    print("=" * 56)
    print("")
    if not dry_run:
        safe_file.check_writable(CAND, LOGF, MUTUAL)
        if not rest_guard.ask_if_resting():
            print("[i] 쉬는 기간이라 오늘은 하지 않습니다.")
            return 0

    with sync_playwright() as pw:
        ctx = U.launch_ctx(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(IG + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            if not U.is_logged_in(ctx):
                if not U.wait_for_login(ctx, page):
                    return 1
            if not dry_run and not verify_yesterday(page, cfg):
                log("오늘은 여기서 멈춥니다.")
                return 0
            if collect_small(page, ctx, cfg) < 0:
                return 0
            if not do_follow(page, ctx, cfg, dry_run, limit):
                return 0
            do_clean(page, ctx, cfg, dry_run, limit)
        except KeyboardInterrupt:
            print("\n[중단] 사용자가 중지했습니다.")
        except PWTimeout:
            print("\n[중단] 인스타 페이지가 응답하지 않습니다. 인터넷을 확인하고 나중에 다시 해 주세요.")
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="실제로 누르지 않고 판단만")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행 최대 인원")
    ap.add_argument("--stat", action="store_true", help="현황 보기")
    a = ap.parse_args()
    cfg = U.load_config()
    U.warn_if_cloud_folder()
    if a.stat:
        return do_stat(cfg)
    return run(cfg, a.dry_run, a.limit)


if __name__ == "__main__":
    sys.exit(safe_file.run_main(main))
