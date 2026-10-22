"""생성한 드래프트를 캡컷이 실제로 저장한 드래프트와 대조 검증한다.

빌드 성공 != 검증. 이건 "캡컷에서 열어볼 가치가 있는가"까지만 확인해준다.
최종 검증은 사람이 캡컷에서 직접 재생하는 것.

    python tools/verify_draft.py "<드래프트폴더>" [--ref "<기준드래프트폴더>"]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

US = 1_000_000
ok_n = 0
fail: list[str] = []
warn: list[str] = []


def check(cond: bool, msg: str) -> None:
    global ok_n
    if cond:
        ok_n += 1
    else:
        fail.append(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--ref", help="비교 기준이 될 캡컷 저장 드래프트 폴더")
    a = ap.parse_args()

    d = Path(a.folder)
    cf, mf = d / "draft_content.json", d / "draft_meta_info.json"
    if not cf.exists() or not mf.exists():
        print(f"[실패] 필수 파일 없음: {cf.name} / {mf.name}")
        return 1

    c = json.loads(cf.read_text(encoding="utf-8"))
    m = json.loads(mf.read_text(encoding="utf-8"))

    # ── 1. 타임라인 무결성 ──
    tracks = c["tracks"]
    check(len(tracks) >= 1, "트랙이 없음")
    vt = next((t for t in tracks if t["type"] == "video"), None)
    check(vt is not None, "비디오 트랙 없음")
    segs = vt["segments"] if vt else []
    check(len(segs) > 0, "세그먼트 0개")

    cursor = 0
    for i, s in enumerate(segs):
        tr, sr = s["target_timerange"], s["source_timerange"]
        spd = float(s.get("speed", 1.0)) or 1.0
        check(tr["start"] == cursor,
              f"세그먼트 {i}: 타임라인에 틈/겹침 (start={tr['start']}, 기대={cursor})")
        # 배속이 걸리면 타임라인 길이 = 원본 길이 / 배속 이어야 한다.
        # 이게 어긋나면 영상이 밀리거나 잘린다.
        expect = sr["duration"] / spd
        check(abs(tr["duration"] - expect) <= 2,
              f"세그먼트 {i}: 배속 {spd} 인데 target({tr['duration']}) != "
              f"source/배속({expect:.0f})")
        check(tr["duration"] > 0, f"세그먼트 {i}: 길이 0")
        cursor += tr["duration"]

    check(c["duration"] == cursor, f"content.duration({c['duration']}) != 실제 합({cursor})")
    check(m["tm_duration"] == cursor,
          f"tm_duration({m['tm_duration']}) != 타임라인 길이({cursor}) — 캡컷이 길이를 잘못 잡음")

    # ── 2. 참조 무결성 ──
    mats = c["materials"]
    vid_ids = {v["id"] for v in mats["videos"]}
    all_ids: set[str] = set()
    for lst in mats.values():
        if isinstance(lst, list):
            for it in lst:
                if isinstance(it, dict) and "id" in it:
                    all_ids.add(it["id"])

    for i, s in enumerate(segs):
        check(s["material_id"] in vid_ids, f"세그먼트 {i}: material_id 참조 깨짐")
        missing = [r for r in s["extra_material_refs"] if r not in all_ids]
        check(not missing, f"세그먼트 {i}: extra_material_refs 참조 깨짐 {missing[:2]}")
        check(len(s["extra_material_refs"]) == 7,
              f"세그먼트 {i}: extra refs {len(s['extra_material_refs'])}개 (기대 7)")

    # id 중복 검사
    flat = [it["id"] for lst in mats.values() if isinstance(lst, list)
            for it in lst if isinstance(it, dict) and "id" in it]
    check(len(flat) == len(set(flat)), "material id 중복 있음")

    # ── 3. 원본 파일 실존 ──
    for v in mats["videos"]:
        p = Path(v["path"])
        check(p.exists(), f"원본 파일 없음: {v['path']}")
        check(v["duration"] >= cursor, "material duration 이 타임라인보다 짧음")
        break

    # source_timerange 가 원본 길이를 넘지 않는지
    src_dur = mats["videos"][0]["duration"] if mats["videos"] else 0
    for i, s in enumerate(segs):
        sr = s["source_timerange"]
        check(sr["start"] + sr["duration"] <= src_dur,
              f"세그먼트 {i}: 원본 길이를 벗어남")

    # ── 3.5 자막 트랙 ──
    tt = next((t for t in tracks if t["type"] == "text"), None)
    n_sub = 0
    longest = 0
    sub_info = ""
    if tt is not None:
        tsegs = tt["segments"]
        n_sub = len(tsegs)
        text_ids = {t["id"] for t in mats.get("texts", [])}
        prev_end = -1
        for i, s in enumerate(tsegs):
            tr_ = s["target_timerange"]
            check(s["material_id"] in text_ids, f"자막 {i}: text material 참조 깨짐")
            check(all(r in all_ids for r in s["extra_material_refs"]),
                  f"자막 {i}: extra_material_refs 참조 깨짐")
            check(tr_["duration"] > 0, f"자막 {i}: 길이 0")
            check(tr_["start"] >= prev_end,
                  f"자막 {i}: 앞 자막과 겹침 (start={tr_['start']}, 앞 끝={prev_end})")
            check(tr_["start"] + tr_["duration"] <= cursor,
                  f"자막 {i}: 타임라인 끝을 넘어감")
            prev_end = tr_["start"] + tr_["duration"]

        # 자막 커버리지 — 빈틈 없이 이어지는가
        covered = sum(s["target_timerange"]["duration"] for s in tsegs)
        gaps = sum(1 for i in range(len(tsegs) - 1)
                   if tsegs[i + 1]["target_timerange"]["start"] >
                   tsegs[i]["target_timerange"]["start"] + tsegs[i]["target_timerange"]["duration"])
        head = tsegs[0]["target_timerange"]["start"] if tsegs else 0
        tail = cursor - prev_end
        sub_info = (f"자막 커버리지 {covered / cursor * 100:.1f}%  "
                    f"빈틈 {gaps}곳  앞 {head / US:.2f}s  뒤 {tail / US:.2f}s")

        for i, t in enumerate(mats["texts"]):
            try:
                parsed = json.loads(t["content"])
            except (json.JSONDecodeError, TypeError):
                fail.append(f"text material {i}: content 가 올바른 JSON 이 아님")
                continue
            check(bool(parsed.get("text", "").strip()),
                  f"text material {i}: 빈 자막")
            check("\n" not in parsed.get("text", ""),
                  f"text material {i}: 자막에 줄바꿈이 들어감 — 한 줄이어야 함")
            longest = max(longest, len(parsed.get("text", "")))
            styles = parsed.get("styles") or []
            check(bool(styles), f"text material {i}: styles 없음")
            if styles:
                rng = styles[0].get("range", [0, 0])
                check(rng[1] == len(parsed["text"]),
                      f"text material {i}: style range 가 글자수와 불일치 "
                      f"({rng[1]} vs {len(parsed['text'])})")
                fp = styles[0].get("font", {}).get("path", "")
                if fp and not Path(fp).exists():
                    warn.append(f"text material {i}: 폰트 파일 없음 {fp}")

    # ── 4. 메타 경로 규칙 ──
    check("\\" not in m["draft_fold_path"], "draft_fold_path 에 역슬래시 (캡컷은 / 사용)")
    check(m["draft_fold_path"].rstrip("/").endswith(d.name),
          "draft_fold_path 가 실제 폴더와 불일치")
    check(m["draft_name"] == d.name, "draft_name 이 폴더명과 불일치")
    check(mats["videos"][0]["path"].find("\\") == -1, "material path 에 역슬래시")

    # ── 5. 기준 드래프트와 키 비교 ──
    if a.ref:
        r = json.loads((Path(a.ref) / "draft_content.json").read_text(encoding="utf-8"))
        miss = set(r) - set(c)
        if miss:
            fail.append(f"content 최상위 키 누락: {sorted(miss)}")
        else:
            globals()["ok_n"] = ok_n + 1

        rseg = next((t for t in r["tracks"] if t["type"] == "video"), {}).get("segments")
        if rseg and segs:
            miss = set(rseg[0]) - set(segs[0])
            if miss:
                fail.append(f"세그먼트 키 누락: {sorted(miss)}")
        rv = r["materials"]["videos"]
        if rv and mats["videos"]:
            miss = set(rv[0]) - set(mats["videos"][0])
            if miss:
                fail.append(f"video material 키 누락: {sorted(miss)}")
        rm = json.loads((Path(a.ref) / "draft_meta_info.json").read_text(encoding="utf-8"))
        miss = set(rm) - set(m) - {k for k in rm if k.startswith(("tm_draft_cloud", "pippit"))}
        if miss:
            warn.append(f"meta 키 누락(클라우드/부가 항목 제외): {sorted(miss)}")

    # ── 결과 ──
    print(f"세그먼트 {len(segs)}개  자막 {n_sub}개  타임라인 {cursor / US:.3f}s  "
          f"원본 {src_dur / US:.3f}s  material {len(mats['videos'])}개")
    if sub_info:
        print(f"{sub_info}  최장 {longest}자")
    print()
    for w in warn:
        print(f"  [경고] {w}")
    if fail:
        for f in fail:
            print(f"  [실패] {f}")
        print(f"\n통과 {ok_n} / 실패 {len(fail)}")
        return 1
    print(f"구조 검사 {ok_n}건 전부 통과.")
    print("→ 이제 캡컷에서 직접 열어 재생해야 진짜 검증입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
