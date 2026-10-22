"""완성본에서 편집 기준을 뽑아낸다.

대표님이 캡컷에서 끝까지 다듬은 드래프트를 읽어, 장표·자막·컷이 실제로 어떻게
놓여 있는지를 수치로 뽑는다. 여기서 나온 값이 `slides.py` 의 기본값이 된다.

    python tools/learn_style.py "파트0_1편_OT형" "파트0_2편_키형_v5" "수강오리엔테이션_v8"

추정하지 않는다. 완성본이 곧 정답이다.
"""
from __future__ import annotations

import bisect
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capcut_agent.config import find_draft_root

US = 1_000_000
SAME = 40_000          # 이만큼 가까우면 같은 자리로 본다


def load(name):
    root = find_draft_root()
    return json.loads((root / name / "draft_content.json").read_text(encoding="utf-8"))


def near(arr, x):
    i = bisect.bisect_left(arr, x)
    best = float("inf")
    for j in (i - 1, i):
        if 0 <= j < len(arr):
            best = min(best, abs(arr[j] - x))
    return best


def analyse(name):
    c = load(name)
    vids = {m["id"]: m for m in c["materials"]["videos"]}
    vt = [t for t in c["tracks"] if t["type"] == "video"]
    tt = [t for t in c["tracks"] if t["type"] == "text"]
    main, over = vt[0]["segments"], (vt[1]["segments"] if len(vt) > 1 else [])
    subs_seg = tt[0]["segments"] if tt else []

    total = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                for s in main)
    cuts = sorted({s["target_timerange"]["start"] for s in main} |
                  {s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                   for s in main})
    subs = sorted({s["target_timerange"]["start"] for s in subs_seg} |
                  {s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                   for s in subs_seg})

    # 장표: 종류별 첫 등장 / 리드 / 패널
    segs = sorted(over, key=lambda s: s["target_timerange"]["start"])
    seen, starts, leadends, holds, leads = set(), [], [], {}, []
    panel_geo, cover, fulltime = [], 0, 0
    for s in segs:
        n = int(re.match(r"slide(\d+)", vids[s["material_id"]]["material_name"]).group(1))
        r = s["target_timerange"]
        cl = s.get("clip", {})
        full = cl.get("scale", {}).get("x", 1.0) > 0.9
        cover += r["duration"]
        if full:
            fulltime += r["duration"]
        else:
            panel_geo.append((round(cl.get("scale", {}).get("x", 1.0), 3),
                              round(cl.get("transform", {}).get("x", 0.0), 3),
                              round(cl.get("transform", {}).get("y", 0.0), 3)))
        if n not in seen:
            starts.append(r["start"])
            holds[n] = 0
            if full:
                leads.append(r["duration"])
                leadends.append(r["start"] + r["duration"])
        seen.add(n)
        holds[n] = holds.get(n, 0) + r["duration"]

    def pct(vals, arr):
        if not vals:
            return "-"
        ok = sum(1 for v in vals if near(arr, v) <= SAME)
        return f"{ok}/{len(vals)} ({ok/len(vals):.0%})"

    cut_starts = sorted({s["target_timerange"]["start"] for s in main})
    sub_starts = sorted({s["target_timerange"]["start"] for s in subs_seg})

    print(f"\n{'='*68}\n{name}   {total/US/60:.2f}분")
    print(f"  컷 {len(main)} · 장표 {len(holds)}종({len(segs)}조각) · 자막 {len(subs_seg)}")
    print(f"\n  [이음매]")
    print(f"    장표 등장  → 컷 {pct(starts, cuts)} · 자막 {pct(starts, subs)}")
    print(f"    리드 끝    → 컷 {pct(leadends, cuts)} · 자막 {pct(leadends, subs)}")
    print(f"    컷 시작    → 자막 {pct(cut_starts, sub_starts)}")
    print(f"\n  [장표]")
    h = [v/US for v in holds.values()]
    print(f"    한 장 유지  중앙 {statistics.median(h):.1f}s · 최장 {max(h):.1f}s "
          f"· 25s 초과 {sum(1 for x in h if x>25)}개")
    print(f"    화면 점유  {cover/total:.0%} · 그중 전체화면 {fulltime/cover:.0%}")
    if leads:
        L = sorted(v/US for v in leads)
        print(f"    전체화면 리드  {len(leads)}/{len(holds)}장 · 중앙 {statistics.median(L):.2f}s "
              f"· {L[0]:.2f}~{L[-1]:.2f}s")
    if panel_geo:
        from collections import Counter
        g, cnt = Counter(panel_geo).most_common(1)[0]
        print(f"    패널 좌표  scale {g[0]} · x {g[1]:+} · y {g[2]:+}  ({cnt}/{len(panel_geo)})")
    print(f"\n  [컷·자막]")
    cd = sorted(s["target_timerange"]["duration"]/US for s in main)
    sd = sorted(s["target_timerange"]["duration"]/US for s in subs_seg)
    print(f"    컷 길이   중앙 {statistics.median(cd):.2f}s · {cd[0]:.2f}~{cd[-1]:.2f}s")
    print(f"    자막 길이 중앙 {statistics.median(sd):.2f}s · {sd[0]:.2f}~{sd[-1]:.2f}s")
    return dict(leads=[v/US for v in leads], holds=h, geo=panel_geo,
                start_cut=[near(cuts, v) for v in starts],
                lead_cut=[near(cuts, v) for v in leadends],
                nolead=len(holds)-len(leads), nslides=len(holds))


def main():
    names = sys.argv[1:]
    if not names:
        print(__doc__)
        return 1
    all_leads, all_holds, all_geo = [], [], []
    nolead = nslides = 0
    for n in names:
        r = analyse(n)
        all_leads += r["leads"]; all_holds += r["holds"]; all_geo += r["geo"]
        nolead += r["nolead"]; nslides += r["nslides"]

    from collections import Counter
    print(f"\n{'='*68}\n세 편 합산 — 이 값을 기본값으로 쓴다\n")
    print(f"  장표 한 장 유지    중앙 {statistics.median(all_holds):.1f}초")
    print(f"  전체화면 리드      중앙 {statistics.median(all_leads):.2f}초 "
          f"({min(all_leads):.2f}~{max(all_leads):.2f})")
    print(f"  리드 없는 장표     {nolead}/{nslides} ({nolead/nslides:.0%})")
    g, cnt = Counter(all_geo).most_common(1)[0]
    print(f"  패널 좌표          scale {g[0]} · x {g[1]:+} · y {g[2]:+} "
          f"({cnt}/{len(all_geo)} = {cnt/len(all_geo):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
