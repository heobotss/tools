# -*- coding: utf-8 -*-
r"""영문 이름 진입점 — 배치 파일이 한글 파일명을 못 넘기는 문제를 우회한다.

왜 있나: 2026-09-09 10:00 스케줄러가 _task.bat 을 불렀는데 0초 만에 끝나고
자동운행로그에 아무것도 안 남았다. bat 안의 한글(자동운행.py·--시험)을 cmd 가 못 읽은 것이다.
그래서 bat 은 영문만 두고, 한글 파일명·한글 플래그는 여기서 붙인다.

다계정 (2026-09-22): 트레이너 한 사람이 블로그 아이디를 두세 개 돌릴 수 있다.
계정마다 자리(계정\<아이디>\)가 따로 있고 — 로그인·기록·명단·지문이 전부 갈린다 —
이 파일이 계정을 **차례로** 부른다. 동시에 돌리지 않는다: 같은 인터넷 주소에서 여러 계정이
동시에 신청하면 계정이 묶여 보인다. 사이에 10~30분을 쉰다.

  python run_auto.py            → 자동운행.py            (스케줄러: 흔들림 대기 포함, 계정 1개)
  python run_auto.py --now      → 계정마다 자동운행.py --지금 (사이에 쉼)
  python run_auto.py --test     → 계정마다 --시험 (제출·메일 없음, 쉼 없음)
  python run_auto.py --login    → 계정마다 로그인.py
  python run_auto.py --ping     → 한 줄만 찍고 끝 (배선 확인용)
  python run_auto.py --install  → 처음 설치 (부품 설치 + 이름·아이디 등록)
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ME_FILE = HERE / "내정보.json"     # 이름·아이디 — zip 에 없는 파일이라 새 버전을 덮어써도 남는다
FLAGS = {"--now": "--지금", "--test": "--시험"}
NEED = ["공용.py", "자동운행.py", "후보뽑기.py", "신청실행.py", "신청정리.py", "로그인.py", "설정.json"]
GAP_MIN, GAP_MAX = 10, 30          # 계정 사이 쉬는 시간(분)
MAX_ACCOUNTS = 3


def stamp(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def files_ok():
    missing = [n for n in NEED if not (HERE / n).exists()]
    if missing:
        print()
        print("[!] 프로그램 파일 이름이 깨졌거나 빠졌습니다: " + ", ".join(missing))
        print("    압축을 풀 때 글자가 깨진 경우가 많습니다. 폴더를 지우고")
        print("    zip 파일에 마우스 오른쪽 → 「반디집」이나 「알집」으로 다시 풀어 주세요.")
        return False
    return True


def load_cfg():
    cfg = json.loads((HERE / "설정.json").read_text(encoding="utf-8"))
    if ME_FILE.exists():
        me = json.loads(ME_FILE.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in me.items() if k != "계정목록"})
        cfg["계정목록"] = me.get("계정목록", [])
    return cfg


def save_me(me):
    ME_FILE.write_text(json.dumps(me, ensure_ascii=False, indent=2), encoding="utf-8")


def accounts(cfg):
    """[{계정ID: ...}] — 다계정이면 등록한 순서, 센터판이면 설정의 계정ID 하나."""
    lst = [a for a in (cfg.get("계정목록") or []) if a.get("계정ID")]
    if lst:
        return lst
    return [{"계정ID": cfg["계정ID"]}] if cfg.get("계정ID") else []


def env_for(accs, i):
    env = dict(os.environ)
    if not ME_FILE.exists():
        return env                                   # 센터판 — 예전처럼 프로그램 폴더를 그대로 쓴다
    a = accs[i]
    env["NR_DATA"] = str(HERE / "계정" / a["계정ID"])
    env["NR_ACCOUNT_ID"] = a["계정ID"]
    env["NR_LOGIN_ID"] = a.get("로그인ID") or a["계정ID"]
    env["NR_ACCOUNT_INDEX"] = str(i)
    env["NR_ACCOUNT_COUNT"] = str(len(accs))
    return env


def call(script, args, env):
    return subprocess.call([sys.executable, "-X", "utf8", "-u", str(HERE / script), *args],
                           cwd=str(HERE), env=env)


def rest(minutes):
    print(f"\n  다음 계정까지 {minutes}분 쉽니다. 창을 닫지 마세요. (한 번에 몰아 보내지 않으려는 쉼입니다)", flush=True)
    for left in range(minutes, 0, -5):
        time.sleep(min(5, left) * 60)
        if left > 5:
            print(f"    … {left - 5}분 남음", flush=True)


# ──────────────────────────── 처음 설치 ────────────────────────────

class InputClosed(Exception):
    pass


def ask(prompt):
    try:
        return input(prompt).lstrip("﻿")
    except EOFError:
        raise InputClosed


def ensure_playwright():
    try:
        import playwright  # noqa: F401
        print("[✓] 부품(playwright) 이미 설치됨")
        return True
    except ImportError:
        pass
    print("[i] 부품(playwright)을 설치합니다. 1~3분 걸립니다 …", flush=True)
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "--user",
                          "--disable-pip-version-check", "playwright"])
    if rc != 0:
        print("[!] 부품 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 눌러 주세요.")
        return False
    print("[✓] 부품 설치 완료")
    return True


def ask_one_id(order, blog_info, taken):
    while True:
        raw = ask(f"  {order}번째 블로그 아이디: ").strip().strip("/")
        bid = re.sub(r"[^A-Za-z0-9_\-]", "", raw.split("/")[-1])
        if not bid:
            continue
        if bid in taken:
            print("  이미 등록한 아이디입니다. 다른 아이디를 넣어 주세요.")
            continue
        try:
            info = blog_info(bid)
        except Exception:
            print("  그 아이디로 블로그를 찾지 못했습니다. 다시 입력해 주세요.")
            continue
        print(f"  → 「{info['블로그명'] or bid}」 ({info['별명']}) 블로그를 찾았습니다.")
        if ask("  맞으면 Enter, 틀리면 n 을 치고 Enter: ").strip().lower() == "n":
            continue
        # 네이버는 로그인 아이디의 - 와 . 을 블로그 주소에서 _ 로 바꿔 보여준다 (2026-10-09 실측).
        # 로그인할 때 치는 아이디가 주소와 다를 수 있어 따로 묻는다.
        print(f"  네이버에 로그인할 때 치는 아이디가 「{bid}」 와 다른가요?")
        lg = re.sub(r"[^A-Za-z0-9_.\-]", "", ask("  같으면 그냥 Enter, 다르면 로그인 아이디를 치고 Enter: ").strip())
        return {"계정ID": bid, "로그인ID": lg or bid}


def ask_identity(cfg):
    me = {}
    names = cfg.get("배정이름", [])
    if names:
        print()
        print("=" * 50)
        print("  본인 이름 번호를 누르고 Enter 를 치세요.")
        for i, n in enumerate(names, 1):
            print(f"    {i}. {n}")
        print("=" * 50)
        while True:
            v = re.sub(r"\D", "", ask("  번호: "))
            if v and 1 <= int(v) <= len(names):
                me["배정번호"] = int(v) - 1
                me["트레이너"] = names[int(v) - 1]
                break
            print(f"  1부터 {len(names)} 사이 숫자를 눌러 주세요.")
    print()
    print(f"  돌릴 블로그가 몇 개인가요? (1~{MAX_ACCOUNTS}, 보통 1)")
    print("  아이디를 여러 개 넣으면 하루에 차례로 하나씩 돕니다.")
    while True:
        v = re.sub(r"\D", "", ask("  개수: "))
        if v and 1 <= int(v) <= MAX_ACCOUNTS:
            n_acc = int(v)
            break
        print(f"  1부터 {MAX_ACCOUNTS} 사이 숫자를 눌러 주세요.")
    print()
    print("  블로그 아이디를 넣어 주세요.")
    print("  (블로그 주소 blog.naver.com/ 뒤에 붙은 영어 글자입니다. 비밀번호는 묻지 않습니다)")
    sys.path.insert(0, str(HERE))
    from 공용 import blog_info
    accs = []
    for k in range(n_acc):
        accs.append(ask_one_id(k + 1, blog_info, [a["계정ID"] for a in accs]))
    me["계정목록"] = accs                      # [{계정ID, 로그인ID}]
    return me


def install():
    print("[이웃모집기 처음 설치]")
    if not files_ok():
        return 1
    print(f"[✓] 파이썬 {sys.version.split()[0]}")
    if not ensure_playwright():
        return 1
    cfg = load_cfg()
    if not accounts(cfg) or (cfg.get("배정이름") and cfg.get("배정번호") is None):
        try:
            me = ask_identity(cfg)
        except InputClosed:
            print("\n[!] 입력이 끊겼습니다. 「1_처음설치」를 다시 더블클릭해 주세요.")
            return 1
        save_me(me)
        cfg = load_cfg()
    else:
        print("[✓] 이미 등록됨 — 바꾸려면 폴더의 「내정보.json」을 지우고 다시 누르세요")
    accs = accounts(cfg)
    for a in accs:
        (HERE / "계정" / a["계정ID"]).mkdir(parents=True, exist_ok=True)
    print()
    print(f"[✓] 설치 끝 — {cfg.get('트레이너', '')} / 블로그 " + " · ".join(a["계정ID"] for a in accs))
    print("    다음은 「2_로그인」을 더블클릭하세요." +
          (" 블로그 수만큼 로그인 창이 차례로 뜹니다." if len(accs) > 1 else ""))
    return 0


# ──────────────────────────── 매일 실행 ────────────────────────────

def ready_or_explain():
    if not files_ok():
        return None
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("[!] 아직 설치가 안 됐습니다. 「1_처음설치」를 먼저 더블클릭하세요.")
        return None
    cfg = load_cfg()
    accs = accounts(cfg)
    if not accs or (cfg.get("배정이름") and cfg.get("배정번호") is None):
        print("[!] 이름·블로그 아이디 등록이 안 됐습니다. 「1_처음설치」를 먼저 더블클릭하세요.")
        return None
    return accs


def main():
    args = sys.argv[1:]
    if "--ping" in args:
        stamp(f"ping OK  python={sys.executable}  cwd={Path.cwd()}")
        return 0
    if "--install" in args:
        return install()
    accs = ready_or_explain()
    if accs is None:
        return 1

    login = "--login" in args
    test = "--test" in args
    if test:
        print("자동운행 배선 점검 - 제출도 메일도 없이 경로만 돕니다. 브라우저가 잠깐 떴다 사라집니다.", flush=True)
    passthru = [FLAGS.get(a, a) for a in args if a not in ("--login",)]

    rc = 0
    for i, a in enumerate(accs):
        env = env_for(accs, i)
        if len(accs) > 1:
            print()
            print("=" * 50)
            print(f"  [{i + 1}/{len(accs)}] 블로그 {a['계정ID']}")
            print("=" * 50, flush=True)
        if login:
            stamp(f"로그인.py 실행 — {a['계정ID']}")
            rc |= call("로그인.py", [], env)
        else:
            stamp("launch 자동운행.py " + (" ".join(passthru) or "(scheduled)"))
            rc |= call("자동운행.py", passthru, env)
        if i + 1 < len(accs) and not login and not test:
            rest(random.randint(GAP_MIN, GAP_MAX))
    if test:
        print("\n끝. 위에 [!] 줄이 없으면 정상입니다.", flush=True)
    elif len(accs) > 1 and not login:
        print("\n블로그 " + " · ".join(a["계정ID"] for a in accs) + " 모두 끝났습니다. 창을 닫으셔도 됩니다.", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
