# -*- coding: utf-8 -*-
"""후보뽑기 — 검색키워드로 상권 블로거를 발굴하고, 실사용자만 걸러 명단을 만든다.

사용법:
  python 후보뽑기.py              후보 수집 → 후보명단.csv + 후보검토.html
  python 후보뽑기.py --자동       점수 3점 이상만 승인(O) 표시 (완전자동 모드용)

거르는 것(전부 자동):
  · 장부에 이미 있는 사람(영구 재신청 금지) · 이미 신청/이웃
  · 45일 넘게 글 없는 휴면 블로그 · 방문수 100 미만 유령
  · 최근 10개 글의 절반이 협찬·체험단 글인 직업 체험단
  · 블로그명·별명이 동종업계(트레이너·필라테스…)인 계정
  · 이웃 4,500명 초과(5,000 한도 임박 — 수락 여력 없음)

반자동 원칙: 명단은 사람이 검토한다. 후보명단.csv의 「승인」 칸이 O인 사람만
신청실행이 처리한다. 빼고 싶으면 O를 지우거나 X로 바꾼다.
"""
import csv
import html
import sys
import time
import urllib.parse
from datetime import date

from playwright.sync_api import sync_playwright

from 공용 import (CAND_FILE, REVIEW_FILE, load_cfg, launch_ctx, ledger_ids,
                  blog_info, recent_posts, excluded_ids, assigned)

CAND_COLS = ["승인", "블로그ID", "별명", "블로그명", "방문수", "이웃수",
             "최근발행", "점수", "발굴키워드", "최근글", "링크"]

EXTRACT_POSTS_JS = """() => {
    const root = document.querySelector('ul.lst_view') || document.body;
    const seen = new Set();
    const posts = [];
    for (const a of root.querySelectorAll('a[href*="blog.naver.com"]')) {
        const m = a.href.match(/blog\\.naver\\.com\\/([A-Za-z0-9_-]+)\\/(\\d{8,})/);
        if (!m) continue;
        const key = m[1] + '/' + m[2];
        if (!seen.has(key)) { seen.add(key); posts.push({blog: m[1], post: m[2]}); }
    }
    return posts;
}"""


def collect_authors(page, keywords, per_kw=30):
    """키워드별 블로그 탭 검색 → 글쓴이 ID 수집. {id: 발굴키워드}"""
    authors = {}
    for idx, kw in enumerate(keywords, 1):
        try:
            url = ("https://search.naver.com/search.naver?ssc=tab.blog.all"
                   f"&sm=tab_jum&query={urllib.parse.quote(kw)}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            if "보안문자" in page.content():
                print(f"  ({idx}/{len(keywords)}) {kw}: ⚠ 캡차 — 건너뜀")
                continue
            posts = page.evaluate(EXTRACT_POSTS_JS)
            stall = 0
            while len(posts) < per_kw and stall < 3:
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1100)
                new = page.evaluate(EXTRACT_POSTS_JS)
                stall = stall + 1 if len(new) == len(posts) else 0
                posts = new
            fresh = 0
            for p in posts[:per_kw]:
                if p["blog"] not in authors:
                    authors[p["blog"]] = kw
                    fresh += 1
            print(f"  ({idx}/{len(keywords)}) {kw}: 글 {len(posts[:per_kw])}건, 새 블로거 {fresh}명")
            time.sleep(0.8)
        except Exception as e:
            print(f"  ({idx}/{len(keywords)}) {kw}: ⚠ {type(e).__name__}")
    return authors


def examine(bid, kw, cfg, today):
    """블로그 하나를 실측해 (후보row | None, 탈락사유)를 돌려준다."""
    try:
        info = blog_info(bid)
    except Exception:
        return None, "조회실패"
    if info["방문수"] < cfg.get("최소방문", 100):
        return None, "유령(방문수)"
    if info["이웃수"] > 4500:
        return None, "이웃한도임박"
    name_nick = (info["블로그명"] + " " + info["별명"]).lower()
    if any(w.lower() in name_nick for w in cfg.get("업종제외어", [])):
        return None, "동종업계"
    try:
        posts = recent_posts(bid, count=10)
        time.sleep(0.25)
    except Exception:
        return None, "글조회실패"
    if not posts or not posts[0]["발행일"]:
        return None, "글없음"
    days = (today - posts[0]["발행일"]).days
    if days > cfg.get("활동기준일", 45):
        return None, "휴면"
    titles = [p["제목"] for p in posts]
    ad = sum(1 for t in titles
             if any(w in t for w in cfg.get("체험단어", [])))
    if ad >= 5:
        return None, "직업체험단"
    junk = sum(1 for t in titles
               if any(w in t for w in cfg.get("잡블로그단어", [])))
    if junk >= 4:
        return None, "잡블로그(퀴즈·앱테크)"       # 지역 주민이 아니라 이벤트 헌터

    score = 0
    if any(any(r in t for r in cfg.get("지역단어", [])) for t in titles):
        score += 2                                   # 제목에 지역 언급 = 진짜 주민 신호
    if 100 <= info["이웃수"] <= 3000:
        score += 1                                   # 서이추 문화권 + 수락 여력
    if 1000 <= info["방문수"] <= 300000:
        score += 1                                   # 실사용 체급
    if days <= 14:
        score += 1                                   # 지금 활동 중

    row = {"승인": "O", "블로그ID": bid, "별명": info["별명"],
           "블로그명": info["블로그명"], "방문수": info["방문수"],
           "이웃수": info["이웃수"], "최근발행": posts[0]["발행일"].isoformat(),
           "점수": score, "발굴키워드": kw,
           "최근글": " | ".join(titles[:3]),
           "링크": f"https://m.blog.naver.com/{bid}"}
    return row, ""


def write_review_html(rows):
    cards = []
    for r in rows:
        titles = html.escape(r["최근글"]).replace(" | ", "<br>· ")
        cards.append(f"""
  <a class="card" href="{r['링크']}" target="_blank">
    <div class="head"><b>{html.escape(r['별명'] or r['블로그ID'])}</b>
      <span class="score">★{r['점수']}</span></div>
    <div class="meta">{html.escape(r['블로그명'])} · 방문 {r['방문수']:,} · 이웃 {r['이웃수']:,}
      · 최근 {r['최근발행']}</div>
    <div class="kw">발굴: {html.escape(r['발굴키워드'])}</div>
    <div class="titles">· {titles}</div>
    <div class="bid">{r['블로그ID']}</div>
  </a>""")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>이웃 후보 검토 — {len(rows)}명</title><style>
body{{background:#0d1526;color:#e8edf6;font-family:'Malgun Gothic',sans-serif;
     margin:0;padding:20px;max-width:760px;margin:auto}}
h1{{font-size:19px}} .tip{{color:#8fa4c4;font-size:13px;line-height:1.6}}
.card{{display:block;background:#16213c;border:1px solid #24345c;border-radius:10px;
      padding:13px 15px;margin:10px 0;text-decoration:none;color:inherit}}
.card:hover{{border-color:#4a7de0}}
.head{{display:flex;justify-content:space-between;font-size:15px}}
.score{{color:#f4c542}} .meta{{color:#8fa4c4;font-size:12px;margin-top:3px}}
.kw{{color:#5e9bff;font-size:12px;margin-top:2px}}
.titles{{font-size:12px;color:#c3cede;margin-top:6px;line-height:1.5}}
.bid{{color:#5a6a88;font-size:11px;margin-top:5px}}
</style></head><body>
<h1>이웃 후보 검토 — {len(rows)}명 (점수 높은 순)</h1>
<p class="tip">카드를 누르면 그 블로그가 열립니다. 빼고 싶은 사람은
<b>후보명단.csv</b>에서 그 줄의 「승인」 칸을 지우거나 X로 바꾸세요.<br>
검토가 끝나면 <b>python 신청실행.py</b> 를 실행합니다.</p>
{''.join(cards)}
</body></html>"""
    out = REVIEW_FILE
    out.write_text(doc, encoding="utf-8")
    return out


def apply_expansion(cfg, today):
    """확장지역 — 시작일이 지난 단계의 「검색접두 × 확장생활키워드」를 검색키워드에, 지역단어를 점수 단어에 합친다.
    상권분석 --적용 은 지역단어·검색키워드만 덮어쓰므로 확장지역 설정은 살아남는다. (2026-09-08)
    동 단위 이름은 다른 도시에도 있으니(신림=서울, 안흥=태안) 검색접두에 반드시 도시명을 붙인다."""
    kws = list(cfg.get("검색키워드", []))
    regions = list(cfg.get("지역단어", []))
    life = cfg.get("확장생활키워드", ["맛집", "카페", "일상", "육아"])
    active = []
    for st in cfg.get("확장지역", []):
        try:
            if date.fromisoformat(st.get("시작일", "1900-01-01")) > today:
                continue
        except ValueError:
            continue
        active.append(st.get("이름", "?"))
        for pre in st.get("검색접두", []):
            for w in life:
                k = f"{pre} {w}"
                if k not in kws:
                    kws.append(k)
        for r in st.get("지역단어", []):
            if r not in regions:
                regions.append(r)
    cfg["검색키워드"], cfg["지역단어"] = kws, regions          # 이번 실행에서만 — 파일에는 안 쓴다
    return active


def main():
    auto = "--자동" in sys.argv
    cfg = load_cfg()
    active = apply_expansion(cfg, date.today())
    if active:
        print(f"[i] 확장지역 켜짐: {', '.join(active)}")
    keywords = cfg.get("검색키워드", [])
    if not keywords:
        print("[!] 검색키워드가 비었습니다. 먼저:  python 상권분석.py --적용")
        return
    known = ledger_ids() | excluded_ids(cfg) | {cfg["계정ID"]}
    limit = cfg.get("후보최대", 60)
    today = date.today()

    print(f"[1/3] 검색 수집 — 키워드 {len(keywords)}개 (비로그인 수집 전용 프로필)")
    with sync_playwright() as pw:
        ctx = launch_ctx(pw, cfg, 용도="수집")
        page = ctx.new_page()
        authors = collect_authors(page, keywords)
        ctx.close()

    fresh = [(b, k) for b, k in authors.items() if b not in known]
    todo = [(b, k) for b, k in fresh if assigned(b, cfg)]
    if len(todo) != len(fresh):
        print(f"[i] 배정 나눔: 새 블로거 {len(fresh)}명 중 내 몫 {len(todo)}명 (나머지는 다른 트레이너 몫)")
    print(f"[2/3] 실측 필터 — 새 블로거 {len(todo)}명 (블로그당 약 1초)")
    rows, drops = [], {}
    for i, (bid, kw) in enumerate(todo, 1):
        row, why = examine(bid, kw, cfg, today)
        if row:
            rows.append(row)
        else:
            drops[why] = drops.get(why, 0) + 1
        if i % 20 == 0:
            print(f"    …{i}/{len(todo)} (후보 {len(rows)}명)")
        time.sleep(0.25)

    # 점수 → 최근 활동 순 (안정 정렬 2단). 방문수 순은 대형 상업 블로거만 남긴다(실측).
    rows.sort(key=lambda r: r["최근발행"], reverse=True)
    rows.sort(key=lambda r: r["점수"], reverse=True)
    rows = rows[:limit]
    if auto:
        for r in rows:
            r["승인"] = "O" if r["점수"] >= 3 else ""
    else:
        # 재실행해도 이미 검토한 승인 표시(X·빈칸 포함)는 잃지 않는다
        if CAND_FILE.exists():
            with open(CAND_FILE, encoding="utf-8-sig") as f:
                old = {r["블로그ID"]: r.get("승인", "") for r in csv.DictReader(f)}
            for r in rows:
                if r["블로그ID"] in old:
                    r["승인"] = old[r["블로그ID"]]

    with open(CAND_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAND_COLS)
        w.writeheader()
        w.writerows(rows)
    review = write_review_html(rows)

    print(f"[3/3] 완료 — 후보 {len(rows)}명")
    if drops:
        print("    탈락:", ", ".join(f"{k} {v}" for k, v in sorted(drops.items(), key=lambda x: -x[1])))
    print(f"    검토 화면: {review}")
    print(f"    명단 파일: {CAND_FILE}")
    if auto:
        ok = sum(1 for r in rows if r["승인"] == "O")
        print(f"    자동 승인: 점수 3+ → {ok}명")
    else:
        print("    검토 후:  python 신청실행.py")


if __name__ == "__main__":
    main()
