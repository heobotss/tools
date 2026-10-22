# -*- coding: utf-8 -*-
"""이웃모집기 공용 부품 — 설정·장부·네이버 모바일 API·브라우저.

다른 파일(상권분석·후보뽑기·신청실행·신청정리)이 전부 여기를 import 한다.
"""
import csv
import json
import os
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

# ──────────────────────────── 계정별 자리 (2026-09-22 다계정) ────────────────────────────
# 한 트레이너가 블로그 아이디를 두세 개 돌릴 수 있다. 계정마다 로그인·기록·명단이 완전히 갈려야 해서
# 「내 자리」(DATA)를 환경변수로 받는다. run_auto 가 계정마다 다른 값을 넣어 차례로 부른다.
#   NR_DATA          계정 자리 폴더 (없으면 프로그램 폴더 = 한 계정만 쓰는 센터판·옛 방식 그대로)
#   NR_ACCOUNT_ID    그 자리의 블로그 아이디 · NR_ACCOUNT_INDEX 몇 번째 계정인가 · NR_ACCOUNT_COUNT 몇 개인가
DATA = Path(os.environ.get("NR_DATA") or BASE)
REC = DATA / "기록"
SNAP = REC / "스냅"
DATA.mkdir(parents=True, exist_ok=True)
REC.mkdir(exist_ok=True)
SNAP.mkdir(exist_ok=True)

CFG_FILE = BASE / "설정.json"                 # 설정·창고·제외목록은 프로그램 폴더에 한 벌 (계정끼리 공유)
# 이웃그룹 이름은 계정마다 따로 (2026-10-22). 설정.json 에 써 넣으면 ① 계정끼리 섞이고
# ② 배포판을 새로 덮을 때 빈칸으로 돌아갔다. 이 파일이 있으면 설정.json 의 「이웃그룹」보다 먼저 쓴다.
GROUP_FILE = DATA / "이웃그룹.txt"
LEDGER_FILE = REC / "장부.csv"
CAND_FILE = DATA / "후보명단.csv"
REVIEW_FILE = DATA / "후보검토.html"
PROFILE_DIR = str(DATA / "크롬프로필")
COLLECT_PROFILE_DIR = str(DATA / "크롬프로필_수집")

UA_TEMPLATE = ("Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/{ver} Mobile Safari/537.36")
UA_MOBILE = UA_TEMPLATE.format(ver="124.0.0.0", model="SM-S908N", android="13")  # API 호출용 기본값

# 계정마다 다른 기기로 보이게 한다 (2026-09-22). 한 PC에서 두세 아이디를 돌리면 인터넷 주소가 같은데
# 기기 지문까지 같으면 「한 사람이 여러 계정을 돌린다」가 그대로 드러난다. 주소는 못 바꾸니 기기라도 가른다.
DEVICES = [
    {"model": "SM-S908N", "android": "13", "w": 390, "h": 844},
    {"model": "SM-S918N", "android": "14", "w": 384, "h": 854},
    {"model": "SM-A546S", "android": "14", "w": 412, "h": 915},
    {"model": "SM-S926N", "android": "14", "w": 400, "h": 880},
]


def device_of(cfg):
    """계정 아이디로 기기를 고정 선택 — 같은 계정은 늘 같은 기기(지문이 흔들리면 그것도 신호다).
    설정의 「기기」로 못 박을 수 있다 — 센터 계정은 09-08부터 SM-S908N 으로 돌아서 바꾸면 안 된다."""
    import zlib
    want = cfg.get("기기")
    if want:
        for d in DEVICES:
            if d["model"] == want:
                return d
    if int(cfg.get("계정수") or 1) > 1:
        # 한 PC에서 여러 아이디 — 순번으로 고르면 같은 PC 안에서 절대 안 겹친다
        # (아이디 해시로 고르면 둘이 같은 기기로 잡히는 일이 실제로 나왔다. 2026-09-25 시험)
        return DEVICES[int(cfg.get("계정순번", 0)) % len(DEVICES)]
    key = cfg.get("계정ID") or ""
    return DEVICES[zlib.crc32(key.encode("utf-8")) % len(DEVICES)] if key else DEVICES[0]

LEDGER_COLS = ["신청일", "블로그ID", "별명", "발굴키워드", "상태", "갱신일", "메모"]
# 상태 값: 신청 / 수락 / 소멸(거절·만료) / 기한취소 / 이미이웃 / 대기중(기존) /
#          서이추거부설정 / 실패-{사유}


# ──────────────────────────── 설정 ────────────────────────────

ME_FILE = BASE / "내정보.json"     # 트레이너 배포판: 이름·블로그 아이디 — zip 에 없는 파일이라 새 버전을 덮어써도 남는다


def load_cfg():
    with open(CFG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    if ME_FILE.exists():
        try:
            me = json.loads(ME_FILE.read_text(encoding="utf-8"))
            me.pop("계정목록", None)                      # 목록은 run_auto 가 쓰고, 여기선 지금 계정만 본다
            cfg.update(me)
        except Exception:
            pass
    if os.environ.get("NR_ACCOUNT_ID"):                   # 지금 도는 계정 (다계정)
        cfg["계정ID"] = os.environ["NR_ACCOUNT_ID"]
        cfg["로그인ID"] = os.environ.get("NR_LOGIN_ID") or os.environ["NR_ACCOUNT_ID"]
        cfg["계정순번"] = int(os.environ.get("NR_ACCOUNT_INDEX", 0))
        cfg["계정수"] = int(os.environ.get("NR_ACCOUNT_COUNT", 1))
    try:
        g = GROUP_FILE.read_text(encoding="utf-8").strip() if GROUP_FILE.exists() else ""
        if g:
            cfg["이웃그룹"] = g
    except Exception:
        pass
    return cfg


def save_group(name):
    """이 계정의 이웃그룹 이름을 계정 자리(DATA)의 이웃그룹.txt 에 저장한다."""
    GROUP_FILE.write_text(name.strip() + "\n", encoding="utf-8")


def save_cfg(cfg):
    if ME_FILE.exists():                          # 내정보 칸은 설정.json 에 되쓰지 않는다
        me = json.loads(ME_FILE.read_text(encoding="utf-8"))
        cfg = {k: v for k, v in cfg.items() if k not in me}
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ──────────────────────────── 여러 계정 나눠 쓰기 (트레이너 배포판, 2026-09-22) ────────────────────────────
# 트레이너들은 각자 자기 PC에서 돌려 장부를 같이 못 본다. 그래서 겹침을 두 겹으로 막는다.
#  ① 제외목록.txt — 센터 계정이 이미 신청했거나 대기 명단에 올린 블로거 (배포판을 만들 때 채워 넣는다)
#  ② 배정 — 설정의 「배정수」 N 과 「배정번호」 k 가 있으면, 블로그 아이디의 crc32 % N == k 인 사람에게만 간다.
#     트레이너 4명이 번호 0~3을 나눠 가지면 같은 블로거에게 두 트레이너가 가는 일이 없다.
# 센터판 설정에는 두 칸이 없으므로 동작이 그대로다.

EXCLUDE_FILE = BASE / "제외목록.txt"
WAREHOUSE_FILE = BASE / "창고후보.csv"     # 트레이너 배포판: 체험단모집기 후보 창고의 원주 거주자 (배포판 굽기 때 넣는다)


def warehouse_rows():
    """창고후보.csv — 없으면 빈 목록(센터판). 칸: 블로그ID·별명·발굴키워드 (+참고 칸)."""
    if not WAREHOUSE_FILE.exists():
        return []
    with open(WAREHOUSE_FILE, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if (r.get("블로그ID") or "").strip()]


def excluded_ids(cfg):
    ids = set(cfg.get("제외ID", []))
    if EXCLUDE_FILE.exists():
        for line in EXCLUDE_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.add(line)
    return ids


def assigned(bid, cfg):
    """내 몫인가. 두 겹으로 나눈다 —
    ① 트레이너 사이(배정수·배정번호): 네 분이 4등분
    ② 한 트레이너의 계정 사이(계정수·계정순번): 내 몫을 다시 내 아이디 수만큼 나눈다.
       ②는 ①이 쓰고 남은 자릿수로 나누므로, 아이디가 1개인 분의 몫이 낭비되지 않는다."""
    import zlib
    h = zlib.crc32(bid.encode("utf-8"))
    n = int(cfg.get("배정수") or 0)
    if n and h % n != int(cfg.get("배정번호", 0)):
        return False
    m = int(cfg.get("계정수") or 0)
    if m > 1 and (h // max(n, 1)) % m != int(cfg.get("계정순번", 0)):
        return False
    return True


# ──────────────────────────── 네이버 모바일 API ────────────────────────────

def api(bid, path=""):
    """m.blog.naver.com 공개 API. Referer 헤더가 열쇠다(키워드추적기에서 발견).
    실패 시 예외를 그대로 던진다 — 호출부에서 try."""
    req = urllib.request.Request(
        f"https://m.blog.naver.com/api/blogs/{bid}{path}",
        headers={"User-Agent": UA_MOBILE,
                 "Referer": f"https://m.blog.naver.com/{bid}"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))["result"]


def blog_info(bid):
    """방문수·이웃수·블로그명·닉네임."""
    info = api(bid, "?isGnbAccess=false")
    return {"방문수": info.get("totalVisitorCount", 0),
            "이웃수": info.get("subscriberCount", 0),
            "블로그명": info.get("blogName", "") or "",
            "별명": info.get("nickName", "") or ""}


def recent_posts(bid, count=10, page=1):
    """최근 글 [{제목, 발행일(date)}]."""
    items = api(bid, f"/post-list?categoryNo=0&itemCount={count}&page={page}").get("items", [])
    out = []
    for i in items:
        title = i.get("titleWithInspectMessage") or i.get("title") or ""
        ts = i.get("addDate")
        d = date.fromtimestamp(ts / 1000) if ts else None
        out.append({"제목": title.strip(), "발행일": d})
    return out


# ──────────────────────────── 장부 ────────────────────────────

def ledger_rows():
    if not LEDGER_FILE.exists():
        return []
    with open(LEDGER_FILE, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def ledger_write(rows):
    with open(LEDGER_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in LEDGER_COLS})


def ledger_append(new_rows):
    rows = ledger_rows()
    rows.extend(new_rows)
    ledger_write(rows)


FAIL_RETRY_MAX = 2      # 같은 블로거에서 실패가 이만큼 쌓이면 더 들어가지 않는다 (2026-09-22)


def blocked_ids():
    """재신청 금지 ID — 실패(일시 오류)는 빼고 전부 영구 차단.
    실패-* 는 다음 실행에서 다시 시도할 수 있어야 한다.
    단, 같은 블로거에서 실패가 FAIL_RETRY_MAX 번 쌓이면 일시 오류가 아니라고 보고 막는다
    (09-17~21 서로이웃 잠김 블로거 2명에게 매일 헛방문 5회 — 실패 재시도가 무한 반복됐다)."""
    rows = ledger_rows()
    fails = {}
    for r in rows:
        if r["상태"].startswith("실패"):
            fails[r["블로그ID"]] = fails.get(r["블로그ID"], 0) + 1
    return ({r["블로그ID"] for r in rows if not r["상태"].startswith("실패")}
            | {bid for bid, n in fails.items() if n >= FAIL_RETRY_MAX})


def ledger_ids():
    """장부에 있는 모든 블로그ID (상태 불문)."""
    return {r["블로그ID"] for r in ledger_rows()}


def sent_today():
    today = date.today().isoformat()
    return sum(1 for r in ledger_rows()
               if r["신청일"] == today and r["상태"].startswith(("신청", "대기")))


# ──────────────────────────── 브라우저 ────────────────────────────

BROWSER_ARGS = ["--disable-blink-features=AutomationControlled",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling"]


def launch_ctx(pw, cfg, 용도="신청"):
    """전용 프로필로 크롬을 띄운다. 용도에 따라 프로필과 지문이 갈린다.

    신청: 로그인 프로필 + **갤럭시 모바일 위장** — m.blog만 도는 데스크톱 크롬은
          그 자체로 어색한 지문이다. 폰으로 위장하면 동선 전체가 자연스럽다.
    수집: 별도 프로필(크롬프로필_수집, 비로그인) + 데스크톱 —
          발굴 크롤링의 흔적을 소중한 계정과 분리한다."""
    if 용도 == "수집":
        profile = COLLECT_PROFILE_DIR
        opts = dict(headless=False, locale="ko-KR",
                    viewport={"width": 1280, "height": 900}, args=BROWSER_ARGS)
    else:
        profile = PROFILE_DIR
        dev = device_of(cfg)
        opts = dict(headless=False, locale="ko-KR",
                    user_agent=UA_TEMPLATE.format(ver="124.0.0.0", model=dev["model"], android=dev["android"]),
                    viewport={"width": dev["w"], "height": dev["h"]},
                    device_scale_factor=3, is_mobile=True, has_touch=True,
                    args=BROWSER_ARGS)
    want = cfg.get("브라우저", "chrome")
    order = [want] + [c for c in ("chrome", "msedge", "chromium") if c != want]
    last = None
    for ch in order:
        try:
            if ch == "chromium":
                ctx = pw.chromium.launch_persistent_context(profile, **opts)
            else:
                ctx = pw.chromium.launch_persistent_context(profile, channel=ch, **opts)
            print(f"[i] 브라우저: {ch} ({용도}{'·모바일 위장' if 용도 == '신청' else ''})")
            if 용도 == "신청":
                _sync_fingerprint(ctx, device_of(cfg))
            return ctx
        except Exception as e:
            last = e
    raise RuntimeError(f"브라우저 실행 실패: {last}")


def _sync_fingerprint(ctx, dev=None):
    """UA 문자열·Client Hints·플랫폼을 실제 크롬 버전 + 갤럭시로 일치시킨다.

    실측(2026-09-08): user_agent 옵션만 주면 UA 는 Chrome/124 인데 navigator.userAgentData
    브랜드는 실제 버전(152)을 그대로 내보낸다 — 이 어긋남 자체가 봇 지문이다."""
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        product = cdp.send("Browser.getVersion").get("product", "")     # 'Chrome/152.0.7977.76'
        full = product.split("/")[-1] if "/" in product else "124.0.0.0"
        major = full.split(".")[0]
        cdp.detach()
    except Exception:
        return
    dev = dev or DEVICES[0]
    ua = UA_TEMPLATE.format(ver=full, model=dev["model"], android=dev["android"])
    meta = {"brands": [{"brand": "Chromium", "version": major},
                       {"brand": "Not?A_Brand", "version": "24"},
                       {"brand": "Google Chrome", "version": major}],
            "fullVersionList": [{"brand": "Chromium", "version": full},
                                {"brand": "Not?A_Brand", "version": "24.0.0.0"},
                                {"brand": "Google Chrome", "version": full}],
            "platform": "Android", "platformVersion": f"{dev['android']}.0.0", "architecture": "",
            "model": dev["model"], "mobile": True, "bitness": "", "wow64": False}

    def apply(pg):
        try:
            c = ctx.new_cdp_session(pg)
            c.send("Emulation.setUserAgentOverride",
                   {"userAgent": ua, "acceptLanguage": "ko-KR,ko;q=0.9",
                    "platform": "Linux armv8l", "userAgentMetadata": meta})
        except Exception:
            pass

    for pg in ctx.pages:
        apply(pg)
    ctx.on("page", apply)
    print(f"[i] 지문 동기화: Chrome/{full} · Android {dev['android']} · {dev['model']}")


# ──────────────────────────── 안전 운행 (워밍업·쿨다운) ────────────────────────────

COOL_FILE = REC / "쿨다운.json"


def cooldown_active():
    """보안문자·이용제한을 만나면 며칠 쉰다. 활성 상태면 정보를, 아니면 None."""
    if not COOL_FILE.exists():
        return None
    try:
        d = json.loads(COOL_FILE.read_text(encoding="utf-8"))
        if date.today() <= date.fromisoformat(d["until"]):
            return d
    except Exception:
        pass
    return None


def set_cooldown(days, reason):
    from datetime import timedelta
    d = {"until": (date.today() + timedelta(days=days)).isoformat(),
         "reason": reason, "set": date.today().isoformat()}
    COOL_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    new = not SANCTION_FILE.exists()                       # 이력은 지우지 않는다 — 램프 4단계 자격 판정에 쓴다
    with open(SANCTION_FILE, "a", encoding="utf-8-sig", newline="") as f:
        if new:
            print("날짜,사유,쿨다운까지", file=f)
        print(f"{d['set']},{reason},{d['until']}", file=f)
    print(f"[!] 쿨다운 설정: {d['until']}까지 신청 중단 ({reason})")


SANCTION_FILE = REC / "제재이력.csv"


def sanction_count():
    """보안문자·이용제한으로 쿨다운을 건 횟수. 램프 4단계(40)의 자격 조건."""
    if not SANCTION_FILE.exists():
        return 0
    with open(SANCTION_FILE, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)


def acceptance():
    """(보낸 수, 수락 수). 보낸 수 = 신청·수락·소멸·기한취소 상태 합."""
    rows = ledger_rows()
    sent = sum(1 for r in rows if r["상태"].startswith(("신청", "수락", "소멸", "기한취소")))
    acc = sum(1 for r in rows if r["상태"] == "수락")
    return sent, acc


def cap_today(cfg):
    """워밍업 램프 — 계정이 신청을 시작한 지 얼마나 됐나에 따라 한도를 서서히 올린다.
    1주차 10 · 2주차 20 · 3~4주차 30 · 이후 설정 한도(40) — 단, 4단계는 제재 이력 0 이고
    수락률 20% 이상(표본 20+)일 때만. 아니면 30에 머문다. (2026-09-08 대표님 위임으로 확정)"""
    limit = cfg.get("일일한도", 40)
    if not cfg.get("워밍업", True):
        return limit
    firsts = [r["신청일"] for r in ledger_rows()
              if r["신청일"] and r["상태"].startswith(("신청", "대기", "수락", "소멸", "기한취소"))]
    if not firsts:
        return min(10, limit)
    age = (date.today() - date.fromisoformat(min(firsts))).days
    if age < 7:
        ramp = 10
    elif age < 14:
        ramp = 20
    elif age < 28:
        ramp = 30
    else:
        sent, acc = acceptance()
        clean = sanction_count() == 0 and sent >= 20 and acc / sent >= 0.2
        ramp = limit if clean else 30
    return min(ramp, limit)


def snap(page, name):
    """상태를 못 알아본 화면을 저장 — 파일럿 뒤 선택자 보정의 근거."""
    path = SNAP / f"{datetime.now():%m%d_%H%M%S}_{name}.png"
    try:
        page.screenshot(path=str(path))
        print(f"    📸 화면 저장: {path.name}")
    except Exception:
        pass
    return path


def naver_logged_in(page):
    """네이버 로그인 여부 — 인증 쿠키(NID_AUT)가 유일하게 확실한 신호다.
    (FeedList 등은 로그아웃 상태에서도 리다이렉트가 없어 화면으로는 오판한다 — 실측)"""
    try:
        cookies = page.context.cookies("https://naver.com")
        return any(c["name"] == "NID_AUT" and c.get("value") for c in cookies)
    except Exception:
        return False


def ensure_login(page, cfg, wait_sec=420):
    """로그인 상태 확인. 안 돼 있으면 로그인 창을 띄우고 사람이 할 때까지 기다린다.
    비밀번호는 프로그램이 절대 다루지 않는다 — 인스타 봇과 같은 원칙.

    완료 판정은 Enter 가 아니라 인증 쿠키(NID_AUT)가 생기는 것으로 한다 — 사람·에이전트·
    스케줄러 누가 띄워도 같은 코드가 돈다. 단, 무인 모드(stdin 없음)에서는 기다리지 않는다.
    ⚠ 「로그인 상태 유지」를 켜지 않으면 세션 쿠키라 창을 닫는 순간 사라진다 (2026-09-08 실측)."""
    if naver_logged_in(page):
        return True
    import sys
    if not sys.stdin.isatty() and cfg.get("완전자동"):
        print(f"[!] 네이버 로그인이 안 돼 있습니다 ({cfg.get('계정ID', '')}). (무인 모드에서는 대기하지 않음)")
        print("    「2_로그인」을 한 번 더블클릭해 로그인하세요. (센터 PC 는 python 로그인.py)")
        return False
    print()
    print("=" * 50)
    print("  네이버 로그인이 필요합니다.")
    # ⚠ 로그인 아이디와 블로그 주소의 아이디는 다를 수 있다 (2026-10-09 실측):
    #    로그인 vivre-hyeoksin (하이픈) / 블로그 주소 vivre_hyeoksin (밑줄 — 네이버가 바꿔 보여준다)
    login_id = cfg.get("로그인ID") or cfg["계정ID"]
    print(f"  열린 브라우저에서 [{login_id}] 로 직접 로그인하세요.")
    if login_id != cfg["계정ID"]:
        print(f"  (블로그 주소에 보이는 {cfg['계정ID']} 가 아니라 위 아이디입니다 — 네이버가 - 를 _ 로 바꿔 보여줍니다)")
    print("  ⚠ 「로그인 상태 유지」를 반드시 켜세요 — 안 켜면 창 닫을 때 로그아웃됩니다.")
    print("  (로그인 정보는 이 창에만 저장되고 프로그램은 비밀번호를 모릅니다)")
    print(f"  로그인이 확인되면 자동으로 이어갑니다. 최대 {wait_sec // 60}분 기다립니다.")
    print("=" * 50, flush=True)
    page.goto("https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fm.blog.naver.com%2F")
    t0 = time.time()
    while not naver_logged_in(page):
        if time.time() - t0 > wait_sec:
            print("[!] 로그인 대기 시간 초과 — 다시 실행해 주세요.")
            return False
        time.sleep(2)
    page.wait_for_timeout(1500)
    try:                                                     # 세션 쿠키면 다음 실행에서 또 묻는다 — 미리 알린다
        c = next(c for c in page.context.cookies("https://naver.com") if c["name"] == "NID_AUT")
        if c.get("expires", -1) in (-1, 0):
            print("[!] 「로그인 상태 유지」가 꺼진 채 로그인됐습니다. 다음 실행에서 다시 로그인을 묻게 됩니다.")
    except Exception:
        pass
    print("[✓] 로그인 확인")
    return True
