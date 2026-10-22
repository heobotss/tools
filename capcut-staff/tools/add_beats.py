"""이미 있는 드래프트를 **건드리지 않고**, 복사본에 비트를 얹는다.

대표님이 손질을 끝낸 파일은 그대로 두어야 한다. 그래서 처음부터 다시 만들지
않고 폴더를 통째로 복사한 뒤, 오버레이 트랙 하나만 더한다.
컷·자막·확대 등 대표님이 하신 작업은 손대지 않는다.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent.beats import read_subs, render, resolve, to_clip
from capcut_agent.draft import _extras_video, _fwd, _load, _uid

US = 1_000_000


def f(us: int) -> str:
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def main() -> int:
    src = Path(sys.argv[1])
    new_name = sys.argv[2]
    plan = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))

    dst = src.parent / new_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"복사  {src.name}  →  {dst.name}")

    content = json.loads((dst / "draft_content.json").read_text(encoding="utf-8"))

    # 이미 비트가 깔려 있으면 그 트랙만 걷어낸다. 대표님이 얹으신 효과음·확대·
    # 색 자막은 그대로 둔다 — 손대야 할 건 비트뿐이다.
    beat_ids = {m["id"] for m in content["materials"]["videos"]
                if "beat" in (m.get("material_name") or "")}
    if beat_ids:
        before = len(content["tracks"])
        content["tracks"] = [
            t for t in content["tracks"]
            if not (t["type"] == "video"
                    and t.get("segments")
                    and all(sg["material_id"] in beat_ids for sg in t["segments"]))]
        print(f"  기존 비트 트랙 {before - len(content['tracks'])}개 걷어냄")
    cw = content["canvas_config"]["width"]
    ch = content["canvas_config"]["height"]
    fps = content.get("fps", 30.0)
    total = content["duration"]

    # 절대 시각이 아니라 **자막 문구**로 자리를 잡는다. 시각으로 박아두면
    # 대표님이 뒤에 컷을 더 하실 때 그대로 어긋난다.
    subs = read_subs(content)
    beats, missing = resolve(plan, subs, total)
    for m in missing:
        print(f"  ! 앵커 못 찾음 — 건너뜀: {m}")

    out_dir = dst / "beats"
    material_tpl = _load("video_material.json")
    segment_tpl = _load("video_segment.json")
    mats = content["materials"]

    segs = []
    for i, b in enumerate(beats):
        png = render(b, cw, ch, out_dir / f"beat{i:02d}.png")
        mp4 = to_clip(png, b.dur_us, cw, ch, fps, out_dir / f"beat{i:02d}.mp4")

        cm = json.loads(json.dumps(material_tpl, ensure_ascii=False))
        cm["id"] = _uid()
        cm["local_material_id"] = str(uuid.uuid4())
        cm["path"] = _fwd(mp4)
        cm["material_name"] = mp4.name
        cm["duration"] = b.dur_us
        cm["width"], cm["height"] = cw, ch
        cm["has_audio"] = False
        mats["videos"].append(cm)

        refs, buckets = _extras_video(1.0)
        for cat, obj in buckets.items():
            mats.setdefault(cat, []).append(obj)

        cs = json.loads(json.dumps(segment_tpl, ensure_ascii=False))
        cs["id"] = _uid()
        cs["material_id"] = cm["id"]
        cs["extra_material_refs"] = refs
        cs["source_timerange"] = {"start": 0, "duration": b.dur_us}
        cs["target_timerange"] = {"start": b.at_us, "duration": b.dur_us}
        # 본 영상(0) 위, 자막(14000) 아래 — 자막은 계속 보여야 한다
        cs["render_index"] = 1000 + i
        cs["track_render_index"] = 1
        cs["volume"] = 0.0
        cs["last_nonzero_volume"] = 0.0
        segs.append(cs)
        print(f"  비트 {i+1}  {f(b.at_us)}  {b.dur_us/US:.1f}s  "
              f"{b.label + ' / ' if b.label else ''}{b.text}")

    content["tracks"].append({
        "id": _uid(), "type": "video", "flag": 0, "attribute": 0,
        "name": "", "is_default_name": True, "segments": segs,
    })
    (dst / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8")

    meta = json.loads((dst / "draft_meta_info.json").read_text(encoding="utf-8"))
    meta["draft_id"] = str(uuid.uuid4()).upper()
    meta["draft_name"] = new_name
    meta["draft_fold_path"] = _fwd(dst)
    meta["draft_root_path"] = _fwd(dst.parent)
    (dst / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 나중에 다시 붙일 수 있게 계획을 드래프트 안에 남긴다.
    # 대표님이 컷을 더 하시면 tools/resync_beats.py 가 이걸 보고 되맞춘다.
    (out_dir / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    # 캡컷이 백업본을 먼저 읽어 예전 상태로 되돌리는 일이 있다
    for stale in ("draft_content.json.bak", "template-2.tmp"):
        (dst / stale).unlink(missing_ok=True)

    print(f"\n비트 {len(beats)}개 · 총 {sum(b.dur_us for b in beats)/US:.1f}초 "
          f"(영상 {total/US:.1f}초의 {sum(b.dur_us for b in beats)/total*100:.1f}%)")
    print(f"드래프트  {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
