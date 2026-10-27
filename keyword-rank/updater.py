# -*- coding: utf-8 -*-
"""허봇스 배포 프로그램 공용 자동 업데이트 (모든 배포 프로그램에 같은 파일을 쓴다).

시작.bat 이 메뉴를 띄우기 전에 부른다. 인터넷에서 최신 버전 목록(manifest.json)을 보고,
바뀐 파일만 받아서 「내용 확인(해시) → 파이썬 문법 확인 → 옛 파일 백업 → 교체」 순서로 바꾼다.
무엇이든 잘못되면 아무것도 바꾸지 않고 지금 버전으로 실행한다 — 업데이트 때문에 안 켜지는 일은 없다.

    python updater.py              확인하고 새 버전이면 바꾼다 (시작.bat 이 부름)
    python updater.py --install    처음 설치용: 실행 중이 아닐 때라 .bat 도 바로 바꾼다
    python updater.py --check      확인만 (아무것도 안 바꿈)

설정은 같은 폴더의 _update.json: {"app": "프로그램 폴더 이름", "repo": "heobotss/tools", "branch": "main"}
사용자 파일(설정·기록·로그인)은 manifest 에 아예 없으므로 절대 건드리지 않는다.
외부 부품을 쓰지 않는다 (파이썬 기본 기능만).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "_update.json")
VER = os.path.join(HERE, "_version.txt")
TIMEOUT = 8


def say(msg):
    try:
        print(msg)
    except Exception:
        pass


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def fetch(url):
    # 캐시를 피하려고 시각을 붙인다 (raw.githubusercontent 는 몇 분 캐시한다)
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(url + sep + "t=%d" % time.time(),
                                 headers={"User-Agent": "heobotss-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def local_version():
    try:
        with open(VER, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def main(argv):
    install = "--install" in argv
    check_only = "--check" in argv
    # 대표님 PC 의 원본·배포판 폴더(_publish.json 이 있는 곳)에서는 절대 스스로 바꾸지 않는다 —
    # 고치는 중인 파일을 인터넷의 옛 버전이 덮어쓰면 안 된다. _publish.json 은 배포 목록에 없다.
    if os.path.exists(os.path.join(HERE, "_publish.json")):
        return 0
    try:
        with open(CONF, encoding="utf-8") as f:
            conf = json.load(f)
        base = "https://raw.githubusercontent.com/%s/%s/%s/" % (
            conf.get("repo", "heobotss/tools"), conf.get("branch", "main"), conf["app"])
        base = os.environ.get("HEOBOTSS_UPDATE_BASE") or base  # 시험용 — 평소엔 비어 있다
    except Exception:
        return 0  # 자동 업데이트 설정이 없는 옛 설치본 — 조용히 넘어간다

    try:
        manifest = json.loads(fetch(base + "manifest.json").decode("utf-8"))
        remote = manifest["version"]
        files = manifest["files"]
    except Exception:
        say("[업데이트] 인터넷에서 확인하지 못해 지금 버전으로 실행합니다.")
        return 0

    mine = local_version()
    if remote == mine and not install:
        return 0  # 최신 — 조용히 메뉴로

    # 바꿀 파일 고르기: 내 파일과 내용(해시)이 다른 것만
    todo = []
    for rel, want in files.items():
        if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            say("[업데이트] 목록에 이상한 경로가 있어 업데이트를 건너뜁니다.")
            return 0
        p = os.path.join(HERE, rel)
        if not os.path.exists(p) or sha256_file(p) != want:
            todo.append((rel, want))

    if check_only:
        say("[업데이트] 새 버전 %s (지금 %s) — 바뀔 파일 %d개" % (remote, mine or "모름", len(todo)))
        return 0

    say("[업데이트] 새 버전을 받는 중입니다 (%s)..." % remote)
    tmp = tempfile.mkdtemp(prefix="heobotss_upd_")
    try:
        # 1) 전부 받아서 확인부터 — 하나라도 이상하면 아무것도 안 바꾼다
        staged = []
        for rel, want in todo:
            data = fetch(base + urllib.request.quote(rel.replace("\\", "/")))
            if sha256_bytes(data) != want:
                say("[업데이트] 받은 파일이 목록과 달라 업데이트를 건너뜁니다: %s" % rel)
                return 0
            if rel.endswith(".py"):
                try:
                    compile(data, rel, "exec")
                except SyntaxError:
                    say("[업데이트] 받은 파일에 문제가 있어 업데이트를 건너뜁니다: %s" % rel)
                    return 0
            sp = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(sp), exist_ok=True)
            with open(sp, "wb") as f:
                f.write(data)
            staged.append(rel)

        # 2) 옛 파일 백업
        bk = os.path.join(HERE, "_이전버전", (mine or "처음") + "_" + time.strftime("%Y%m%d_%H%M%S"))
        for rel in staged:
            p = os.path.join(HERE, rel)
            if os.path.exists(p):
                os.makedirs(os.path.dirname(os.path.join(bk, rel)), exist_ok=True)
                shutil.copy2(p, os.path.join(bk, rel))

        # 3) 교체 — 실행 중인 .bat 은 바로 못 바꾸니 .new 로 두고 시작.bat 이 다시 켜며 바꾼다
        for rel in staged:
            src = os.path.join(tmp, rel)
            dst = os.path.join(HERE, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if rel.lower().endswith(".bat") and not install:
                shutil.copyfile(src, dst + ".new")
            else:
                os.replace(src, dst) if os.path.splitdrive(src)[0] == os.path.splitdrive(dst)[0] \
                    else shutil.copyfile(src, dst)

        with open(VER, "w", encoding="utf-8") as f:
            f.write(remote)
        say("[업데이트] 새 버전으로 바꿨습니다 (%s → %s)" % (mine or "처음", remote))
        notes = manifest.get("notes")
        if notes:
            say("           바뀐 점: %s" % notes)
        return 0
    except Exception as e:
        say("[업데이트] 도중에 문제가 생겨 지금 버전으로 실행합니다. (%s)" % str(e)[:80])
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
