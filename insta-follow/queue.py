# -*- coding: utf-8 -*-
"""
언팔 작업 대기열 관리

- 작업목록.csv : 언팔 대상 전체 (오래된 순). 내보내기 ZIP에서 한 번 만든다.
- 진행상황.csv : 처리한 계정 기록. 여러 날에 걸쳐 이어가기 위한 로그.

사용법:
    python queue.py build          작업목록 만들기 (ZIP 필요)
    python queue.py next 10        다음에 처리할 10명 보기
    python queue.py done <아이디> <결과> [메모]
    python queue.py stat           진척 현황
"""

import sys
import os
import csv
import glob
import datetime
import safe_file

if hasattr(sys.stdout, "reconfigure"):
    # 콘솔이 쓰는 인코딩을 그대로 두고, 표현 못 하는 글자만 ? 로 바꾼다.
    # utf-8로 강제하면 한국어 Windows cmd 창(CP949)에서 한글이 깨진다.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "작업목록.csv")
LOG = os.path.join(HERE, "진행상황.csv")

# 결과 코드
# unfollowed : 언팔 완료
# skipped    : 전문/브랜드 계정으로 보여 건너뜀 (회원님 확인 필요)
# kept       : 회원님이 유지하기로 함
# failed     : 화면 문제 등으로 실패
# notfound   : 계정 없음/비활성화


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build():
    import check
    zips = sorted(glob.glob(os.path.join(HERE, "*.zip")))
    if not zips:
        raise SystemExit("[!] ZIP이 없습니다.")

    files, _kind = check.load_sources(zips[0])
    following = check.to_map(
        [u for _p, d in check.pick(files, "following") for u in check.extract_users(d)])
    followers = check.to_map(
        [u for _p, d in check.pick(files, "followers") for u in check.extract_users(d)])

    targets = [following[k] for k in (set(following) - set(followers))]
    # 오래된 순 = 내가 먼저 팔로우한 순
    targets.sort(key=lambda r: r[2] or 0)

    with safe_file.open_write(QUEUE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["순번", "아이디", "프로필 주소", "내가 팔로우한 날"])
        for i, (name, href, ts) in enumerate(targets, 1):
            w.writerow([i, name, check.profile_url(name, href), check.fmt_time(ts)])

    print("[+] 작업목록 %d명 저장 (오래된 순)" % len(targets))
    print("    %s" % QUEUE)


def load_done():
    """아이디(소문자) -> 결과"""
    return {r["아이디"].lower(): r["결과"] for r in read_csv(LOG)}


def get_start():
    """시작지점.txt에 적힌 순번부터 작업한다. 없으면 1번부터."""
    p = os.path.join(HERE, "시작지점.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    return int(line)
    return 1


def set_start(n):
    with open(os.path.join(HERE, "시작지점.txt"), "w", encoding="utf-8") as f:
        f.write("%d\n" % n)
    print("시작 지점을 %d번으로 설정했습니다." % n)


def next_n(n):
    q = read_csv(QUEUE)
    if not q:
        raise SystemExit("[!] 작업목록이 없습니다. 먼저 'python queue.py build'.")
    done = load_done()
    start = get_start()
    todo = [r for r in q
            if int(r["순번"]) >= start and r["아이디"].lower() not in done]

    print("시작 %d번 기준 · 남은 대상 %d명 / 전체 %d명" % (start, len(todo), len(q)))
    print("")
    for r in todo[:n]:
        print("%s\t%s\t%s" % (r["순번"], r["아이디"], r["내가 팔로우한 날"]))


def done(name, result, memo=""):
    exists = os.path.exists(LOG)
    with safe_file.open_write(LOG, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["시각", "아이디", "결과", "메모"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), name, result, memo])
    print("기록: %s -> %s %s" % (name, result, memo))


def stat():
    q = read_csv(QUEUE)
    log = read_csv(LOG)
    counts = {}
    for r in log:
        counts[r["결과"]] = counts.get(r["결과"], 0) + 1

    label = {"unfollowed": "언팔 완료", "skipped": "건너뜀(확인 필요)",
             "kept": "유지하기로 함", "failed": "실패", "notfound": "계정 없음",
             "kept_study": "남김 · 공부용", "kept_big": "남김 · 큰계정",
             "deferred_trainer": "보류 · 트레이너(2단계)",
             "already": "이미 언팔 상태였음"}

    # 아이디별 마지막 결과만 센다 (2단계에서 다시 처리된 계정 중복 방지)
    latest = {}
    for r in log:
        latest[r["아이디"].lower()] = r["결과"]
    counts = {}
    for v in latest.values():
        counts[v] = counts.get(v, 0) + 1

    print("=" * 44)
    print("  전체 대상   : %6d 명" % len(q))
    print("  처리함      : %6d 명" % len(latest))
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print("     %-22s %5d" % (label.get(k, k), v))
    remain = len(q) - len(latest)
    print("  남음        : %6d 명" % remain)

    deferred = counts.get("deferred_trainer", 0)
    if remain > 0:
        print("-" * 44)
        print("  1단계 남음: %d명 · 하루 150명 기준 약 %d일"
              % (remain, (remain + 149) // 150))
    elif deferred:
        print("-" * 44)
        print("  1단계 완료. 보류된 트레이너 %d명이 남아 있습니다." % deferred)
        print("  2단계로 넘어가려면: python unfollow_bot.py --phase 2")
    print("=" * 44)

    today = datetime.date.today().strftime("%Y-%m-%d")
    n_today = sum(1 for r in log if r["시각"].startswith(today))
    print("  오늘 처리: %d 명" % n_today)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    try:
        import unfollow_bot
        unfollow_bot.warn_if_cloud_folder()
    except Exception:
        pass
    if cmd == "build":
        build()
    elif cmd == "next":
        next_n(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    elif cmd == "start":
        set_start(int(sys.argv[2]))
    elif cmd == "done":
        done(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "stat":
        stat()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(safe_file.run_main(main))
