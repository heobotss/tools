# -*- coding: utf-8 -*-
"""
트레이너 계정 팔로우 프로그램

해시태그에서 후보를 모으고, 프로필을 열어 트레이너인지 확인한 뒤 팔로우합니다.

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


def blocklist():
    """절대 팔로우하면 안 되는 아이디 집합.

    - 이미 팔로우 중이던 사람 (내보내기 following 전체)
    - 언팔 작업으로 건드린 사람 (되팔로우 금지)
    - 이미 팔로우 시도한 사람
    """
    out = set()

    for r in U.read_log():                       # 진행상황.csv
        out.add(r["아이디"].lower())
    for r in read_csv(FLOG):                     # 팔로우기록.csv
        out.add(r["아이디"].lower())
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


def collect_run(page, cfg, want_total):
    """이미 열려 있는 page로 후보를 모아 CSV에 추가하고, 모은 수를 반환한다."""
    tags = cfg["해시태그"]
    if not tags:
        print("[!] 설정.txt의 [해시태그]가 비어 있습니다.")
        return 0

    block = blocklist()
    existing = {r["아이디"].lower() for r in read_csv(CAND)}
    seen = set(block) | set(existing)

    print("  해시태그 %d개에서 %d명 목표 · 제외 대상 %d명" % (len(tags), want_total, len(seen)))
    print("")

    per_tag = max(10, want_total // len(tags) + 5)
    found = []

    for tag in tags:
        if len(found) >= want_total:
            break
        print("  #%s 수집 중..." % tag)
        got = collect_from_tag(page, tag, per_tag, seen)
        for u in got:
            found.append((u, tag))
        print("    -> %d명 (누적 %d명)" % (len(got), len(found)))
        time.sleep(random.uniform(3, 7))

    if found:
        exists = os.path.exists(CAND)
        with safe_file.open_write(CAND, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["아이디", "출처태그", "수집시각"])
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            for u, t in found:
                w.writerow([u, t, now])

    print("")
    print("  [+] 후보 %d명을 새로 모았습니다." % len(found))
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
        collect_run(page, cfg, cfg["수집_목표"])
        ctx.close()
    return 0


# ---------------------------------------------------------------- 팔로우

def is_trainer(header_text, cfg):
    """소개글 기준으로 트레이너인지 본다. 첫 줄(아이디)은 제외."""
    lines = header_text.split("\n")
    body = "\n".join(lines[1:]) if len(lines) > 1 else header_text
    low = body.lower()
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


def remaining_candidates():
    block = blocklist()
    return [r for r in read_csv(CAND) if r["아이디"].lower() not in block]


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
    todo = remaining_candidates()

    print("=" * 52)
    print("  오늘 팔로우 %d명 (한도 %d)" % (done_today, cfg["팔로우_하루_최대"]))
    print("  이번 목표 : %d명 팔로우" % goal)
    print("  쓸 수 있는 후보 : %d명" % len(todo))
    print("  모드      : %s" % ("판단만 (실제로 안 누름)" if dry_run else "실제 팔로우"))
    print("=" * 52)
    print("")

    # 기록 파일이 엑셀 등에 열려 있으면 브라우저를 띄우기 전에 알려 주고 멈춘다
    if not dry_run:
        safe_file.check_writable(FLOG, CAND)

    processed = 0   # 방문한 계정 수
    did = 0         # 실제로 팔로우한 수
    fails = 0

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
        need = goal * 3
        if len(todo) < need:
            print("[i] 후보가 부족해서 먼저 모읍니다. (%d명 -> %d명 목표)" % (len(todo), need))
            print("")
            collect_run(page, cfg, need - len(todo))
            todo = remaining_candidates()
            print("[i] 이제 후보 %d명. 팔로우를 시작합니다." % len(todo))
            print("")

        if not todo:
            print("[!] 쓸 수 있는 후보가 없습니다.")
            print("    설정.txt의 [해시태그]를 늘리거나 바꿔 보세요.")
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
                    print("\n[중단] 로그인이 풀렸습니다.")
                    break

                blocked = U.check_blocked(page, name)
                if blocked:
                    print("\n[중단] %s" % blocked)
                    break

                head = U.header_text(page)
                if not head:
                    if not dry_run:
                        flog_write(name, "notfound", "계정 없음")
                    print("  @%-24s 없음" % name)
                    processed += 1
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
                    print("  @%-24s [판단만] 팔로우 대상 (#%s · '%s' · 팔로워 %s)"
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
                    print("\n[중단] %s" % blocked)
                    break

                flog_write(name, "followed", "#%s · '%s' · 팔로워 %s"
                           % (tag, word, n if n is not None else "?"))
                print("  @%-24s 팔로우 완료 (#%s · 팔로워 %s)"
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

    block = blocklist()
    todo = [r for r in cands if r["아이디"].lower() not in block]

    print("=" * 44)
    print("  모아둔 후보  : %5d 명" % len(cands))
    print("  처리함       : %5d 명" % len(log))
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print("     %-16s %5d" % (label.get(k, k), v))
    print("  남은 후보    : %5d 명" % len(todo))
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
