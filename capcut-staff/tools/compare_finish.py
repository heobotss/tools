"""자동 드래프트 ↔ 대표님 완성본을 **원본 시각** 위에서 항목별로 채점한다.

    python tools/compare_finish.py <자동 드래프트> <완성본> [--show]

컷이 서로 다르면 타임라인 시각은 비교가 안 된다. 그래서 두 드래프트의 모든 요소를
본 영상 클립을 거꾸로 따라가 **원본 영상의 몇 초**로 옮긴 뒤 겹친다.

  컷      남긴 원본 구간 겹침 (덜 자름 / 더 자름)
  자막    글 일치 · 줄 경계 일치
  색      색 줄 (원본 시각 겹침) 정밀도·재현율
  확대    확대된 원본 시간 겹침 F1 · 배율 일치
  카드    시작 원본 시각 ±1초 일치
  효과음  원본 시각 ±0.3초 일치 · 같은 소리 비율
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

US = 1_000_000
DRAFTS = Path(r"C:\Users\허재윤\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft")


def f(us):
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def norm(s):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


class Draft:
    def __init__(self, name: str):
        p = Path(name)
        folder = p if p.is_dir() else DRAFTS / name
        self.name = folder.name
        d = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
        vids = {m["id"]: m for m in d["materials"].get("videos", [])}
        auds = {m["id"]: m for m in d["materials"].get("audios", [])}
        texts = {m["id"]: m for m in d["materials"].get("texts", [])}
        vt = [t for t in d["tracks"] if t["type"] == "video" and t.get("segments")]
        main = max(vt, key=lambda t: len(t["segments"]))
        self.clips = []
        for s in sorted(main["segments"], key=lambda s: s["target_timerange"]["start"]):
            tr, sr = s["target_timerange"], s["source_timerange"]
            self.clips.append((tr["start"], tr["duration"], sr["start"], sr["duration"],
                               round(((s.get("clip") or {}).get("scale") or {}).get("x", 1), 2)))
        self.cards = sorted(s["target_timerange"]["start"] for t in vt if t is not main
                            for s in t["segments"])
        self.sfx = sorted((s["target_timerange"]["start"], auds.get(s["material_id"], {}).get("name", "?"))
                          for t in d["tracks"] if t["type"] == "audio" for s in t["segments"])
        tts = [t for t in d["tracks"] if t["type"] == "text" and t.get("segments")]
        self.subs = []
        if tts:
            tt = max(tts, key=lambda t: len(t["segments"]))
            for s in sorted(tt["segments"], key=lambda s: s["target_timerange"]["start"]):
                try:
                    c = json.loads(texts[s["material_id"]]["content"])
                    txt = c["text"]
                    col = c["styles"][0]["fill"]["content"]["solid"]["color"]
                except Exception:
                    txt, col = "", [1, 1, 1]
                kind = "normal" if all(x > 0.95 for x in col) else ("neg" if col[0] > 0.8 else "pos")
                self.subs.append((s["target_timerange"]["start"], s["target_timerange"]["duration"], txt, kind))

    def src(self, t: int) -> float | None:
        for t0, dur, s0, sdur, _ in self.clips:
            if t0 <= t < t0 + dur:
                return s0 + (t - t0) * sdur / dur
        return None

    def src_span(self, a: int, b: int) -> list[tuple[float, float]]:
        out = []
        for t0, dur, s0, sdur, _ in self.clips:
            x, y = max(a, t0), min(b, t0 + dur)
            if y > x:
                out.append((s0 + (x - t0) * sdur / dur, s0 + (y - t0) * sdur / dur))
        return out


def inter(A, B):
    return sum(max(0, min(b, d) - max(a, c)) for a, b in A for c, d in B)


def total(A):
    return sum(b - a for a, b in A)


def prf(tp_p, n_p, tp_r, n_r):
    p = tp_p / n_p if n_p else 0
    r = tp_r / n_r if n_r else 0
    return p, r, (2 * p * r / (p + r) if p + r else 0)


def main() -> int:
    A, B = Draft(sys.argv[1]), Draft(sys.argv[2])
    show = "--show" in sys.argv
    print(f"════ 자동 {A.name}  ↔  완성 {B.name} ════")

    ka = [(s0, s0 + sd) for _, _, s0, sd, _ in A.clips]
    kb = [(s0, s0 + sd) for _, _, s0, sd, _ in B.clips]
    both = inter(ka, kb)
    print(f"컷    남긴 원본 자동 {total(ka)/US:.1f}s · 완성 {total(kb)/US:.1f}s · "
          f"덜 자름 {(total(ka)-both)/US:.1f}s · 더 자름 {(total(kb)-both)/US:.1f}s · "
          f"클립 {len(A.clips)} / {len(B.clips)}")

    # 자막 글·줄 경계
    ta = [x[2] for x in A.subs]
    tb = [x[2] for x in B.subs]
    na, nb = norm("".join(ta)), norm("".join(tb))
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    mp = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size + 1):
            mp[blk.b + k] = blk.a + k

    def cuts(rows):
        pos, out = 0, set()
        for r in rows:
            pos += len(norm(r))
            out.add(pos)
        return out
    ca, cb = cuts(ta), {mp[x] for x in cuts(tb) if x in mp}
    print(f"자막  {len(A.subs)}줄 / {len(B.subs)}줄 · 글 일치 {sm.ratio()*100:.1f}% · "
          f"줄 경계 일치 {len(ca & cb)/max(1, len(cb))*100:.1f}%")

    # 색 — 원본 시각에서 색 줄 구간 겹침
    def colspans(D, kind=None):
        out = []
        for t0, dur, _, k in D.subs:
            if k != "normal" and (kind is None or k == kind):
                out += D.src_span(t0, t0 + dur)
        return out
    cA, cB = colspans(A), colspans(B)
    ic = inter(cA, cB)
    p, r, f1 = prf(ic, total(cA), ic, total(cB))
    same_kind = inter(colspans(A, "neg"), colspans(B, "neg")) + inter(colspans(A, "pos"), colspans(B, "pos"))
    ncol = lambda D: sum(1 for x in D.subs if x[3] != "normal")
    print(f"색    자동 {ncol(A)}줄({ncol(A)/max(1,len(A.subs))*100:.0f}%) · 완성 {ncol(B)}줄({ncol(B)/max(1,len(B.subs))*100:.0f}%) · "
          f"시간 정밀도 {p*100:.0f}% 재현율 {r*100:.0f}% F1 {f1*100:.0f} · 겹친 곳 빨/파 일치 {same_kind/max(1,ic)*100:.0f}%")

    # 확대
    def zspans(D):
        return [(s0, s0 + sd, z) for _, _, s0, sd, z in D.clips if z > 1.01]
    zA, zB = zspans(A), zspans(B)
    iz = inter([(a, b) for a, b, _ in zA], [(a, b) for a, b, _ in zB])
    p, r, f1 = prf(iz, total([(a, b) for a, b, _ in zA]), iz, total([(a, b) for a, b, _ in zB]))
    same_scale = sum(max(0, min(b, d) - max(a, c)) for a, b, x in zA for c, d, y in zB if abs(x - y) < 0.01)
    print(f"확대  자동 {total([(a,b) for a,b,_ in zA])/max(1,total(ka))*100:.0f}% 시간 · 완성 "
          f"{total([(a,b) for a,b,_ in zB])/max(1,total(kb))*100:.0f}% · 정밀도 {p*100:.0f}% 재현율 {r*100:.0f}% "
          f"F1 {f1*100:.0f} · 겹친 곳 배율 일치 {same_scale/max(1,iz)*100:.0f}%")

    # 카드
    sa = [A.src(t) for t in A.cards]
    sb = [B.src(t) for t in B.cards]
    sa = [x for x in sa if x is not None]
    sb = [x for x in sb if x is not None]
    hit_b = sum(1 for y in sb if any(abs(x - y) <= US for x in sa))
    hit_a = sum(1 for x in sa if any(abs(x - y) <= US for y in sb))
    p, r, f1 = prf(hit_a, len(sa), hit_b, len(sb))
    print(f"카드  자동 {len(sa)} · 완성 {len(sb)} · 같은 자리 정밀도 {p*100:.0f}% 재현율 {r*100:.0f}% F1 {f1*100:.0f}")

    # 효과음
    xa = [(A.src(t), n, t) for t, n in A.sfx]
    xb = [(B.src(t), n, t) for t, n in B.sfx]
    xa = [x for x in xa if x[0] is not None]
    xb = [x for x in xb if x[0] is not None]
    TOL = 300_000
    hit_b = [y for y in xb if any(abs(x[0] - y[0]) <= TOL for x in xa)]
    hit_a = [x for x in xa if any(abs(x[0] - y[0]) <= TOL for y in xb)]
    same = sum(1 for x in hit_a if any(abs(x[0] - y[0]) <= TOL and x[1] == y[1] for y in xb))
    p, r, f1 = prf(len(hit_a), len(xa), len(hit_b), len(xb))
    dur_a, dur_b = A.clips[-1][0] + A.clips[-1][1], B.clips[-1][0] + B.clips[-1][1]
    print(f"효과음 자동 {len(xa)}({len(xa)/(dur_a/US/60):.1f}/분) · 완성 {len(xb)}({len(xb)/(dur_b/US/60):.1f}/분) · "
          f"같은 자리 정밀도 {p*100:.0f}% 재현율 {r*100:.0f}% F1 {f1*100:.0f} · 그중 같은 소리 {same/max(1,len(hit_a))*100:.0f}%")

    if show:
        print("\n── 완성본 확대 중 자동이 놓친 곳 ──")
        for t0, dur, s0, sd, z in B.clips:
            if z > 1.01 and inter([(s0, s0 + sd)], [(a, b) for a, b, _ in zA]) < 0.3 * sd:
                txt = " / ".join(x[2] for x in B.subs if x[0] < t0 + dur and t0 < x[0] + x[1])
                print(f"  {f(t0)} x{z} {txt[:70]}")
        print("\n── 자동이 확대했는데 완성본은 안 한 곳 ──")
        for t0, dur, s0, sd, z in A.clips:
            if z > 1.01 and inter([(s0, s0 + sd)], [(a, b) for a, b, _ in zB]) < 0.3 * sd:
                txt = " / ".join(x[2] for x in A.subs if x[0] < t0 + dur and t0 < x[0] + x[1])
                print(f"  {f(t0)} x{z} {txt[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
