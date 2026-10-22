# -*- coding: utf-8 -*-
"""로그인 — 로그인이 풀렸을 때 한 번. 브라우저가 뜨면 「로그인 상태 유지」를 켜고 로그인한다."""
from playwright.sync_api import sync_playwright
from 공용 import load_cfg, launch_ctx, ensure_login

cfg = load_cfg()
cfg["완전자동"] = False                       # 이 창에서는 사람을 기다린다
with sync_playwright() as pw:
    ctx = launch_ctx(pw, cfg, 용도="신청")
    page = ctx.new_page()
    ok = ensure_login(page, cfg, wait_sec=600)
    ctx.close()
print("[✓] 로그인 저장됨. 이제 매일 실행(자동운행)이 이어서 돕니다." if ok else "[!] 로그인이 안 됐습니다. 다시 실행하세요.")
