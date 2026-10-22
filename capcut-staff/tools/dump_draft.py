"""완성 드래프트의 **모든 요소**를 한 줄씩 뽑는다 — 편집 규칙 역산용.

    python tools/dump_draft.py 풀링4 [--json out.json]

트랙마다 세그먼트를 타임라인 순서로:
  · 본 영상   원본 구간·배속·확대(scale)·위치(x,y)·키프레임·볼륨·전환·애니메이션
  · 겹친 영상 소재 이름(비트 카드·사진 등)·확대·위치
  · 글자      글·색·글꼴·크기·위치·애니메이션·말풍선/템플릿 여부
  · 소리      소재 이름·경로·볼륨·페이드
  · 필터/효과 이름·구간
그리고 materials 의 종류별 개수 (쓰이는 기능 목록).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

US = 1_000_000
DRAFTS = Path(r"C:\Users\허재윤\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft")


def f(us):
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def load(name: str) -> dict:
    p = Path(name)
    folder = p if p.is_dir() else DRAFTS / name
    return json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))


def index(d: dict) -> dict[str, tuple[str, dict]]:
    """material id → (종류, material)."""
    out = {}
    for cat, items in d["materials"].items():
        if isinstance(items, list):
            for m in items:
                if isinstance(m, dict) and "id" in m:
                    out[m["id"]] = (cat, m)
    return out


def text_info(m: dict) -> dict:
    try:
        c = json.loads(m.get("content", "{}"))
    except ValueError:
        c = {}
    st = (c.get("styles") or [{}])
    s0 = st[0]
    col = (((s0.get("fill") or {}).get("content") or {}).get("solid") or {}).get("color")
    return {
        "text": c.get("text", ""),
        "color": [round(x, 3) for x in col] if col else None,
        "n_styles": len(st),
        "colors": sorted({tuple(round(x, 2) for x in (((s.get("fill") or {}).get("content") or {})
                                                    .get("solid") or {}).get("color", []))
                          for s in st}),
        "size": s0.get("size"),
        "font": Path((s0.get("font") or {}).get("path", "")).name,
        "bold": s0.get("bold"),
        "stroke": bool(s0.get("strokes")),
        "shadow": m.get("has_shadow"),
        "bg": m.get("background_alpha"),
        "type": m.get("type"),
    }


def seg_row(s: dict, idx: dict) -> dict:
    tr, sr = s["target_timerange"], s.get("source_timerange") or {}
    clip = s.get("clip") or {}
    refs = [idx[r][0] + ":" + (idx[r][1].get("name") or idx[r][1].get("type") or "")
            for r in s.get("extra_material_refs", []) if r in idx]
    refs = [r for r in refs if not r.startswith(("speeds", "canvases", "sound_channel_mappings",
                                                 "placeholder_infos", "vocal_separations",
                                                 "material_colors", "loudnesses"))]
    return {
        "t0": tr["start"], "dur": tr["duration"],
        "s0": sr.get("start"), "sdur": sr.get("duration"),
        "speed": round(s.get("speed", 1), 4),
        "scale": round((clip.get("scale") or {}).get("x", 1), 4),
        "x": round((clip.get("transform") or {}).get("x", 0), 4),
        "y": round((clip.get("transform") or {}).get("y", 0), 4),
        "rot": clip.get("rotation", 0),
        "vol": s.get("volume"),
        "kf": [(k.get("property_type"), len(k.get("keyframe_list", [])))
               for k in s.get("common_keyframes", [])],
        "refs": refs,
        "visible": s.get("visible", True),
        "render_index": s.get("render_index"),
    }


def dump(name: str) -> dict:
    d = load(name)
    idx = index(d)
    tracks = []
    for ti, t in enumerate(d["tracks"]):
        rows = []
        for s in sorted(t.get("segments", []), key=lambda s: s["target_timerange"]["start"]):
            r = seg_row(s, idx)
            cat, m = idx.get(s["material_id"], ("?", {}))
            r["cat"] = cat
            r["name"] = m.get("material_name") or m.get("name") or ""
            r["path"] = m.get("path", "")
            if cat == "texts":
                r.update(text_info(m))
            if cat in ("effects", "video_effects", "filters"):
                r["name"] = m.get("name", r["name"])
            rows.append(r)
        tracks.append({"i": ti, "type": t["type"], "attribute": t.get("attribute"),
                       "flag": t.get("flag"), "rows": rows})
    mats = {k: len(v) for k, v in d["materials"].items() if isinstance(v, list) and v}
    return {"name": name, "duration": d["duration"],
            "canvas": d.get("canvas_config"), "tracks": tracks, "materials": mats}


def main() -> int:
    name = sys.argv[1]
    out = dump(name)
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"■ {name} · {f(out['duration'])} · 캔버스 {out['canvas']}")
    print("  materials:", out["materials"])
    for t in out["tracks"]:
        rows = t["rows"]
        cats = Counter(r["cat"] for r in rows)
        print(f"\n  트랙 {t['i']} {t['type']} · {len(rows)}개 · {dict(cats)}")
        if not rows:
            continue
        sc = Counter(r["scale"] for r in rows)
        print(f"    scale 분포 {dict(sc.most_common(8))}")
        refs = Counter(x.split(":")[0] for r in rows for x in r["refs"])
        if refs:
            print(f"    붙은 기능 {dict(refs)}")
        kf = Counter(k[0] for r in rows for k in r["kf"])
        if kf:
            print(f"    키프레임 {dict(kf)}")
        names = Counter(r["name"] for r in rows)
        if t["type"] != "video" or len(names) < 30:
            print(f"    소재 {dict(names.most_common(12))}")
        if t["type"] == "text":
            print(f"    색 {dict(Counter(str(r.get('color')) for r in rows))}")
            print(f"    글꼴·크기 {dict(Counter((r.get('font'), r.get('size')) for r in rows))}")
            print(f"    위치 {dict(Counter((r['x'], r['y'], r['scale']) for r in rows).most_common(5))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
