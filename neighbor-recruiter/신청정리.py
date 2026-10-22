# -*- coding: utf-8 -*-
"""신청정리 — 보낸 신청의 뒷정리. 매주 1회면 충분하다.

사용법:
  python 신청정리.py             대기 중 신청의 상태 갱신 + 21일 지난 건 취소 시도
  python 신청정리.py --통계      장부 요약만 (브라우저 안 띄움)

왜 필요한가: 보낸 서이추 신청은 수락 전까지 이웃 5,000 한도의 자리를 차지한다.
오래된 신청을 걷어내지 않으면 한도가 말라붙는다.
"""
import sys
import time
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

from 공용 import (REC, load_cfg, launch_ctx, ensure_login, snap,
                  ledger_rows, ledger_write, blog_info)


def stats():
    rows = ledger_rows()
    if not rows:
        print("[i] 장부가 비어 있습니다.")
        return
    by = {}
    for r in rows:
        by[r["상태"]] = by.get(r["상태"], 0) + 1
    sent = sum(v for k, v in by.items() if k.startswith(("신청", "수락", "소멸", "기한취소")))
    acc = by.get("수락", 0)
    print(f"[통계] 장부 {len(rows)}건")
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        print(f"    {k:14} {v}건")
    if sent:
        rate = acc / sent * 100
        print(f"    ── 수락률: {acc}/{sent} = {rate:.0f}%")
        if sent >= 20 and rate < 20:
            print("    ⚠ 수락률이 20% 밑입니다 — 스팸으로 읽히고 있다는 신호.")
            print("      타겟 품질(검토 더 깐깐하게)·메시지를 손보고 한도를 낮추세요.")
    cfg = load_cfg()
    info = None
    try:
        info = blog_info(cfg["계정ID"])
        print(f"    ── 현재 {cfg['계정ID']}: 이웃 {info['이웃수']:,} · 방문 {info['방문수']:,}")
    except Exception:
        pass

    # 추이 축적 — 「이웃이 늘면 방문이 느나」를 몇 주 뒤 실측으로 답하기 위한 데이터
    if info:
        import csv as _csv
        trend = REC / "추이.csv"
        head = ["날짜", "이웃수", "방문수", "보낸누적", "수락누적", "수락률%"]
        today = date.today().isoformat()
        old = []
        if trend.exists():
            with open(trend, encoding="utf-8-sig") as f:
                old = [r for r in _csv.reader(f) if r and r[0] not in ("날짜", today)]   # 같은 날은 덮어쓴다
        with open(trend, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.writer(f)
            w.writerow(head)
            w.writerows(old)
            w.writerow([today, info["이웃수"], info["방문수"],
                        sent, acc, f"{acc / sent * 100:.0f}" if sent else ""])
        print(f"    ── 추이 기록: {trend.name} (주 단위로 쌓이면 효과가 그래프로 나온다)")

    # 최근 수락해 준 이웃 — 여기엔 사람이 직접 공감 한 번씩. 자동화 금지 구역.
    recent_acc = [r for r in rows if r["상태"] == "수락"
                  and r.get("갱신일", "") >= (date.today() - timedelta(days=7)).isoformat()]
    if recent_acc:
        print(f"    ── 이번 주 수락 이웃 {len(recent_acc)}명 — 글에 공감 한 번씩 눌러주면 관계가 삽니다 (수동):")
        for r in recent_acc[:10]:
            print(f"        https://m.blog.naver.com/{r['블로그ID']}")


def check_one(page, bid):
    """대기 중 신청 하나의 현재 상태를 확인. 반환: (새상태 | None, 취소가능여부)

    실측(2026-09-08): 보낸 신청이 살아 있으면 BuddyAddForm 이 확인창을 띄운다 —
    「서로이웃 신청 진행중입니다. 서로이웃 신청을 취소하시겠습니까?」 + #_confirmLayercancel / #_confirmLayerOk.
    보낸 신청은 이웃목록(내가 추가한)에는 나오지 않는다. 수락·소멸 문구는 아직 미실측 → 보수적으로 판정."""
    page.goto(f"https://m.blog.naver.com/BuddyAddForm.naver?blogId={bid}",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    t = page.inner_text("body").replace(" ", "")
    if "신청진행중" in t or page.query_selector("#_confirmLayerOk"):
        return None, True                    # 아직 대기 — 이 화면의 「확인」이 곧 취소다
    if "이미추가한이웃" in t or "이미서로이웃" in t or "서로이웃인" in t or "이웃으로추가되어" in t:
        return "수락", False                 # 실측(2026-09-08 babyhotato 첫 수락): 「이미 추가한 이웃입니다.」
    alert = page.query_selector("#_alertLayerClose")
    if alert and alert.is_visible():
        snap(page, f"알림모름_{bid}")           # 모르는 알림 문구 — 실측 뒤 위 조건에 추가
        return None, False
    try:
        form_visible = page.is_visible("input#bothBuddyRadio")
    except Exception:
        form_visible = False
    if form_visible:
        snap(page, f"소멸_{bid}")               # 첫 소멸 케이스 실측용
        return "소멸(거절·만료)", False        # 폼이 새로 뜬다 = 신청이 사라졌다 (거절 또는 상대 취소)
    return None, False


def try_cancel(page, bid):
    """check_one 직후 확인창이 떠 있는 상태에서 「확인」(#_confirmLayerOk)을 눌러 신청을 취소한다.
    실측 구조(2026-09-08). 취소 뒤 다시 열었을 때 폼이 새로 뜨면 성공."""
    try:
        ok = page.query_selector("#_confirmLayerOk")
        if not (ok and ok.is_visible()):
            return False
        ok.click()
        page.wait_for_timeout(1500)
        snap(page, f"취소후_{bid}")                             # 취소 뒤 화면 실측용 (첫 취소 때 확인)
        page.goto(f"https://m.blog.naver.com/BuddyAddForm.naver?blogId={bid}",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        return page.query_selector("#_confirmLayerOk") is None
    except Exception:
        return False


def main():
    if "--통계" in sys.argv:
        stats()
        return

    cfg = load_cfg()
    rows = ledger_rows()
    pending = [r for r in rows if r["상태"].startswith(("신청", "대기"))]
    if not pending:
        print("[i] 대기 중 신청이 없습니다.")
        stats()
        return

    today = date.today()
    cut = cfg.get("취소기준일", 21)
    print(f"[i] 대기 중 {len(pending)}건 상태 확인 (기준: {cut}일 지나면 취소)")

    manual = []
    with sync_playwright() as pw:
        ctx = launch_ctx(pw, cfg)
        page = ctx.new_page()
        if not ensure_login(page, cfg):
            ctx.close()
            return
        for i, r in enumerate(pending, 1):
            bid = r["블로그ID"]
            try:
                new_state, cancellable = check_one(page, bid)
            except Exception as e:
                print(f"  ({i}/{len(pending)}) {bid} ⚠ {type(e).__name__}")
                continue
            age = (today - date.fromisoformat(r["신청일"])).days if r["신청일"] else 0
            if new_state:
                r["상태"] = new_state
                r["갱신일"] = today.isoformat()
                print(f"  ({i}/{len(pending)}) {bid} → {new_state}")
            elif age > cut and cancellable:
                if try_cancel(page, bid):
                    r["상태"] = "기한취소"
                    r["갱신일"] = today.isoformat()
                    print(f"  ({i}/{len(pending)}) {bid} → 기한취소 ({age}일 경과)")
                else:
                    manual.append(bid)
                    snap(page, f"취소실패_{bid}")
                    print(f"  ({i}/{len(pending)}) {bid} → 취소 버튼 못 찾음 ({age}일)")
            else:
                print(f"  ({i}/{len(pending)}) {bid} → 대기 유지 ({age}일)")
            time.sleep(2.5)
        ctx.close()

    ledger_write(rows)
    print("[✓] 장부 갱신 완료")
    if manual:
        print(f"[!] 수동 취소 필요 {len(manual)}건 — m.blog 에서 그 블로그 「이웃추가」를 누르면 뜨는 취소 확인창에서 확인")
        print("    " + ", ".join(manual))
    stats()


if __name__ == "__main__":
    main()
