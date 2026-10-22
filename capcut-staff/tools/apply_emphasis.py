"""이미 편집이 끝난 드래프트의 **자막 색만** 바꾼다.

대표님이 손질을 끝낸 파일은 컷·확대·효과음·비트가 다 들어 있다. 처음부터 다시
만들면 그게 전부 날아가므로, 폴더를 복사한 뒤 **text material 의 스타일 필드만**
갈아끼운다. 시간·트랙·세그먼트 구조는 한 글자도 건드리지 않는다.

계획(plan.json)은 [{"match": "문구", "kind": "neg"|"pos"}, ...] 형식이다.
문구는 자막 한 줄일 수도, 여러 줄에 걸친 말일 수도 있다 — 걸치는 줄은 전부 물든다.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capcut_agent.draft import _fwd, _text_content, check_flag
from capcut_agent.pipeline import Options
from capcut_agent.style import StyleProfile

US = 1_000_000


def norm(s: str) -> str:
    """공백·문장부호를 털어낸 비교용 문자열."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


def restyle(tm: dict, style) -> None:
    """text material 하나를 주어진 자막 스타일로 갈아끼운다.

    draft.py 가 자막을 처음 쓸 때와 **같은 필드 조합**을 쓴다. 하나라도 빠지면
    캡컷이 값은 읽되 화면에는 안 나온다 (check_flag 가 실제 렌더링을 결정한다).
    """
    try:
        shown = json.loads(tm["content"])["text"]
    except (ValueError, KeyError, TypeError):
        return
    tm["content"] = _text_content(shown, style)
    tm["check_flag"] = check_flag(style.stroke, style.background, style.shadow)
    tm["has_shadow"] = style.shadow
    tm["border_color"] = "#000000" if style.stroke else ""
    tm["border_width"] = style.stroke_width
    tm["background_color"] = style.background_color
    tm["background_alpha"] = style.background_alpha
    tm["background_round_radius"] = style.background_round
    tm["background_height"] = style.background_height
    tm["background_width"] = style.background_width
    tm["background_style"] = 2
    tm["text_color"] = "#%02x%02x%02x" % tuple(
        int(round(v * 255)) for v in style.color)
    tm["font_size"] = float(style.size)
    tm["font_path"] = style.resolved_font()
    if style.font_resource_id:
        tm["font_resource_id"] = style.font_resource_id
        tm["font_source_platform"] = 1
        tm["fonts"] = [{
            "id": str(uuid.uuid4()).upper(), "resource_id": style.font_resource_id,
            "third_resource_id": "", "category_id": "", "category_name": "",
            "source_platform": 1, "path": style.resolved_font(),
            "effect_id": style.font_resource_id,
            "title": style.font_title, "team_id": "",
            "file_uri": "", "request_id": "",
        }]
    else:
        tm["font_resource_id"] = ""
        tm["font_source_platform"] = 0
        tm["fonts"] = []


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
    styles = Options().subtitle_styles(StyleProfile.for_preset("youtube"))

    tm_by_id = {m["id"]: m for m in content["materials"]["texts"]}
    tracks = [t for t in content["tracks"]
              if t["type"] == "text" and t.get("segments")]
    if not tracks:
        print("[에러] 자막 트랙이 없습니다.")
        return 1
    tt = max(tracks, key=lambda t: len(t["segments"]))

    rows = []
    for s in sorted(tt["segments"], key=lambda x: x["target_timerange"]["start"]):
        m = tm_by_id.get(s["material_id"])
        if not m:
            continue
        try:
            txt = json.loads(m.get("content", "{}")).get("text", "")
        except (ValueError, TypeError):
            txt = ""
        rows.append((s["target_timerange"]["start"], m, txt))

    hits = {"neg": 0, "pos": 0}
    used = set()
    for i, (at, m, txt) in enumerate(rows):
        n = norm(txt)
        if len(n) < 4:
            continue
        for pi, p in enumerate(plan):
            if n in norm(p["match"]):
                restyle(m, styles[p["kind"]])
                hits[p["kind"]] += 1
                used.add(pi)
                s, ms = divmod(int(at), US)
                mm, s = divmod(s, 60)
                print(f"  {p['kind']:<3} {mm:02d}:{s:02d}.{ms//1000:03d}  {txt[:34]}")
                break

    missed = [p["match"] for i, p in enumerate(plan) if i not in used]
    (dst / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8")

    meta = json.loads((dst / "draft_meta_info.json").read_text(encoding="utf-8"))
    meta["draft_id"] = str(uuid.uuid4()).upper()
    meta["draft_name"] = new_name
    meta["draft_fold_path"] = _fwd(dst)
    meta["draft_root_path"] = _fwd(dst.parent)
    (dst / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    for stale in ("draft_content.json.bak", "template-2.tmp"):
        (dst / stale).unlink(missing_ok=True)

    print(f"\n빨강 {hits['neg']}줄 · 파랑 {hits['pos']}줄 "
          f"/ 자막 {len(rows)}줄 ({(hits['neg']+hits['pos'])/max(1,len(rows))*100:.0f}%)")
    if missed:
        print(f"못 찾은 문구 {len(missed)}개:")
        for x in missed:
            print(f"  - {x[:46]}")
    print(f"드래프트  {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
