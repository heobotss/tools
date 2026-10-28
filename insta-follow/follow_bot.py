# -*- coding: utf-8 -*-
"""
트레이너 계정 팔로우 프로그램

이미 트레이너로 확인된 계정(씨앗)의 「비슷한 계정」 추천에서 후보를 모으고,
프로필을 열어 트레이너인지 확인한 뒤 팔로우합니다. (2026-10-28 해시태그 → 비슷한 계정)

    python follow_bot.py --collect              후보 모으기 (팔로우는 안 함)
    python follow_bot.py --dry-run --limit 20   판단만
    python follow_bot.py                        실제 팔로우
    python follow_bot.py --stat                 현황

!! 언팔 프로그램과 절대 동시에 돌리지 마세요.
   인스타는 팔로우와 언팔을 같은 행동 예산으로 봅니다.

절대 지키는 것:
  - 이미 팔로우 중인 계정은 건드리지 않음
  - 언팔했던 계정은 다시 팔로우하지 않음 (진행상황.csv 대조)
  - 트레이너로 확인되지 않으면 팔로우하지 않음
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
import safe_file

if hasattr(sys.stdout, "reconfigure"):
    # 콘솔이 쓰는 인코딩을 그대로 두고, 표현 못 하는 글자만 ? 로 바꾼다.
    # utf-8로 강제하면 한국어 Windows cmd 창(CP949)에서 한글이 깨진다.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

# playwright가 없으면 개발자용 오류 화면 대신 설치 안내를 보여준다.
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit(
        "\n" + "=" * 56 + "\n"
        "  아직 설치가 안 된 상태입니다\n"
        + "=" * 56 + "\n"
        "  프로그램에 필요한 부품(playwright)이 없습니다.\n"
        "  이 폴더의 '설치.bat' 을 두 번 클릭해서 설치를 먼저\n"
        "  해주세요. 처음 한 번만 하면 됩니다. (5~10분)\n"
        + "=" * 56)

import unfollow_bot as U

HERE = U.HERE
CAND = os.path.join(HERE, "팔로우후보.csv")
FLOG = os.path.join(HERE, "팔로우기록.csv")


# ---------------------------------------------------------------- 기록

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def flog_write(name, result, memo=""):
    exists = os.path.exists(FLOG)
    with safe_file.open_write(FLOG, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["시각", "아이디", "결과", "메모"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), name, result, memo])


def follow_today_count():
    """오늘 '실제로 팔로우한' 건수. 건너뜀·실패는 넣지 않는다."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    return sum(1 for r in read_csv(FLOG)
               if r["시각"].startswith(today) and r["결과"] == "followed")


def blocklist(retry_limit=3):
    """절대 팔로우하면 안 되는 아이디 집합.

    - 이미 팔로우 중이던 사람 (내보내기 following 전체)
    - 언팔 작업으로 건드린 사람 (되팔로우 금지)
    - 이미 팔로우 시도한 사람 (단, 「실패」만 있는 사람은 retry_limit 번까지 다시 본다)
    """
    out = set()

    for r in U.read_log():                       # 진행상황.csv
        out.add(r["아이디"].lower())
    fails = {}
    for r in read_csv(FLOG):                     # 팔로우기록.csv
        k = r["아이디"].lower()
        if r["결과"] == "failed":
            fails[k] = fails.get(k, 0) + 1
        else:
            out.add(k)
    out |= {k for k, n in fails.items() if n >= retry_limit}
    for r in read_csv(os.path.join(HERE, "작업목록.csv")):
        out.add(r["아이디"].lower())             # 언팔 대기열도 제외

    # 내보내기 ZIP에 있던 팔로잉 전체 (이미 팔로우 중이던 사람)
    import glob
    zips = sorted(glob.glob(os.path.join(HERE, "*.zip")))
    if zips:
        try:
            import check
            files, _k = check.load_sources(zips[0])
            fo = check.to_map([u for _p, d in check.pick(files, "following")
                               for u in check.extract_users(d)])
            out |= set(fo)
        except Exception as e:
            print("[!] 기존 팔로잉 목록을 못 읽었습니다: %s" % str(e)[:80])
            print("    되팔로우 방지가 약해집니다. ZIP 파일을 확인하세요.")
    return out


# ---------------------------------------------------------------- 후보 수집

def collect_from_tag(page, tag, want, seen):
    """해시태그 페이지에서 게시물을 넘겨가며 작성자 아이디를 모은다."""
    got = []
    try:
        page.goto("https://www.instagram.com/explore/tags/%s/" % tag,
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
    except Exception:
        print("    [!] #%s 페이지를 열지 못했습니다." % tag)
        return got

    # 첫 게시물 열기
    post = None
    for sel in ('main a[href*="/p/"]', 'a[href*="/p/"]', 'a[href*="/reel/"]'):
        try:
            post = page.query_selector(sel)
            if post:
                break
        except Exception:
            pass
    if not post:
        print("    [!] #%s 에서 게시물을 찾지 못했습니다." % tag)
        return got

    try:
        post.click()
        page.wait_for_timeout(2500)
    except Exception:
        return got

    misses = 0
    for _ in range(want * 3):
        if len(got) >= want or misses >= 8:
            break
        user = None
        for sel in ('div[role="dialog"] header a[href^="/"]',
                    'article header a[href^="/"]',
                    'div[role="dialog"] a[href^="/"]'):
            try:
                el = page.query_selector(sel)
                if el:
                    href = (el.get_attribute("href") or "").strip("/")
                    if href and "/" not in href and href not in ("p", "reel", "explore"):
                        user = href
                        break
            except Exception:
                pass

        if user:
            low = user.lower()
            if low not in seen:
                seen.add(low)
                got.append(user)
                misses = 0
            else:
                misses += 1
        else:
            misses += 1

        try:
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(random.uniform(900, 1600))
        except Exception:
            break

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass
    return got


# ---------------------------------------------------------------- 「비슷한 계정」으로 모으기
# 2026-10-28 개편: 해시태그(#헬스장 등)는 운동하는 일반인·강의 계정이 대부분이라
# 「트레이너 아님」으로 줄줄이 건너뛰었다. 이미 트레이너로 확인된 계정(씨앗)의 프로필에서
# 인스타가 보여 주는 「비슷한 계정」 추천을 받으면 대부분 트레이너다
# (실측: 필라테스 강사 프로필 → 여수PT·광안리PT·종각PT 트레이너·필라테스 강사 10명).
# 사람이 버튼을 누르는 것과 똑같은 방식이고, 씨앗 하나당 프로필 1번만 연다.

SEED_STATE = os.path.join(HERE, "_씨앗상태.json")
SEED_REUSE_DAYS = 30

# 씨앗이 하나도 없을 때(처음 설치)만 쓰는 출발점 — 2026-10-28 「비슷한 계정」으로 확인한 공개 트레이너 계정
STARTER_SEEDS = ["re.move_pilates", "0je_coach", "v_jerry_jerry", "movement_haus_.sun", "b_kk_m"]


class Refused(Exception):
    """인스타가 막았다 (차단 메시지·보안 확인·로그아웃)."""


def _seed_state():
    try:
        with open(SEED_STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _seed_used(name):
    st = _seed_state()
    st[name.lower()] = datetime.date.today().isoformat()
    try:
        tmp = SEED_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SEED_STATE)
    except Exception:
        pass


def pick_seeds(cfg):
    """씨앗 순서: 설정 [씨앗계정] → 최근 내가 팔로우한 트레이너 → 언팔 때 보류한 트레이너 → 출발점.

    30일 안에 이미 쓴 씨앗은 뺀다 (같은 추천을 또 받지 않게).
    """
    order = list(cfg.get("씨앗계정") or [])
    for r in reversed(read_csv(FLOG)):
        if r["결과"] == "followed":
            order.append(r["아이디"].lower())
    for r in U.read_log():
        if r["결과"] == "deferred_trainer":
            order.append(r["아이디"].lower())
    order += STARTER_SEEDS
    st = _seed_state()
    today = datetime.date.today()
    out, seen = [], set()
    for s in order:
        s = s.lower().lstrip("@")
        if not s or s in seen:
            continue
        seen.add(s)
        used = st.get(s)
        if used:
            try:
                if (today - datetime.date.fromisoformat(used)).days < SEED_REUSE_DAYS:
                    continue
            except ValueError:
                pass
        out.append(s)
    return out


_CARD_END = ("팔로우", "Follow", "팔로잉", "Following", "요청됨", "Requested", "맞팔로우", "Follow Back")
_USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def parse_suggestions(text, links, seed):
    """「회원님을 위한 추천」 글에서 (아이디, 이름줄) 목록을 뽑는다.

    화면 글 모양: 아이디 / 이름(직함) / 팔로우 가 한 카드다. 링크 목록에 있는 아이디만 믿는다.
    """
    links = {l.lower() for l in links} - {seed.lower()}
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    for head in ("회원님을 위한 추천", "Suggested for you"):
        if head in lines:
            lines = lines[lines.index(head) + 1:]
            break
    out, card = [], []
    for l in lines:
        if l in ("모두 보기", "See all", "더 보기"):
            continue
        if l in _CARD_END:
            if card and card[0].lower() in links:
                out.append((card[0], " ".join(card[1:])[:80]))
            card = []
            continue
        card.append(l)
        if len(card) > 4:      # 카드 모양이 아니면 끝 (게시물 탭 등)
            break
    # 카드 모양을 하나도 못 읽었으면(화면 구조가 바뀜) 링크 아이디만이라도 쓴다.
    # 카드를 읽었을 때는 쓰지 않는다 — 헤더의 「○○님이 팔로우합니다」 링크가 섞일 수 있다.
    if not out:
        out = [(l, "") for l in sorted(links)]
    return out


_READ_SUGGEST_JS = """
() => {
  const root = document.querySelector('div[role="dialog"]') || document.querySelector('main');
  if (!root) return {text: '', links: []};
  const links = [...root.querySelectorAll('a[href^="/"]')]
    .map(a => a.getAttribute('href'))
    .filter(h => /^\\/[A-Za-z0-9._]+\\/$/.test(h))
    .map(h => h.slice(1, -1));
  return {text: root.innerText.slice(0, 6000), links: [...new Set(links)]};
}
"""


def collect_similar(page, seed, cfg):
    """씨앗 프로필에서 「비슷한 계정」을 펼쳐 [(아이디, 이름줄)] 을 돌려준다. 못 하면 []."""
    U.open_profile(page, seed)
    blocked = U.check_blocked(page, seed)
    if blocked:
        raise Refused(blocked)
    st, _head = U.read_profile(page)
    if st != "ok":
        return []

    btn = None
    for sel in ('header svg[aria-label="비슷한 계정"]', 'header svg[aria-label*="비슷한"]',
                'header svg[aria-label*="Similar"]', 'header svg[aria-label*="Suggested"]'):
        try:
            btn = page.query_selector(sel)
            if btn:
                break
        except Exception:
            pass
    if not btn:
        return []
    try:
        btn.click()
    except Exception:
        return []

    data = {"text": "", "links": []}
    for _ in range(8):
        page.wait_for_timeout(800)
        data = page.evaluate(_READ_SUGGEST_JS)
        if "회원님을 위한 추천" in data["text"] or "Suggested for you" in data["text"]:
            break
    found = parse_suggestions(data["text"], data["links"], seed)

    # 「모두 보기」가 있으면 펼쳐서 더 받는다 (프로필을 더 열지 않고 한 번에 30명 안팎)
    try:
        more = page.query_selector('main a:has-text("모두 보기"), main div[role="button"]:has-text("모두 보기"), '
                                   'main a:has-text("See all")')
        if more and more.is_visible():
            more.click()
            page.wait_for_timeout(2500)
            # 대화상자로 열릴 때만 쓴다 — 다른 화면으로 넘어가면 이 씨앗과 무관한 추천일 수 있다
            dlg = page.evaluate(_READ_SUGGEST_JS) if page.query_selector('div[role="dialog"]') else {"links": []}
            if dlg["links"]:
                have = {u.lower() for u, _n in found}
                for u, n in parse_suggestions(dlg["text"], dlg["links"], seed):
                    if u.lower() not in have:
                        found.append((u, n))
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
    except Exception:
        pass
    return found


def name_hint(name_line, cfg):
    """추천 카드의 이름줄(예: 「윤선아 트레이너 [종각PT]」)에 트레이너 단어가 있는가."""
    low = (name_line or "").lower()
    return any(U.hit(w, low) for w in cfg["트레이너단어"])


def collect_run(page, cfg, want_total):
    """이미 열려 있는 page로 후보를 모아 CSV에 추가하고, 모은 수를 반환한다.

    인스타가 막으면 Refused 를 그대로 올려 보낸다 (부른 쪽이 멈춘다).
    """
    block = blocklist(cfg["재시도_한도"])
    existing = {r["아이디"].lower() for r in read_csv(CAND)}
    seen = set(block) | set(existing)
    seeds = [s for s in pick_seeds(cfg)]

    print("  「비슷한 계정」으로 %d명 목표 · 쓸 수 있는 씨앗(트레이너) %d개 · 제외 대상 %d명"
          % (want_total, len(seeds), len(seen)))
    print("")

    found = []
    used = 0
    for seed in seeds:
        if len(found) >= want_total or used >= cfg["씨앗_한번에"]:
            break
        print("  @%s 의 비슷한 계정 보는 중..." % seed)
        try:
            got = collect_similar(page, seed, cfg)
        except PWTimeout:
            got = []
        _seed_used(seed)
        used += 1
        new = 0
        for u, n in got:
            low = u.lower()
            if low in seen or low.startswith("vivre") or low == "heobotss":
                continue
            seen.add(low)
            found.append((u, "비슷:%s" % seed, n))
            new += 1
        hint = sum(1 for _u, _s, n in found[-new:] if new and name_hint(n, cfg))
        print("    -> 새 후보 %d명 (이름에 트레이너 표시 %d명 · 누적 %d명)" % (new, hint, len(found)))
        time.sleep(random.uniform(4, 9))

    if not found and cfg["해시태그"] and not seeds:
        # 씨앗이 정말 하나도 없을 때만 예전 방식(해시태그)으로 물러선다
        print("  [i] 씨앗이 없어 해시태그로 모읍니다 (정확도가 낮습니다).")
        per_tag = max(10, want_total // len(cfg["해시태그"]) + 5)
        for tag in cfg["해시태그"]:
            if len(found) >= want_total:
                break
            for u in collect_from_tag(page, tag, per_tag, seen):
                found.append((u, tag, ""))
            time.sleep(random.uniform(3, 7))

    if found:
        exists = os.path.exists(CAND)
        with safe_file.open_write(CAND, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["아이디", "출처태그", "수집시각", "이름"])
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            for u, t, n in found:
                w.writerow([u, t, now, n])

    print("")
    print("  [+] 후보 %d명을 새로 모았습니다 (씨앗 %d개 사용)." % (len(found), used))
    return len(found)


def do_collect(cfg):
    """후보만 모으고 끝낸다 (팔로우는 안 함)."""
    print("=" * 52)
    print("  후보 수집만 진행합니다 (팔로우 안 함)")
    print("=" * 52)
    print("")

    with sync_playwright() as pw:
        ctx = U.launch_ctx(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if not U.is_logged_in(ctx):
            if not U.wait_for_login(ctx, page):
                ctx.close()
                return 1
        try:
            collect_run(page, cfg, cfg["수집_목표"])
        except Refused as e:
            import rest_guard
            rest_guard.record_refusal(str(e))
        ctx.close()
    return 0


# ---------------------------------------------------------------- 팔로우

def is_trainer(header_text, cfg):
    """소개글 기준으로 트레이너인지 본다. 아이디 줄과 버튼·하이라이트 이름은 뺀다."""
    low = U.bio_text(header_text).lower()
    for w in cfg["트레이너단어"]:
        if U.hit(w, low):
            return True, w
    return False, None


def find_follow_button(page):
    for sel in ('header button:has-text("팔로우")',
                'main button:has-text("팔로우")',
                'header button:has-text("Follow")',
                'main button:has-text("Follow")'):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                txt = (btn.inner_text() or "").strip()
                if txt in ("팔로우", "Follow"):
                    return btn
        except Exception:
            pass
    return None


def already_following(page):
    for sel in ('header button:has-text("팔로잉")', 'header button:has-text("요청됨")',
                'main button:has-text("팔로잉")', 'main button:has-text("요청됨")'):
        try:
            b = page.query_selector(sel)
            if b and b.is_visible():
                return True
        except Exception:
            pass
    return False


def remaining_candidates(cfg=None):
    """남은 후보. 순서: 이름에 트레이너 표시가 있는 추천 → 다른 추천 → 예전 해시태그 후보."""
    block = blocklist(cfg["재시도_한도"] if cfg else 3)
    rows = [r for r in read_csv(CAND) if r["아이디"].lower() not in block]
    if not cfg:
        return rows

    def rank(r):
        n = r.get("이름") or ((r.get(None) or [""])[0])
        if (r.get("출처태그") or "").startswith("비슷:"):
            return 0 if name_hint(n, cfg) else 1
        return 2
    return sorted(rows, key=rank)


def data_ready():
    """되팔로우 방지에 쓸 '기존 팔로잉 목록'이 준비돼 있는지 본다.

    이게 없으면 이미 팔로우 중인 사람을 또 팔로우하거나,
    예전에 정리한 사람을 다시 팔로우하게 된다. 그래서 없으면 아예 진행하지 않는다.
    """
    import glob
    if not glob.glob(os.path.join(HERE, "*.zip")):
        return False, "인스타 내보내기 ZIP이 이 폴더에 없습니다"
    if not os.path.exists(os.path.join(HERE, "작업목록.csv")):
        return False, "작업목록.csv가 없습니다 (데이터를 아직 안 불러왔습니다)"
    return True, ""


def do_follow(cfg, dry_run, limit):
    ok, why = data_ready()
    if not ok:
        print("=" * 56)
        print("  먼저 내 데이터를 불러와야 합니다")
        print("=" * 56)
        print("  이유: %s" % why)
        print("")
        print("  내가 이미 팔로우 중인 사람 목록이 있어야")
        print("  같은 사람을 또 팔로우하거나, 정리했던 사람을 다시")
        print("  팔로우하는 일을 막을 수 있습니다.")
        print("")
        print("  메뉴 2번 (내 데이터 불러오기) 을 먼저 해주세요.")
        print("=" * 56)
        return 1

    done_today = follow_today_count()
    room = max(0, cfg["팔로우_하루_최대"] - done_today)
    if room == 0:
        print("[i] 오늘 팔로우 %d명을 이미 했습니다. 한도(%d) 도달로 종료합니다."
              % (done_today, cfg["팔로우_하루_최대"]))
        return 0

    goal = min(room, (limit if limit is not None else 10 ** 9))   # 목표는 '실제 팔로우 건수'
    todo = remaining_candidates(cfg)

    import rest_guard
    print("=" * 56)
    print("  오늘 팔로우 %d명 (한도 %d)" % (done_today, cfg["팔로우_하루_최대"]))
    print("  이번 목표 : %d명 팔로우" % goal)
    print("  쓸 수 있는 후보 : %d명" % len(todo))
    print("  모드      : %s" % ("판단만 (실제로 안 누름)" if dry_run else "실제 팔로우"))
    rest_guard.show_today_total()
    print("=" * 56)
    print("")
    if cfg["팔로우_하루_최대"] > 150:
        print("[참고] 하루 팔로우 한도가 %d 입니다. 계정마다 버티는 양이 달라 막지는 않습니다." % cfg["팔로우_하루_최대"])
        print("       인스타가 막으면 그 자리에서 멈추고 알려 드립니다.\n")

    # 기록 파일이 엑셀 등에 열려 있으면 브라우저를 띄우기 전에 알려 주고 멈춘다
    if not dry_run:
        safe_file.check_writable(FLOG, CAND)
        if not rest_guard.ask_if_resting():
            print("[i] 쉬는 기간이라 오늘은 하지 않습니다.")
            return 0

    processed = 0   # 방문한 계정 수
    did = 0         # 실제로 팔로우한 수
    fails = 0
    ignored = 0     # 눌렀는데 반영 안 된 횟수 (인스타가 조용히 막는 신호)

    with sync_playwright() as pw:
        ctx = U.launch_ctx(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if not U.is_logged_in(ctx):
            if not U.wait_for_login(ctx, page):
                ctx.close()
                return 1

        # 후보가 모자라면 여기서 알아서 모으고 이어서 진행한다.
        # 트레이너가 아닌 후보도 섞이므로 목표보다 넉넉히 모은다.
        need = goal * 2
        if len(todo) < need:
            print("[i] 후보가 부족해서 먼저 모읍니다. (%d명 -> %d명 목표)" % (len(todo), need))
            print("")
            try:
                collect_run(page, cfg, need - len(todo))
            except Refused as e:
                if not dry_run:
                    rest_guard.record_refusal(str(e))
                else:
                    print("\n[중단] %s" % e)
                ctx.close()
                return 0
            todo = remaining_candidates(cfg)
            print("[i] 이제 후보 %d명. 팔로우를 시작합니다." % len(todo))
            print("")

        if not todo:
            print("[!] 쓸 수 있는 후보가 없습니다.")
            print("    「비슷한 계정」을 볼 트레이너 씨앗을 다 썼습니다. 며칠 뒤 다시 하거나,")
            print("    설정.txt 에 [씨앗계정] 칸을 만들고 아는 트레이너 아이디를 적어 주세요.")
            ctx.close()
            return 0

        for row in todo:
            if did >= goal:
                break
            name = row["아이디"]
            tag = row.get("출처태그", "")
            try:
                U.open_profile(page, name)

                if not U.is_logged_in(ctx):
                    rest_guard.record_refusal("로그인이 풀림 (인스타가 로그아웃시킴)")
                    break

                blocked = U.check_blocked(page, name)
                if blocked:
                    rest_guard.record_refusal(blocked)
                    break

                st, head = U.read_profile(page)
                if st == "notfound":
                    if not dry_run:
                        flog_write(name, "notfound", "계정 없음")
                    print("  @%-24s 없음" % name)
                    processed += 1
                    continue
                if st == "unread":
                    # 덜 뜬 화면을 「트레이너 아님」으로 영구 기록하지 않는다 — 다음에 다시 본다
                    if not dry_run:
                        flog_write(name, "failed", "소개글 못 읽음 (다음에 다시)")
                    print("  @%-24s 못 읽음 (다음에 다시)" % name)
                    processed += 1
                    fails += 1
                    if fails >= cfg["연속실패_한도"]:
                        print("\n[중단] 프로필을 %d번 연속 못 읽었습니다. 인터넷이나 로그인을 확인하세요." % fails)
                        break
                    continue

                if already_following(page):
                    if not dry_run:
                        flog_write(name, "skipped", "이미 팔로우 중")
                    print("  @%-24s 이미 팔로우 중" % name)
                    processed += 1
                    continue

                ok, word = is_trainer(head, cfg)
                if not ok:
                    if not dry_run:
                        flog_write(name, "skipped", "트레이너 아님")
                    print("  @%-24s 건너뜀 (트레이너 아님)" % name)
                    processed += 1
                    continue

                n = U.parse_followers(head)
                if n is not None and n < cfg["최소_팔로워"]:
                    if not dry_run:
                        flog_write(name, "skipped", "팔로워 %d명 (너무 적음)" % n)
                    print("  @%-24s 건너뜀 (팔로워 %d)" % (name, n))
                    processed += 1
                    continue
                if n is not None and n >= cfg["큰계정_기준"]:
                    if not dry_run:
                        flog_write(name, "skipped", "팔로워 %d명 (큰 계정)" % n)
                    print("  @%-24s 건너뜀 (팔로워 %d, 큰 계정)" % (name, n))
                    processed += 1
                    continue

                if dry_run:
                    print("  @%-24s [판단만] 팔로우 대상 (%s · '%s' · 팔로워 %s)"
                          % (name, tag, word, n if n is not None else "?"))
                    processed += 1
                    did += 1
                    time.sleep(random.uniform(1.5, 3.0))
                    continue

                btn = find_follow_button(page)
                if not btn:
                    flog_write(name, "failed", "팔로우 버튼 못 찾음")
                    print("  @%-24s 실패 (버튼 없음)" % name)
                    fails += 1
                    processed += 1
                    if fails >= cfg["연속실패_한도"]:
                        print("\n[중단] 연속 실패 %d회." % fails)
                        break
                    continue

                btn.click()
                page.wait_for_timeout(2500)

                blocked = U.check_blocked(page, name)
                if blocked:
                    flog_write(name, "failed", blocked)
                    rest_guard.record_refusal(blocked)
                    break

                # 눌렀는데 「팔로잉/요청됨」으로 안 바뀌면 인스타가 조용히 거부한 것이다
                if not already_following(page):
                    page.wait_for_timeout(2000)
                if not already_following(page):
                    flog_write(name, "failed", "눌렀는데 반영 안 됨")
                    print("  @%-24s 실패 (눌렀는데 그대로)" % name)
                    ignored += 1
                    processed += 1
                    if ignored >= 2:
                        rest_guard.record_refusal("팔로우를 눌러도 반영이 안 됨 %d번 (조용한 차단)" % ignored)
                        break
                    continue
                ignored = 0

                flog_write(name, "followed", "%s · '%s' · 팔로워 %s"
                           % (tag, word, n if n is not None else "?"))
                print("  @%-24s 팔로우 완료 (%s · 팔로워 %s)"
                      % (name, tag, n if n is not None else "?"))
                processed += 1
                did += 1
                fails = 0

                time.sleep(random.uniform(cfg["팔로우_대기_최소"], cfg["팔로우_대기_최대"]))

            except KeyboardInterrupt:
                print("\n[중단] 사용자가 중지했습니다.")
                break
            except PWTimeout:
                if not dry_run:
                    flog_write(name, "failed", "시간 초과")
                print("  @%-24s 실패 (시간 초과)" % name)
                fails += 1
                processed += 1
                if fails >= cfg["연속실패_한도"]:
                    break
            except Exception as e:
                if not dry_run:
                    flog_write(name, "failed", str(e)[:120])
                print("  @%-24s 실패 (%s)" % (name, str(e)[:50]))
                fails += 1
                processed += 1
                if fails >= cfg["연속실패_한도"]:
                    break

        ctx.close()

    print("")
    print("=" * 52)
    print("  이번 실행: %d명 팔로우 (프로필 %d개 확인)" % (did, processed))
    print("=" * 52)
    return 0


def do_stat(cfg):
    cands = read_csv(CAND)
    log = read_csv(FLOG)
    counts = {}
    for r in log:
        counts[r["결과"]] = counts.get(r["결과"], 0) + 1
    label = {"followed": "팔로우 완료", "skipped": "건너뜀",
             "failed": "실패", "notfound": "계정 없음"}

    todo = remaining_candidates(cfg)
    sim = sum(1 for r in todo if (r.get("출처태그") or "").startswith("비슷:"))

    print("=" * 44)
    print("  모아둔 후보  : %5d 명" % len(cands))
    print("  처리함       : %5d 명" % len(log))
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print("     %-16s %5d" % (label.get(k, k), v))
    print("  남은 후보    : %5d 명 (비슷한 계정 추천 %d명)" % (len(todo), sim))
    print("  남은 씨앗    : %5d 개 (비슷한 계정을 볼 트레이너)" % len(pick_seeds(cfg)))
    print("-" * 44)
    print("  오늘 팔로우  : %5d 명 (한도 %d)"
          % (follow_today_count(), cfg["팔로우_하루_최대"]))
    print("=" * 44)
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--collect", action="store_true", help="해시태그에서 후보 모으기")
    ap.add_argument("--dry-run", action="store_true", help="실제로 누르지 않고 판단만")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행 최대 인원")
    ap.add_argument("--stat", action="store_true", help="현황 보기")
    a = ap.parse_args()

    cfg = U.load_config()
    U.warn_if_cloud_folder()
    if a.stat:
        return do_stat(cfg)
    if a.collect:
        return do_collect(cfg)
    return do_follow(cfg, a.dry_run, a.limit)


if __name__ == "__main__":
    sys.exit(safe_file.run_main(main))
