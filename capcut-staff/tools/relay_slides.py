"""이미 편집된 드래프트의 **장표 트랙만** 갈아끼운다.

    python tools/relay_slides.py "파트0_1편_OT형"
    python tools/relay_slides.py "파트0_1편_OT형" --out "파트0_1편_OT형_장표"

대표님이 캡컷에서 컷과 자막을 다시 잡으신 뒤에 쓰는 도구다.
처음부터 다시 만들면 그 작업이 통째로 날아가므로, **메인 영상 트랙과 자막
트랙은 손대지 않고** 장표 오버레이 트랙만 지우고 새로 깐다.

타임라인은 우리가 계산한 keeps 가 아니라 **드래프트의 메인 트랙에서 읽는다.**
컷이 다시 잡혔으니 그게 유일한 사실이다.

기본은 **새 폴더로 복사해서** 작업한다. 원본을 건드리지 않아야 되돌릴 수 있다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capcut_agent import asr as asr_mod
from capcut_agent import slides as sl
from capcut_agent.config import WORK_DIR, find_draft_root
from capcut_agent.draft import _extras_video, _fwd, _load, _uid
from capcut_agent.probe import probe

US = 1_000_000


def read_main_track(content: dict):
    """(원본 구간표, 원본 영상 경로). 메인 = 첫 번째 video 트랙."""
    vids = {m["id"]: m for m in content["materials"]["videos"]}
    track = next(t for t in content["tracks"] if t["type"] == "video")
    spans, path = [], ""
    for s in track["segments"]:
        st, tt = s["source_timerange"], s["target_timerange"]
        p = vids.get(s["material_id"], {}).get("path", "")
        if p and not path:
            path = p
        spans.append((st["start"], st["start"] + st["duration"],
                      tt["start"], tt["start"] + tt["duration"],
                      float(s.get("speed", 1.0))))
    return spans, path


def strip_overlay(content: dict) -> int:
    """장표 오버레이 트랙과 그 재료를 걷어낸다. 메인·자막은 안 건드린다."""
    video_tracks = [t for t in content["tracks"] if t["type"] == "video"]
    if len(video_tracks) < 2:
        return 0
    doomed = video_tracks[1:]

    mat_ids, extra_ids = set(), set()
    for t in doomed:
        for s in t["segments"]:
            mat_ids.add(s["material_id"])
            extra_ids.update(s.get("extra_material_refs") or [])

    mats = content["materials"]
    mats["videos"] = [m for m in mats["videos"] if m["id"] not in mat_ids]
    for cat, items in list(mats.items()):
        if isinstance(items, list):
            mats[cat] = [o for o in items
                         if not (isinstance(o, dict) and o.get("id") in extra_ids)]

    content["tracks"] = [t for t in content["tracks"] if t not in doomed]
    return sum(len(t["segments"]) for t in doomed)


def add_overlay(content: dict, clips, media) -> int:
    """새 장표 트랙을 얹는다. draft.build_draft 와 같은 방식으로 만든다."""
    if not clips:
        return 0
    material_tpl = _load("video_material.json")
    segment_tpl = _load("video_segment.json")
    mats = content["materials"]
    segs = []

    for ci, c in enumerate(sorted(clips, key=lambda x: x.start)):
        cm = json.loads(json.dumps(material_tpl, ensure_ascii=False))
        cm["id"] = _uid()
        cm["local_material_id"] = _uid()
        cm["path"] = _fwd(c.path)
        cm["material_name"] = Path(c.path).name
        cm["duration"] = c.source_dur
        cm["width"], cm["height"] = media.width, media.height
        cm["has_audio"] = False
        mats["videos"].append(cm)

        refs, buckets = _extras_video(1.0)
        for cat, obj in buckets.items():
            mats.setdefault(cat, []).append(obj)

        s = json.loads(json.dumps(segment_tpl, ensure_ascii=False))
        s["id"] = _uid()
        s["material_id"] = cm["id"]
        s["extra_material_refs"] = refs
        s["source_timerange"] = {"start": c.source_start,
                                 "duration": min(c.dur, c.source_dur)}
        s["target_timerange"] = {"start": c.start, "duration": c.dur}
        s["render_index"] = 1000 + ci        # 메인(0) 위, 자막(14000) 아래
        s["track_render_index"] = 1
        s["volume"] = 0.0
        s["last_nonzero_volume"] = 0.0
        if not c.fullscreen:
            s["clip"]["scale"] = {"x": c.scale, "y": c.scale}
            s["clip"]["transform"] = {"x": c.pos_x, "y": c.pos_y}
        segs.append(s)

    # 자막 트랙보다 앞에 끼워 넣는다 (캡컷이 트랙 순서를 층으로 읽는다)
    idx = next((i for i, t in enumerate(content["tracks"])
                if t["type"] == "text"), len(content["tracks"]))
    content["tracks"].insert(idx, {
        "id": _uid(), "type": "video", "flag": 0, "attribute": 0,
        "name": "", "is_default_name": True, "segments": segs,
    })
    return len(segs)


def capcut_running() -> bool:
    """캡컷이 떠 있는가.

    캡컷은 열어 둔 드래프트를 **메모리에 들고 있다가 종료할 때 다시 쓴다.**
    실행 중에 고치면 그 순간엔 맞지만, 캡컷을 끄는 순간 예전 내용으로 덮어써진다.
    실제로 그렇게 한 번 날아갔다 — 그래서 아예 못 돌게 막는다.
    """
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             timeout=30, errors="ignore").stdout.lower()
        return "capcut.exe" in out
    except (subprocess.SubprocessError, OSError):
        return False


async def run(name: str, out_name: str, deck_key: str, inplace: bool,
              force: bool = False, align_subs: bool = False) -> int:
    # 위험한 건 **실행 중인 캡컷이 들고 있는 드래프트를 직접 고치는 것**뿐이다.
    # 캡컷은 끌 때 메모리에 있던 예전 내용을 다시 써서 수정을 덮어쓴다.
    # 새 폴더로 복사하는 건 캡컷이 모르는 폴더라 안전하다.
    if inplace and capcut_running() and not force:
        print('캡컷이 실행 중이라 원본을 직접 고칠 수 없습니다.')
        print('  캡컷을 완전히 종료하거나, --out 으로 새 드래프트를 만드세요.')
        return 1
    if capcut_running():
        print('! 캡컷 실행 중 — 마지막 저장분을 기준으로 복사합니다.')
    root = find_draft_root()
    src = root / name
    if not (src / "draft_content.json").exists():
        print(f"드래프트를 못 찾았습니다: {src}")
        return 1

    dest = src if inplace else root / (out_name or f"{name}_장표")
    if not inplace:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

    content = json.loads((dest / "draft_content.json").read_text(encoding="utf-8"))
    spans, path = read_main_track(content)
    if not spans:
        print("메인 영상 트랙이 비어 있습니다.")
        return 1

    video = Path(path)
    if not video.exists():
        print(f"원본 영상을 못 찾았습니다: {video}")
        return 1
    media = probe(video)

    tr, _ = await asr_mod.transcribe(video, language="ko",
                                     duration_us=media.duration_us)
    deck = sl.get_deck(deck_key) if deck_key else sl.deck_for(tr)
    if deck is None:
        print("어느 강의인지 못 알아봤습니다. --deck 로 지정하세요.")
        return 1

    cues, sents = deck.load()
    resolved = sl.resolve(cues, sents, tr)

    speak0 = tr.segs[0].start if tr.segs else 0
    speak1 = tr.segs[-1].end if tr.segs else media.duration_us
    zone = sl.measure_safe_zone(video, media.duration_us,
                                start_us=speak0, end_us=speak1)

    # 컷 경계와 자막 경계 — 장표를 여기에 물린다
    cut_edges, sub_edges = [], []
    for a, b, t0, t1, _sp in spans:
        cut_edges += [t0, t1]

    # 자막을 먼저 컷에 맞춘다. 안 하면 장표가 컷에 맞으면 자막과 어긋나고
    # 자막에 맞추면 컷과 어긋난다 — 셋이 동시에 맞을 수가 없다.
    if align_subs:
        for trk in content["tracks"]:
            if trk["type"] == "text":
                n, avg = sl.align_subtitles(trk, cut_edges)
                if n:
                    print(f"자막 {n}개를 컷에 맞췄습니다 (평균 {avg:.3f}초 이동)")
    for trk in content["tracks"]:
        if trk["type"] == "text":
            for g in trk["segments"]:
                rng = g["target_timerange"]
                sub_edges += [rng["start"], rng["start"] + rng["duration"]]

    plans, warns = sl.plan_slides(resolved, deck.slide_dir, None, 1.0, zone,
                                  media, mapper=sl.mapper_from_spans(spans),
                                  snap=sl.make_snapper(cut_edges, sub_edges))
    clips = sl.build_clips(plans, media, WORK_DIR / "slides" / deck.key)

    removed = strip_overlay(content)
    added = add_overlay(content, clips, media)
    (dest / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8")

    meta_p = dest / "draft_meta_info.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["draft_fold_path"] = _fwd(dest)
    meta["draft_name"] = dest.name
    if not inplace:
        meta["draft_id"] = _uid()
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    total = max(x[3] for x in spans)
    print(f"\n=== {dest.name}   ({deck.label})")
    print(f"타임라인 {total/US/60:.2f}분 · 메인 {len(spans)}컷 (건드리지 않음)")
    print(f"빈 자리 {zone.x0:.0%}~{zone.x1:.0%} ({zone.detail})")
    print(f"장표 트랙: {removed}개 → {added}개\n")
    for w in warns:
        print(f"  ! {w}")

    print(f"\n{'시각':>8} {'길이':>7}  {'화면':<10} 라벨")
    print("-" * 64)
    for p in sorted(plans, key=lambda x: x.start):
        kind = "전체화면" if p.fullscreen else "패널"
        print(f"{p.start/US//60:>4.0f}:{p.start/US%60:05.2f} "
              f"{p.dur/US:>6.1f}s  {kind:<10} {p.cue.label}  「{p.cue.phrase}」")
    gaps = sum(p.dur for p in plans)
    print("-" * 64)
    print(f"장표가 떠 있는 비율 {gaps/total:.0%}")
    print(f"\n캡컷을 완전히 껐다 켜면 목록에 뜹니다: {dest.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="편집된 드래프트의 장표만 다시 깔기")
    ap.add_argument("draft", help="드래프트 폴더 이름")
    ap.add_argument("--out", default="", help="새 드래프트 이름")
    ap.add_argument("--deck", default="", help="덱 키 (기본: 대본 자동 판별)")
    ap.add_argument("--inplace", action="store_true",
                    help="복사하지 않고 원본을 직접 고침")
    ap.add_argument("--align-subs", action="store_true",
                    help="자막 경계를 컷에 맞춘 뒤 장표를 깐다 (셋 다 일치)")
    ap.add_argument("--force", action="store_true",
                    help="캡컷이 실행 중이어도 강행 (권장하지 않음)")
    a = ap.parse_args()
    return asyncio.run(run(a.draft, a.out, a.deck, a.inplace, a.force,
                       a.align_subs))


if __name__ == "__main__":
    raise SystemExit(main())
