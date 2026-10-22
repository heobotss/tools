"""드래프트를 더 편집한 뒤, 비트를 다시 대사 자리에 붙인다.

캡컷에서 본 영상을 자르면 말은 앞으로 당겨지는데, 오버레이 트랙의 비트는
절대 시각에 남아 그대로 어긋난다. 실측(2026-08-18): 비트 9개가 0.82~2.72초씩
밀려 있었고 대표님이 하나씩 끌어다 맞추셔야 했다.

이 도구는 드래프트 안에 남겨둔 beats/plan.json 의 앵커 문구를 지금 자막에서
다시 찾아, 비트를 그 자리로 옮긴다. 영상·자막·효과음은 건드리지 않는다.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent.beats import read_subs, resolve

US = 1_000_000


def f(us: int) -> str:
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def main() -> int:
    draft = Path(sys.argv[1])
    plan_path = draft / "beats" / "plan.json"
    if not plan_path.exists():
        print(f"[에러] {plan_path} 가 없습니다 — add_beats.py 로 만든 드래프트여야 합니다.")
        return 1

    cpath = draft / "draft_content.json"
    content = json.loads(cpath.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    beat_tracks = [t for t in content["tracks"]
                   if t["type"] == "video" and any(
                       "beat" in (m.get("material_name", "") or "")
                       for m in content["materials"]["videos"]
                       for s in t.get("segments", [])
                       if m["id"] == s["material_id"])]
    if not beat_tracks:
        print("[에러] 비트 트랙을 못 찾았습니다.")
        return 1
    bt = beat_tracks[0]

    subs = read_subs(content)
    beats, missing = resolve(plan, subs, content["duration"])
    for m in missing:
        print(f"  ! 앵커 못 찾음: {m}")

    segs = sorted(bt["segments"], key=lambda s: s["target_timerange"]["start"])
    if len(segs) != len(beats):
        print(f"[주의] 비트 {len(segs)}개인데 계획은 {len(beats)}개입니다. "
              "지우신 게 있으면 그 줄을 plan.json 에서도 빼주세요.")
        return 1

    shutil.copy2(cpath, cpath.with_suffix(".json.before_resync"))
    moved = 0
    for s, b in zip(segs, beats):
        old = s["target_timerange"]["start"]
        if old != b.at_us or s["target_timerange"]["duration"] != b.dur_us:
            moved += 1
        print(f"  {f(old)} → {f(b.at_us)}  ({(b.at_us-old)/US:+.2f}s)  "
              f"{b.dur_us/US:.1f}s  {b.text[:26]}")
        s["target_timerange"] = {"start": b.at_us, "duration": b.dur_us}
        s["source_timerange"]["duration"] = min(
            s["source_timerange"].get("duration", b.dur_us), b.dur_us)

    cpath.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    print(f"\n{moved}개 옮김. 되돌리려면 draft_content.json.before_resync 를 쓰세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
