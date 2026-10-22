"""대표님 완성본은 **그대로 두고** 빠진 차례 카드만 얹는다.

    python tools/add_section_cards.py "<완성본 이름>" "<새 이름>" [--dry]

대표님이 손질을 끝낸 드래프트에는 컷·자막·색·확대·효과음·음성 보정이 모두 들어 있다.
처음부터 다시 만들면 그게 전부 날아간다 (2026-09-25 대표님 지적:
"내가 수정해놓은 것들까지 다시 이전으로 돌아가서 이상하게 만들어 놓았다").

그래서 여기서는 **카드 트랙에 세그먼트만 더한다.** 자막·컷·소리는 한 글자도 안 건드린다.
차례 카드(첫 번째·두 번째…)는 밑 자막을 내리지 않는 종류라(beats.hides_subtitle),
자막을 지울 일도 없다.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from capcut_agent import beats as B          # noqa: E402
from capcut_agent import slides as S         # noqa: E402
from capcut_agent.config import WORK_DIR     # noqa: E402
from capcut_agent.draft import _extras_video, _fwd, _uid  # noqa: E402

US = 1_000_000
DRAFTS = Path(r"C:\Users\허재윤\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft")
NEAR_US = 3_000_000        # 이 안에 이미 카드가 있으면 새로 넣지 않는다


def f(us):
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def main() -> int:
    src = DRAFTS / sys.argv[1]
    dst = DRAFTS / sys.argv[2]
    dry = "--dry" in sys.argv

    c = json.loads((src / "draft_content.json").read_text(encoding="utf-8"))
    cw, ch = c["canvas_config"]["width"], c["canvas_config"]["height"]
    fps = c.get("fps", 30.0)
    vids = {m["id"]: m for m in c["materials"]["videos"]}
    vtracks = [t for t in c["tracks"] if t["type"] == "video" and t.get("segments")]
    main_t = max(vtracks, key=lambda t: len(t["segments"]))
    card_tracks = [t for t in vtracks if t is not main_t]
    bt = card_tracks[0] if card_tracks else None
    if bt is None:
        print("[에러] 카드 트랙이 없습니다 — 이 도구는 카드가 이미 있는 완성본용입니다.")
        return 1

    existing = sorted((s["target_timerange"]["start"],
                       s["target_timerange"]["start"] + s["target_timerange"]["duration"])
                      for s in bt["segments"])
    subs = B.read_subs(c)
    total = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                for s in main_t["segments"])

    plan = B.pick(subs, total)
    want = [p for p in plan if p.get("label")]
    print(f"{src.name} · 자막 {len(subs)}줄 · 기존 카드 {len(existing)}장 · 차례 후보 {len(want)}개\n")

    todo = []
    for p in want:
        at = p.get("at") or B.find_anchor(subs, p["anchor"])
        if at is None:
            print(f"  · 못 찾음(앵커) {p['label']} | {p['text']}")
            continue
        dur, n = B.span(subs, at, p["text"])
        end = at + dur
        clash = [e for e in existing if e[0] < end + NEAR_US and at - NEAR_US < e[1]]
        if clash:
            print(f"  · 이미 카드 있음 {f(at)} {p['label']} | {p['text']}")
            continue
        todo.append((at, dur, n, p))
        existing.append((at, end))
        existing.sort()

    print(f"\n새로 넣을 차례 카드 {len(todo)}장")
    for at, dur, n, p in todo:
        print(f"  + {f(at)} {dur/US:4.2f}s ({n}줄)  [{p['label']}] {p['text']}")
    if dry or not todo:
        return 0

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    c = json.loads((dst / "draft_content.json").read_text(encoding="utf-8"))
    vids = {m["id"]: m for m in c["materials"]["videos"]}
    vtracks = [t for t in c["tracks"] if t["type"] == "video" and t.get("segments")]
    main_t = max(vtracks, key=lambda t: len(t["segments"]))
    bt = [t for t in vtracks if t is not main_t][0]
    tmpl_seg = bt["segments"][0]
    tmpl_mat = vids[tmpl_seg["material_id"]]

    out = WORK_DIR / "beats" / dst.name
    clip_us = int(S.CLIP_LEN_S * US)
    pad_us = int(S.CLIP_PAD_S * US)
    for i, (at, dur, n, p) in enumerate(todo):
        b = B.Beat(at_us=at, dur_us=dur, text=p["text"], label=p["label"], span_lines=n)
        png = B.render(b, cw, ch, out / f"sec{i:02d}.png")
        mp4 = S.to_clip(png, clip_us, cw, ch, fps, out / f"sec{i:02d}.mp4")

        m = json.loads(json.dumps(tmpl_mat, ensure_ascii=False))
        m["id"] = _uid()
        m["local_material_id"] = str(uuid.uuid4())
        m["path"] = _fwd(mp4)
        m["material_name"] = mp4.name
        m["duration"] = clip_us
        c["materials"]["videos"].append(m)

        refs, buckets = _extras_video(1.0)
        for cat, obj in buckets.items():
            c["materials"].setdefault(cat, []).append(obj)

        sg = json.loads(json.dumps(tmpl_seg, ensure_ascii=False))
        sg["id"] = _uid()
        sg["material_id"] = m["id"]
        sg["extra_material_refs"] = refs
        sg["source_timerange"] = {"start": min(pad_us, max(0, clip_us - dur)),
                                  "duration": dur}
        sg["target_timerange"] = {"start": at, "duration": dur}
        sg["render_index"] = 1000 + len(bt["segments"])
        sg["volume"] = 0.0
        sg["last_nonzero_volume"] = 0.0
        bt["segments"].append(sg)

    bt["segments"].sort(key=lambda s: s["target_timerange"]["start"])
    (dst / "draft_content.json").write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    meta = json.loads((dst / "draft_meta_info.json").read_text(encoding="utf-8"))
    meta["draft_id"] = str(uuid.uuid4()).upper()
    meta["draft_name"] = dst.name
    meta["draft_fold_path"] = _fwd(dst)
    meta["draft_root_path"] = _fwd(dst.parent)
    # 시각을 안 바꾸면 캡컷 목록에서 원본과 같은 자리에 묻혀 안 보인다 (2026-09-28 실측).
    # 클라우드 식별자도 원본 것을 그대로 물려받으면 같은 항목으로 취급될 수 있어 비운다.
    now = int(time.time() * 1_000_000)
    meta["tm_draft_create"] = now
    meta["tm_draft_modified"] = now
    for k in list(meta):
        if k.startswith("tm_draft_cloud"):
            meta[k] = "" if isinstance(meta[k], str) else 0
    (dst / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    for stale in ("draft_content.json.bak", "template-2.tmp"):
        (dst / stale).unlink(missing_ok=True)

    # ── 검증: 카드 말고는 아무것도 안 바뀌었나 ──
    a = json.loads((src / "draft_content.json").read_text(encoding="utf-8"))
    def shape(d):
        return [(t["type"], len(t.get("segments", []))) for t in d["tracks"]]
    print(f"\n원본 트랙 {shape(a)}\n새 파일  {shape(c)}")
    print(f"자막 {'그대로' if B.read_subs(a) == B.read_subs(c) else '달라짐!'} · "
          f"길이 {a['duration']/US:.3f}s → {c['duration']/US:.3f}s · "
          f"카드 {len(existing) - len(todo)} → {len(bt['segments'])}장")
    print(f"드래프트  {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
