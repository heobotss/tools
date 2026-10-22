"""캡컷 드래프트(draft_content.json + draft_meta_info.json)를 생성한다.

스키마는 이 PC의 캡컷 9.1.0 이 실제로 저장한 드래프트에서 그대로 떠왔다.
(templates/*.json — 추정이 아니라 실측)

핵심 규칙
  · 시간 단위는 전부 µs 정수
  · target_timerange 는 타임라인 위 위치(연속), source_timerange 는 원본에서 떼올 위치
  · 세그먼트 1개마다 material 1개 + extra_material 7종을 새로 만든다 (캡컷이 그렇게 저장함)
  · draft_meta_info.tm_duration 이 타임라인 총길이와 다르면 캡컷이 길이를 잘못 잡는다
  · path 는 슬래시(/), draft_root_path 만 역슬래시(\\) — 캡컷이 그렇게 쓴다
  · 자막의 clip.transform.y 는 화면 절반높이 기준 정규화값. +1 위, -1 아래.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import TEMPLATE_DIR, find_draft_root
from .cuts import SubtitlePiece
from .probe import MediaInfo
from .silence import Span

US = 1_000_000

# 한글 폰트 후보 — 앞에서부터 존재하는 걸 쓴다. 없으면 캡컷 기본값에 맡긴다.
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\malgunbd.ttf",
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\NanumGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)


def _uid() -> str:
    """캡컷 스타일 대문자 UUID."""
    return str(uuid.uuid4()).upper()


def _load(name: str) -> dict:
    return json.loads((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def _fwd(p: Path | str) -> str:
    """캡컷이 저장하는 경로 표기 — 슬래시."""
    return str(p).replace("\\", "/")


def default_font() -> str:
    for c in _FONT_CANDIDATES:
        if Path(c).exists():
            return _fwd(c)
    return ""


# 캡컷 text material 의 check_flag 비트마스크.
#
# 배경·외곽선·그림자는 값을 채워두는 것만으로는 안 나온다. background_color 가
# "#000000" 이어도 이 비트가 꺼져 있으면 렌더링되지 않는다. 실제 드래프트에서
# 확인한 값: 일반 자막 23(배경만), 강조 자막 47(외곽선+그림자, 배경 없음).
CF_BASE = 1 | 2 | 4          # 항상 켜져 있던 하위 비트
CF_STROKE = 8
CF_BACKGROUND = 16
CF_SHADOW = 32


def check_flag(stroke: bool, background: bool, shadow: bool) -> int:
    return (CF_BASE
            | (CF_STROKE if stroke else 0)
            | (CF_BACKGROUND if background else 0)
            | (CF_SHADOW if shadow else 0))


@dataclass
class SubtitleStyle:
    font_path: str = ""            # 비우면 시스템 한글 폰트를 자동 탐색
    # 캡컷이 내려받아 쓰는 폰트는 경로가 아니라 이 id 로 식별한다.
    # 캐시가 지워졌거나 경로 정규화가 어긋나도 id 가 있으면 캡컷이 다시 찾는다.
    font_resource_id: str = ""
    font_title: str = ""
    size: float = 9.0              # 캡컷 텍스트 크기 단위
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    transform_y: float = -0.72     # -1 이 화면 맨 아래. 자막은 아래쪽에.
    transform_x: float = 0.0
    stroke: bool = True            # 검은 테두리 — 밝은 배경에서 글자가 사는가
    stroke_width: float = 0.08
    shadow: bool = True
    # 자막 뒤 반투명 검은 박스. 켜면 어떤 배경에서도 글자가 읽힌다.
    background: bool = False
    background_color: str = "#000000"
    background_alpha: float = 0.6
    background_round: float = 0.3
    background_height: float = 0.14
    background_width: float = 0.14
    # 한 줄 최대 글자수는 여기가 아니라 cuts.map_subtitles 가 쓴다.
    # 길이 초과분을 줄바꿈이 아니라 다음 자막으로 넘겨야 하는데, 그건
    # 시간 분배가 필요해서 자막을 만드는 단계에서만 할 수 있다.

    def resolved_font(self) -> str:
        return self.font_path or default_font()


# ── 세그먼트마다 붙는 부속 material ─────────────────────────────────
def _extras_audio() -> tuple[list[str], dict[str, dict]]:
    """(extra_material_refs, {카테고리: 객체}) — 효과음 세그먼트용 5종.

    실제 드래프트에서 뜯어온 조합 그대로다 (추정 아님).
    """
    buckets = {
        "speeds": {"id": _uid(), "type": "speed", "mode": 0, "speed": 1.0,
                   "curve_speed": None},
        "placeholder_infos": {"id": _uid(), "type": "placeholder_info",
                              "meta_type": "none", "res_path": "", "res_text": "",
                              "error_path": "", "error_text": ""},
        "beats": {"id": _uid(), "type": "beats", "enable_ai_beats": False,
                  "gear": 404, "gear_count": 0, "mode": 404, "user_beats": [],
                  "user_delete_ai_beats": None, "ai_beats": {}},
        "sound_channel_mappings": {"id": _uid(), "type": "none",
                                   "audio_channel_mapping": 0,
                                   "is_config_open": False},
        "vocal_separations": {"id": _uid(), "type": "vocal_separation", "choice": 0,
                              "removed_sounds": [], "time_range": None,
                              "production_path": "", "final_algorithm": "",
                              "enter_from": ""},
    }
    return [o["id"] for o in buckets.values()], buckets


def _extras_video(speed: float = 1.0) -> tuple[list[str], dict[str, dict]]:
    """(extra_material_refs, {카테고리: 객체}) — 영상 세그먼트용 7종."""
    buckets = {
        "speeds": {"id": _uid(), "type": "speed", "mode": 0, "speed": float(speed),
                   "curve_speed": None},
        "placeholder_infos": {"id": _uid(), "type": "placeholder_info",
                              "meta_type": "none", "res_path": "", "res_text": "",
                              "error_path": "", "error_text": ""},
        "canvases": {"id": _uid(), "type": "canvas_color", "color": "", "blur": 0.0,
                     "image": "", "album_image": "", "image_id": "", "image_name": "",
                     "source_platform": 0, "team_id": ""},
        "sound_channel_mappings": {"id": _uid(), "type": "none",
                                   "audio_channel_mapping": 0, "is_config_open": False},
        "vocal_separations": {"id": _uid(), "type": "vocal_separation", "choice": 0,
                              "removed_sounds": [], "time_range": None,
                              "production_path": "", "final_algorithm": "",
                              "enter_from": ""},
        "material_animations": {"id": _uid(), "type": "sticker_animation",
                                "animations": [], "multi_language_current": "none"},
        "material_colors": {"id": _uid(), "is_color_clip": False, "is_gradient": False,
                            "solid_color": "", "gradient_colors": [],
                            "gradient_percents": [], "gradient_angle": 90.0,
                            "width": 0.0, "height": 0.0},
    }
    return [o["id"] for o in buckets.values()], buckets


def _one_line(text: str) -> str:
    """자막은 항상 한 줄.

    길이 조절은 cuts.map_subtitles 가 이미 끝냈다 — 넘치는 분량은 줄바꿈이
    아니라 **다음 자막 조각**으로 넘어가 있다. 여기서는 개행만 확실히 지운다.
    """
    return " ".join(text.split())


def _text_content(text: str, st: SubtitleStyle) -> str:
    """캡컷 text material 의 content 필드. JSON 이 문자열로 한 번 더 감싸인다."""
    style: dict = {
        "fill": {"content": {"render_type": "solid",
                             "solid": {"color": list(st.color)}}},
        "font": {"path": st.resolved_font(), "id": st.font_resource_id},
        "size": st.size,
        "range": [0, len(text)],
        "useLetterColor": True,
    }
    if st.stroke:
        style["strokes"] = [{
            "content": {"render_type": "solid", "solid": {"color": [0.0, 0.0, 0.0]}},
            "width": st.stroke_width,
        }]
    if st.shadow:
        style["shadows"] = [{
            "content": {"render_type": "solid", "solid": {"color": [0.0, 0.0, 0.0]}},
            "alpha": 0.9, "angle": -45.0, "distance": 5.0, "diffuse": 0.025,
            "thickness_projection_enable": False,
            "thickness_projection_angle": -45,
            "thickness_projection_distance": 0,
        }]
    return json.dumps({"text": text, "styles": [style]}, ensure_ascii=False)


def _unique_dir(root: Path, name: str) -> Path:
    """이름 충돌 시 뒤에 (2), (3) … 을 붙인다. 기존 드래프트는 절대 덮어쓰지 않는다."""
    candidate = root / name
    n = 2
    while candidate.exists():
        candidate = root / f"{name} ({n})"
        n += 1
    return candidate


def _make_cover(video: Path, at_us: int, dest: Path) -> bool:
    """드래프트 목록에 뜰 썸네일. 실패해도 드래프트 자체엔 영향 없다."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{at_us / US:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=-2:400", str(dest)],
            capture_output=True, timeout=60,
        )
        return r.returncode == 0 and dest.exists()
    except (subprocess.SubprocessError, OSError):
        return False


@dataclass
class CardClip:
    """타임라인에 얹을 카드. 시간은 배속이 적용된 타임라인 좌표."""
    start: int
    dur: int
    path: Path
    source_dur: int
    source_start: int = 0        # 소재 안에서 어디부터 쓰는가 (앞으로 늘릴 여유)
    fullscreen: bool = True
    scale: float = 0.42          # 오버레이일 때 크기
    pos_x: float = 0.52          # 화면 절반폭 기준. + 가 오른쪽.
    pos_y: float = 0.42          # + 가 위쪽


@dataclass
class DraftResult:
    folder: Path
    name: str
    segments: int
    total_us: int
    original_us: int
    subtitles: int = 0
    cards: int = 0

    @property
    def removed_us(self) -> int:
        return self.original_us - self.total_us

    @property
    def removed_ratio(self) -> float:
        return self.removed_us / self.original_us if self.original_us else 0.0


def build_draft(
    media: MediaInfo,
    keeps: list[Span],
    name: str,
    draft_root: Path | None = None,
    subtitles: list[SubtitlePiece] | None = None,
    style: SubtitleStyle | None = None,
    styles: dict[str, SubtitleStyle] | None = None,
    speed: float = 1.0,
    zooms: list[float] | None = None,
    zoom_offsets: list[float] | None = None,
    cards: list["CardClip"] | None = None,
    sfx: list | None = None,
    beauty: bool = False,
    filter_4k: bool = False,
) -> DraftResult:
    """남길 구간(keeps)만 이어붙인 점프컷 드래프트를 만든다.

    subtitles 를 주면 자막 트랙을 얹는다. 자막의 시간은 **타임라인 좌표**여야 한다
    (원본 좌표를 그대로 넣으면 컷 이후 화면과 어긋난다 — cuts.map_subtitles 참고).
    """
    if not keeps:
        raise ValueError("남길 구간이 하나도 없습니다. 무음 임계값(noise_db)을 낮춰보세요.")

    base_style = style or SubtitleStyle()
    root = Path(draft_root) if draft_root else find_draft_root()
    folder = _unique_dir(root, name)
    folder.mkdir(parents=True)

    content = _load("draft_skeleton.json")
    material_tpl = _load("video_material.json")
    segment_tpl = _load("video_segment.json")

    content["canvas_config"] = {
        "ratio": "original",
        "width": media.width,
        "height": media.height,
        "background": None,
    }
    content["fps"] = float(media.fps)
    content["id"] = _uid()
    content["name"] = ""

    mats: dict[str, list] = content["materials"]
    segments: list[dict] = []
    cursor = 0  # 타임라인 위 현재 위치
    acc_src = 0  # 배속 전 좌표에서의 누적 위치 (자막과 좌표계를 맞추기 위함)

    # 얼굴 보정 — 대표님 완성본 세 편 모든 클립에 같은 값으로 들어 있다
    # (잡티 지우기·고르게·작은 얼굴·머리 0.5). 풀링4 의 설정을 틀로 떠 두었다.
    beauty_tpl = _load("beauty_effects.json") if beauty else []
    beauty_tpl = [m for m in beauty_tpl if not m.get("path") or Path(m["path"]).exists()]

    for ki, keep in enumerate(keeps):
        mat = json.loads(json.dumps(material_tpl, ensure_ascii=False))
        mat["id"] = _uid()
        mat["local_material_id"] = str(uuid.uuid4())
        mat["path"] = _fwd(media.path)
        mat["material_name"] = media.path.name
        mat["duration"] = media.duration_us
        mat["width"] = media.width
        mat["height"] = media.height
        mat["has_audio"] = media.has_audio
        mats["videos"].append(mat)

        refs, buckets = _extras_video(speed)
        for cat, obj in buckets.items():
            mats[cat].append(obj)
        for bt in beauty_tpl:
            bm = json.loads(json.dumps(bt, ensure_ascii=False))
            bm["id"] = _uid()
            mats.setdefault("effects", []).append(bm)
            refs = refs + [bm["id"]]

        # 배속을 주면 타임라인에서 차지하는 길이가 줄어든다.
        # source 는 원본에서 떼올 구간, target 은 그게 놓일 자리 —
        # 둘을 같게 두면 배속이 걸린 만큼 영상이 밀린다.
        #
        # **길이를 하나씩 반올림해 더하면 안 된다.** 자막은 절대 위치를
        # 반올림해서 넣는데(아래 starts), 클립만 반올림한 길이를 누적하면
        # 두 좌표계가 클립마다 조금씩 벌어져 자막이 컷과 안 맞는다.
        # 양쪽 다 "배속 전 위치를 반올림" 으로 통일한다.
        b0 = int(round(acc_src / speed))
        acc_src += keep.dur
        tgt_dur = max(1, int(round(acc_src / speed)) - b0)

        seg = json.loads(json.dumps(segment_tpl, ensure_ascii=False))
        seg["id"] = _uid()
        seg["material_id"] = mat["id"]
        seg["extra_material_refs"] = refs
        seg["source_timerange"] = {"start": keep.start, "duration": keep.dur}
        seg["target_timerange"] = {"start": b0, "duration": tgt_dur}
        seg["render_index"] = 0
        seg["speed"] = float(speed)

        if zooms:
            z = float(zooms[ki % len(zooms)])
            seg["clip"]["scale"] = {"x": z, "y": z}
            if zoom_offsets:
                # 화면을 움직일 수 있는 건 **확대로 생긴 여유 안**에서다.
                # 1.0배인데 0.12 를 밀어 화면 아래 6% 가 까맣게 빈 적이 있다
                # (2026-10-05 대표님 제보, 그 장면이 딱 그 클립이었다).
                limit = max(0.0, z - 1.0)
                y = float(zoom_offsets[ki % len(zoom_offsets)])
                y = max(-limit, min(limit, y))
                seg["clip"]["transform"] = {"x": 0.0, "y": y}
        segments.append(seg)

        cursor = b0 + tgt_dur

    tracks = [{
        "id": _uid(), "type": "video", "flag": 0, "attribute": 0,
        "name": "", "is_default_name": True, "segments": segments,
    }]

    # ── 카드 오버레이 트랙 ──
    #
    # 전체화면 카드도 메인 트랙에 끼워넣지 않고 **위에 덮는다.** 끼워넣으면
    # 뒤 세그먼트가 전부 밀려서 자막 타이밍을 다시 계산해야 하는데, 덮으면
    # 아래 타임라인은 그대로다. 전체화면은 그냥 화면을 꽉 채우는 오버레이다.
    n_cards = 0
    if cards:
        card_segs: list[dict] = []
        for ci, card in enumerate(cards):
            if card.start >= cursor:
                continue
            dur = min(card.dur, cursor - card.start)
            if dur <= 0:
                continue

            cm = json.loads(json.dumps(material_tpl, ensure_ascii=False))
            cm["id"] = _uid()
            cm["local_material_id"] = str(uuid.uuid4())
            cm["path"] = _fwd(card.path)
            cm["material_name"] = Path(card.path).name
            cm["duration"] = card.source_dur
            cm["width"], cm["height"] = media.width, media.height
            cm["has_audio"] = False
            mats["videos"].append(cm)

            refs, buckets = _extras_video(1.0)
            for cat, obj in buckets.items():
                mats[cat].append(obj)

            cs = json.loads(json.dumps(segment_tpl, ensure_ascii=False))
            cs["id"] = _uid()
            cs["material_id"] = cm["id"]
            cs["extra_material_refs"] = refs
            cs["source_timerange"] = {"start": card.source_start,
                                      "duration": min(dur, card.source_dur)}
            cs["target_timerange"] = {"start": card.start, "duration": dur}
            # 메인 영상(0)보다 위, 자막(14000)보다 아래
            cs["render_index"] = 1000 + ci
            cs["track_render_index"] = 1
            cs["volume"] = 0.0          # 카드에 소리가 섞이면 안 된다
            cs["last_nonzero_volume"] = 0.0
            if not card.fullscreen:
                cs["clip"]["scale"] = {"x": card.scale, "y": card.scale}
                cs["clip"]["transform"] = {"x": card.pos_x, "y": card.pos_y}
            card_segs.append(cs)

        if card_segs:
            tracks.append({
                "id": _uid(), "type": "video", "flag": 0, "attribute": 0,
                "name": "", "is_default_name": True, "segments": card_segs,
            })
            n_cards = len(card_segs)

    # ── 효과음 트랙 ──
    n_sfx = 0
    if sfx:
        from .sfx import load_palette
        pal = load_palette()
        sfx_segs: list[dict] = []
        for clip in sfx:
            mat = pal.get(clip.name)
            if not mat or clip.at_us >= cursor:
                continue
            am = json.loads(json.dumps(mat, ensure_ascii=False))
            am["id"] = _uid()
            mats.setdefault("audios", []).append(am)

            refs, buckets = _extras_audio()
            for cat, obj in buckets.items():
                mats.setdefault(cat, []).append(obj)

            dur = min(clip.dur_us, cursor - clip.at_us)
            sfx_segs.append({
                "id": _uid(), "material_id": am["id"],
                "extra_material_refs": refs,
                "source_timerange": {"start": 0, "duration": dur},
                "target_timerange": {"start": clip.at_us, "duration": dur},
                "speed": 1.0, "volume": 1.0, "last_nonzero_volume": 1.0,
                "render_index": 0, "visible": True, "is_loop": False,
                "is_placeholder": False, "reverse": False, "enable_adjust": False,
                "intensifies_audio": False, "is_tone_modify": False,
                "common_keyframes": [], "keyframe_refs": [],
                "template_id": "", "group_id": "", "track_attribute": 0,
                "track_render_index": 0, "uniform_scale": None,
                "caption_info": None, "cartoon": False, "clip": None,
                "hdr_settings": None, "responsive_layout": None,
                "source": 0, "state": 0, "desc": "",
                "render_timerange": {"start": 0, "duration": 0},
                "lyric_keyframes": None, "raw_segment_id": "",
                "segment_color_tag": 0,
            })
        if sfx_segs:
            tracks.append({
                "id": _uid(), "type": "audio", "flag": 0, "attribute": 0,
                "name": "", "is_default_name": True, "segments": sfx_segs,
            })
            n_sfx = len(sfx_segs)

    # ── 자막 트랙 ──
    sub_count = 0
    if subtitles:
        text_mat_tpl = _load("text_material.json")
        text_seg_tpl = _load("text_segment.json")
        text_segments: list[dict] = []

        # 자막 시간은 배속 걸기 **전** 좌표로 들어온다. 영상은 배속만큼
        # 짧아지므로 같은 비율로 당겨줘야 화면과 맞는다.
        # (이걸 빼먹으면 뒤로 갈수록 자막이 밀리고 끝부분은 잘려나간다)
        #
        # 시작과 길이를 따로 반올림하면 사이에 1µs 짜리 틈이 생겨서
        # 자막이 미세하게 깜빡인다. 시작만 반올림하고 길이는 다음 시작에서 뺀다.
        starts = [int(round(p.start / speed)) for p in subtitles]
        # 다음 자막과 **붙어 있을 때만** 다음 시작까지 잇는다.
        # 일부러 비워 둔 자리(카드가 떠서 자막을 내린 곳)까지 이어 붙이면
        # 앞 자막이 카드 밑으로 늘어나 버린다.
        own_ends = [int(round(p.end / speed)) for p in subtitles]
        ends = [starts[i + 1] if i + 1 < len(starts)
                and abs(own_ends[i] - starts[i + 1]) <= 2_000
                else own_ends[i]
                for i in range(len(starts))]

        for i, piece in enumerate(subtitles):
            p_start = starts[i]
            p_dur = max(1, ends[i] - starts[i])
            if p_start >= cursor:
                continue  # 타임라인 밖 자막은 버린다
            dur = min(p_dur, cursor - p_start)
            if dur <= 0:
                continue

            shown = _one_line(piece.text)
            # 문장 성격(일반/부정/긍정)에 따라 다른 자막 스타일을 쓴다
            style = (styles or {}).get(piece.kind) or base_style

            tm = json.loads(json.dumps(text_mat_tpl, ensure_ascii=False))
            tm["id"] = _uid()
            tm["type"] = "subtitle"
            tm["recognize_text"] = piece.text
            tm["content"] = _text_content(shown, style)
            tm["base_content"] = ""
            # 값만 채우면 안 나온다 — check_flag 비트가 실제 렌더링을 결정한다
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
            # 캡컷 내려받기 폰트는 id 로 찾는다. 경로만 적으면 캐시가 지워졌을 때
            # 조용히 기본 폰트로 대체된다.
            if style.font_resource_id:
                tm["font_resource_id"] = style.font_resource_id
                tm["font_source_platform"] = 1
                tm["fonts"] = [{
                    "id": _uid(), "resource_id": style.font_resource_id,
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
            # 단어 타임스탬프는 세그먼트 시작 기준 ms
            if piece.words:
                tm["words"] = {
                    "start_time": [max(0, int((w.start - piece.start) / speed) // 1000)
                                   for w in piece.words],
                    "end_time": [max(0, int((w.end - piece.start) / speed) // 1000)
                                 for w in piece.words],
                    "text": [w.text for w in piece.words],
                }
            mats["texts"].append(tm)

            anim = {"id": _uid(), "type": "sticker_animation",
                    "animations": [], "multi_language_current": "none"}
            mats["material_animations"].append(anim)

            ts = json.loads(json.dumps(text_seg_tpl, ensure_ascii=False))
            ts["id"] = _uid()
            ts["material_id"] = tm["id"]
            ts["extra_material_refs"] = [anim["id"]]
            ts["target_timerange"] = {"start": p_start, "duration": dur}
            ts["clip"]["transform"] = {"x": style.transform_x, "y": style.transform_y}
            ts["clip"]["scale"] = {"x": 1.0, "y": 1.0}
            ts["render_index"] = 14000 + i
            ts["track_render_index"] = 1
            text_segments.append(ts)

        if text_segments:
            tracks.append({
                "id": _uid(), "type": "text", "flag": 1, "attribute": 0,
                "name": "", "is_default_name": True, "segments": text_segments,
            })
            sub_count = len(text_segments)

    # ── 4K 필터 ── 완성본 세 편 모두 영상 전체에 "4K" 필터(값 1.0) 하나를 깔았다.
    if filter_4k:
        ftpl = _load("filter_4k.json")
        fm = json.loads(json.dumps(ftpl["material"], ensure_ascii=False))
        if not fm.get("path") or Path(fm["path"]).exists():
            fm["id"] = _uid()
            mats.setdefault("effects", []).append(fm)
            fs = json.loads(json.dumps(ftpl["segment"], ensure_ascii=False))
            fs["id"] = _uid()
            fs["material_id"] = fm["id"]
            fs["target_timerange"] = {"start": 0, "duration": cursor}
            ft = json.loads(json.dumps(ftpl["track"], ensure_ascii=False))
            ft["id"] = _uid()
            ft["segments"] = [fs]
            tracks.append(ft)

    content["tracks"] = tracks
    content["duration"] = cursor

    now_s = int(time.time())
    now_us = now_s * US
    content["create_time"] = now_s
    content["update_time"] = now_s

    (folder / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8")

    # ── meta_info ──
    meta = _load("meta_skeleton.json")
    meta["draft_fold_path"] = _fwd(folder)
    meta["draft_root_path"] = str(root)          # 여긴 역슬래시가 맞다
    meta["draft_id"] = _uid()
    meta["draft_name"] = folder.name
    meta["draft_timeline_materials_size_"] = media.size_bytes
    meta["tm_draft_create"] = now_us
    meta["tm_draft_modified"] = now_us
    meta["tm_duration"] = cursor                  # 타임라인 총길이와 반드시 일치
    meta["draft_materials"][0]["value"] = [{
        "ai_group_type": "",
        "create_time": now_s,
        "duration": media.duration_us,
        "enter_from": 0,
        "extra_info": media.path.name,
        "file_Path": _fwd(media.path),
        "height": media.height,
        "id": mats["videos"][0]["local_material_id"],
        "import_time": now_s,
        "import_time_ms": now_us,
        "item_source": 1,
        "material_color_tag": "",
        "md5": "",
        "metetype": "video",
        "roughcut_time_range": {"duration": media.duration_us, "start": 0},
        "sub_time_range": {"duration": -1, "start": -1},
        "type": 0,
        "width": media.width,
    }]

    cover_ok = _make_cover(media.path, keeps[0].start + min(keeps[0].dur // 2, US),
                           folder / "draft_cover.jpg")
    meta["draft_cover"] = "draft_cover.jpg" if cover_ok else ""

    (folder / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    return DraftResult(
        folder=folder,
        name=folder.name,
        segments=len(segments),
        total_us=cursor,
        original_us=media.duration_us,
        subtitles=sub_count,
        cards=n_cards,
    )


def delete_draft(folder: Path) -> None:
    """테스트용 — 만든 드래프트를 지운다. 드래프트 루트 밑인지 확인 후에만."""
    folder = Path(folder).resolve()
    root = find_draft_root().resolve()
    if root not in folder.parents:
        raise ValueError(f"드래프트 루트 밖의 경로는 지우지 않습니다: {folder}")
    if (folder / ".locked").exists():
        raise ValueError(
            f"캡컷이 열고 있는 드래프트입니다. 캡컷에서 닫은 뒤 다시 시도하세요: {folder.name}")
    shutil.rmtree(folder)
