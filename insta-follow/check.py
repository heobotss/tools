# -*- coding: utf-8 -*-
"""
인스타그램 '맞팔 안 하는 사람' 찾기

인스타그램 공식 데이터 내보내기(Download Your Information) ZIP 파일만 있으면
로그인/비밀번호 없이 안전하게 목록을 뽑아냅니다.

사용법:
    python check.py "C:\\경로\\instagram-내아이디-....zip"
    python check.py "C:\\경로\\압축푼폴더"
    python check.py            (같은 폴더에 있는 ZIP 자동 탐색)
"""

import sys
import os
import json
import csv
import zipfile
import html
import glob
import safe_file

if hasattr(sys.stdout, "reconfigure"):
    # 콘솔이 쓰는 인코딩을 그대로 두고, 표현 못 하는 글자만 ? 로 바꾼다.
    # utf-8로 강제하면 한국어 Windows cmd 창(CP949)에서 한글이 깨진다.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 파일 읽기

def load_sources(target):
    """ZIP이든 폴더든 {파일경로소문자: bytes} 형태로 JSON 파일들을 모은다."""
    files = {}

    if zipfile.is_zipfile(target):
        with zipfile.ZipFile(target) as z:
            for name in z.namelist():
                if name.lower().endswith(".json"):
                    files[name.replace("\\", "/").lower()] = z.read(name)
        return files, "zip"

    if os.path.isdir(target):
        for root, _dirs, names in os.walk(target):
            for n in names:
                if n.lower().endswith(".json"):
                    p = os.path.join(root, n)
                    rel = os.path.relpath(p, target).replace("\\", "/").lower()
                    with open(p, "rb") as f:
                        files[rel] = f.read()
        return files, "dir"

    raise SystemExit("[!] ZIP 파일도 폴더도 아닙니다: %s" % target)


def pick(files, *names):
    """파일 이름이 names 중 하나로 시작하는 json들을 고른다.

    following_hashtags.json 같은 건 사람이 아니므로 제외한다.
    """
    skip = ("following_hashtags",)
    out = []
    for path, data in files.items():
        base = path.rsplit("/", 1)[-1]
        if base.startswith(skip):
            continue
        if any(base.startswith(n) for n in names):
            out.append((path, data))
    return sorted(out)


# ---------------------------------------------------------------- 파싱

def extract_users(raw):
    """
    인스타 내보내기 JSON에서 (username, href, timestamp)를 전부 긁어온다.

    구조가 버전마다 달라서(최상위가 배열이거나 relationships_* 키로 감싸여 있거나)
    통째로 재귀 탐색하며 string_list_data를 찾는다.
    """
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        return []

    found = []

    def walk(node):
        if isinstance(node, dict):
            sld = node.get("string_list_data")
            if isinstance(sld, list) and sld:
                # followers는 아이디가 value에, following은 바깥 title에 들어 있다
                title = (node.get("title") or "").strip()
                for item in sld:
                    if not isinstance(item, dict):
                        continue
                    name = (item.get("value") or "").strip() or title
                    href = item.get("href") or ""
                    # 둘 다 비면 href 끝에서 아이디를 뽑는다
                    if not name and href:
                        name = href.rstrip("/").rsplit("/", 1)[-1]
                    if name:
                        found.append((name, href, item.get("timestamp")))
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return found


def to_map(entries):
    """username(소문자) -> (원래표기, href, timestamp)"""
    m = {}
    for name, href, ts in entries:
        m.setdefault(name.lower(), (name, href, ts))
    return m


def fmt_time(ts):
    if not ts:
        return ""
    try:
        import datetime
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return ""


# ---------------------------------------------------------------- 출력

def profile_url(name, href):
    # 내보내기의 href는 /_u/아이디 형태(앱 딥링크)라 브라우저용으로 바꿔서 쓴다
    return "https://www.instagram.com/%s/" % name


def write_csv(path, rows):
    with safe_file.open_write(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["번호", "아이디", "프로필 주소", "내가 팔로우한 날"])
        for i, (name, href, ts) in enumerate(rows, 1):
            w.writerow([i, name, profile_url(name, href), fmt_time(ts)])


def write_html(path, rows, me_total, back_total):
    items = []
    for i, (name, href, ts) in enumerate(rows, 1):
        items.append(
            '<li><label><input type="checkbox"><span class="n">%d</span>'
            '<a href="%s" target="_blank" rel="noopener">@%s</a>'
            '<span class="d">%s</span></label></li>'
            % (i, html.escape(profile_url(name, href)), html.escape(name), html.escape(fmt_time(ts)))
        )

    doc = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>맞팔 안 하는 계정</title>
<style>
 :root{color-scheme:light dark;--bg:#fff;--fg:#111;--sub:#777;--line:#e5e5e5;--acc:#c13584}
 @media(prefers-color-scheme:dark){:root{--bg:#151515;--fg:#eee;--sub:#999;--line:#2c2c2c;--acc:#e17bb4}}
 *{box-sizing:border-box}
 body{margin:0 auto;padding:24px;background:var(--bg);color:var(--fg);max-width:720px;
      font:15px/1.6 -apple-system,'Malgun Gothic',sans-serif}
 h1{font-size:20px;margin:0 0 4px}
 .sum{color:var(--sub);font-size:13px;margin-bottom:20px}
 .tip{border:1px solid var(--line);border-radius:10px;padding:12px 16px;
      font-size:13px;color:var(--sub);margin-bottom:20px}
 ul{list-style:none;padding:0;margin:0}
 li{border-bottom:1px solid var(--line)}
 label{display:flex;align-items:center;gap:10px;padding:10px 4px;cursor:pointer}
 input{width:17px;height:17px;accent-color:var(--acc);flex:none}
 .n{color:var(--sub);font-size:12px;min-width:34px;flex:none}
 a{color:var(--acc);text-decoration:none;font-weight:600}
 a:hover{text-decoration:underline}
 .d{margin-left:auto;color:var(--sub);font-size:12px}
 input:checked ~ a{opacity:.35;text-decoration:line-through}
 input:checked ~ .n, input:checked ~ .d{opacity:.35}
</style></head><body>
<h1>맞팔 안 하는 계정 __COUNT__명</h1>
<div class="sum">내가 팔로우 중 __ME__명 · 나를 팔로우 __BACK__명</div>
<div class="tip">아이디를 누르면 프로필이 새 탭에서 열립니다. 언팔로우한 계정은 체크해서 표시해 두세요
(체크 상태는 저장되지 않으니 창을 닫기 전에 끝내는 걸 권합니다).<br><br>
한 번에 몰아서 하지 말고 <b>시간당 30~50개, 하루 100~150개</b> 정도로 나눠서 하세요.
너무 빠르면 인스타가 일시적으로 기능을 차단합니다.</div>
<ul>
__ITEMS__
</ul>
</body></html>"""

    doc = (doc.replace("__COUNT__", str(len(rows)))
              .replace("__ME__", str(me_total))
              .replace("__BACK__", str(back_total))
              .replace("__ITEMS__", "\n".join(items)))

    with safe_file.open_write(path, "w", encoding="utf-8") as f:
        f.write(doc)


# ---------------------------------------------------------------- 메인

def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        cands = sorted(glob.glob(os.path.join(HERE, "*.zip")))
        if not cands:
            print(__doc__)
            print("[!] 같은 폴더에서 ZIP을 못 찾았습니다. 경로를 인자로 넘겨주세요.")
            return 1
        target = cands[0]
        print("[i] 자동 선택: %s" % os.path.basename(target))

    if not os.path.exists(target):
        raise SystemExit("[!] 경로가 없습니다: %s" % target)

    files, kind = load_sources(target)
    print("[i] %s 안에서 JSON %d개를 찾았습니다." % ("ZIP" if kind == "zip" else "폴더", len(files)))

    following_files = pick(files, "following")
    follower_files = pick(files, "followers")

    if not following_files or not follower_files:
        print("")
        print("[!] followers / following 파일을 찾지 못했습니다.")
        print("    내보내기를 받을 때 형식을 반드시 'JSON'으로 골라야 합니다 (HTML 아님).")
        print("    그리고 'Followers and following' 항목이 포함돼 있어야 합니다.")
        print("")
        print("    참고 - 찾은 JSON 파일 목록(최대 25개):")
        for p in list(files)[:25]:
            print("      - %s" % p)
        return 1

    print("[i] following 파일: %s" % ", ".join(p for p, _ in following_files))
    print("[i] followers 파일: %s" % ", ".join(p for p, _ in follower_files))

    following = to_map([u for _p, d in following_files for u in extract_users(d)])
    followers = to_map([u for _p, d in follower_files for u in extract_users(d)])

    if not following or not followers:
        print("")
        print("[!] 목록이 비어 있습니다. 파일 구조가 예상과 다를 수 있습니다.")
        return 1

    not_back_keys = sorted(set(following) - set(followers))
    fans_keys = sorted(set(followers) - set(following))
    mutual = len(set(following) & set(followers))

    rows = [following[k] for k in not_back_keys]

    print("")
    print("=" * 46)
    print("  내가 팔로우 중         : %5d 명" % len(following))
    print("  나를 팔로우 중         : %5d 명" % len(followers))
    print("  맞팔                   : %5d 명" % mutual)
    print("-" * 46)
    print("  맞팔 안 하는 계정      : %5d 명  <-- 언팔 후보" % len(rows))
    print("  나만 안 팔로우한 계정  : %5d 명" % len(fans_keys))
    print("=" * 46)

    csv_path = os.path.join(HERE, "맞팔안함_목록.csv")
    html_path = os.path.join(HERE, "맞팔안함_목록.html")
    write_csv(csv_path, rows)
    write_html(html_path, rows, len(following), len(followers))

    print("")
    print("[+] 저장 완료")
    print("    %s" % csv_path)
    print("    %s" % html_path)
    print("    -> HTML을 브라우저로 열면 클릭 한 번에 프로필로 이동합니다.")

    if rows:
        print("")
        print("[미리보기] 앞 15명")
        for i, (name, _h, _t) in enumerate(rows[:15], 1):
            print("   %2d. @%s" % (i, name))
        if len(rows) > 15:
            print("   ... 외 %d명" % (len(rows) - 15))

    return 0


if __name__ == "__main__":
    sys.exit(safe_file.run_main(main))
