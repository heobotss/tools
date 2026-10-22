"""완성 드래프트의 자막 싱크를 **실제 소리**로 잰다.

드래프트의 본 영상 클립을 캡컷이 재생하는 그대로(구간 자르기 + 배속) 이어 붙여
타임라인 소리를 만들고, 자막 한 줄 한 줄의 글을 그 소리에 강제 정렬해서
"이 줄을 실제로 말하기 시작한 순간"과 "자막이 뜨는 순간"을 비교한다.

만드는 쪽(pipeline)은 문장 단위로 원본 소리에 정렬하고 컷·배속으로 옮겨 자막을
만든다. 여기서는 **컷이 끝난 결과물의 소리**에 **줄 단위**로 다시 대므로, 옮기는
과정(컷 경계·배속·줄 나눔)에서 생긴 어긋남이 그대로 드러난다.

    python tools/audit_sync.py <드래프트 이름 또는 폴더> [--show 20]
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from capcut_agent import forced_align as FA  # noqa: E402

US = 1_000_000
SR = FA.SR
DRAFTS = Path(r"C:\Users\허재윤\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft")
EDGE_US = 300_000        # 이웃 줄이 없을 때 앞뒤로 더 보는 소리
LATE_BAD_US = 120_000    # 말보다 이만큼 늦게 뜨면 늦은 것
EARLY_BAD_US = 250_000   # 말보다 이만큼 먼저 뜨면 이른 것


def f(us):
    s, ms = divmod(int(us), US)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 1000:03d}"


def timeline_audio(d: dict) -> np.ndarray:
    """본 영상 트랙을 캡컷 재생 그대로 16k 모노로 만든다."""
    vids = {m["id"]: m for m in d["materials"]["videos"]}
    tracks = [t for t in d["tracks"] if t["type"] == "video" and t.get("segments")]
    main = max(tracks, key=lambda t: len(t["segments"]))
    segs = sorted(main["segments"], key=lambda s: s["target_timerange"]["start"])
    paths = sorted({vids[s["material_id"]]["path"] for s in segs})
    idx = {p: i for i, p in enumerate(paths)}
    parts, labels = [], []
    cursor = 0
    for k, s in enumerate(segs):
        src, tgt = s["source_timerange"], s["target_timerange"]
        if tgt["start"] > cursor:                       # 타임라인 빈 곳은 무음
            parts.append(f"anullsrc=r={SR}:cl=mono,atrim=duration={(tgt['start']-cursor)/US:.6f}[z{k}]")
            labels.append(f"[z{k}]")
        tempo = src["duration"] / tgt["duration"]
        i = idx[vids[s["material_id"]]["path"]]
        parts.append(
            f"[{i}:a]atrim=start={src['start']/US:.6f}:duration={src['duration']/US:.6f},"
            f"asetpts=PTS-STARTPTS,aresample={SR},aformat=channel_layouts=mono,"
            # atempo 는 조각마다 15ms 안팎 꼬리를 더 붙인다 — 179조각이면 2.8초가
            # 밀려 뒤로 갈수록 자막이 먼저 뜨는 것처럼 보였다. 목표 길이로 딱 자른다.
            f"atempo={tempo:.6f},apad,atrim=duration={tgt['duration']/US:.6f}[a{k}]")
        labels.append(f"[a{k}]")
        cursor = tgt["start"] + tgt["duration"]
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(graph)
        script = fh.name
    cmd = ["ffmpeg", "-v", "error"]
    for p in paths:
        cmd += ["-i", p]
    cmd += ["-filter_complex_script", script, "-map", "[out]", "-ac", "1", "-ar", str(SR),
            "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True, check=True)
    Path(script).unlink(missing_ok=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def main() -> int:
    arg = sys.argv[1]
    show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 20
    # 조용히 녹음한 촬영본은 정렬 점수가 낮게 나온다 — 기준을 낮춰서 잴 수 있다
    conf_min = float(sys.argv[sys.argv.index("--conf") + 1]) if "--conf" in sys.argv else -2.0
    folder = Path(arg) if Path(arg).is_dir() else DRAFTS / arg
    d = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
    tm = {m["id"]: m for m in d["materials"]["texts"]}
    tt = max((t for t in d["tracks"] if t["type"] == "text" and t.get("segments")),
             key=lambda t: len(t["segments"]))
    subs = []
    for s in sorted(tt["segments"], key=lambda x: x["target_timerange"]["start"]):
        try:
            txt = json.loads(tm[s["material_id"]]["content"])["text"]
        except Exception:
            continue
        a = s["target_timerange"]["start"]
        subs.append((a, a + s["target_timerange"]["duration"], txt))

    import torch
    model, proc, mode = FA._load()
    vocab = proc.tokenizer.get_vocab()
    blank = proc.tokenizer.pad_token_id
    wave = timeline_audio(d)
    total = int(len(wave) / SR * US)
    print(f"{folder.name} · 자막 {len(subs)}줄 · 타임라인 소리 {f(total)}")

    rows = []
    for i, (a, b, txt) in enumerate(subs):
        # 이웃 줄이 딱 붙어 있으면 같이 정렬해 경계를 서로 잡아 준다
        group = [i]
        if i > 0 and abs(subs[i - 1][1] - a) < 50_000:
            group.insert(0, i - 1)
        if i + 1 < len(subs) and abs(subs[i + 1][0] - b) < 50_000:
            group.append(i + 1)
        w0 = subs[group[0]][0] - (EDGE_US if group[0] == i else 0)
        w1 = subs[group[-1]][1] + (EDGE_US if group[-1] == i else 0)
        w0, w1 = max(0, w0), min(total, w1)
        toks, owner = [], []
        for g in group:
            for t in FA._tokens(subs[g][2].replace(" ", ""), vocab, mode):
                toks.append(t)
                owner.append(g)
        mine = [k for k, o in enumerate(owner) if o == i]
        chunk = wave[int(w0 / US * SR): int(w1 / US * SR)]
        if not mine or len(chunk) < SR // 10:
            continue
        with torch.inference_mode():
            em = torch.log_softmax(model(proc(chunk, sampling_rate=SR, return_tensors="pt")
                                         .input_values).logits[0], dim=-1).cpu().numpy()
        if em.shape[0] < len(toks):
            continue
        spans = FA._trellis(em, toks, blank)
        upf = (w1 - w0) / em.shape[0]
        got = [spans[k] for k in mine if spans[k][0] is not None]
        if not got:
            continue
        vs = int(w0 + got[0][0] * upf)
        ve = int(w0 + got[-1][1] * upf)
        # 글자 점수가 너무 낮으면(엉뚱한 소리에 억지로 붙음) 판정에서 뺀다
        conf = float(np.mean([em[spans[k][0], toks[k]] for k in mine if spans[k][0] is not None]))
        # 뒤 줄이 카드 밑에 내려져 없으면 그 말까지 이 줄에 붙어 창 끝에 닿는다 — 끝은 못 잰다
        edge = group[-1] == i and ve >= w1 - 60_000
        rows.append((a, b, txt, vs, ve, conf, edge))

    ok = [r for r in rows if r[5] > conf_min]
    ds = np.array([r[0] - r[3] for r in ok]) / 1000      # + 늦게 뜸 / - 먼저 뜸
    de = np.array([r[1] - r[4] for r in ok if not r[6]]) / 1000
    late = [r for r in ok if r[0] - r[3] > LATE_BAD_US]
    early = [r for r in ok if r[3] - r[0] > EARLY_BAD_US]
    cut_early = [r for r in ok if not r[6] and r[4] - r[1] > LATE_BAD_US]   # 말이 안 끝났는데 사라짐
    print(f"잰 줄 {len(ok)} (정렬 신뢰 낮아 뺀 줄 {len(rows) - len(ok)})")
    print(f"자막 시작 - 말 시작   중앙 {np.median(ds):+.0f}ms · |차| 90% {np.percentile(abs(ds), 90):.0f}ms")
    print(f"자막 끝   - 말 끝     중앙 {np.median(de):+.0f}ms")
    print(f"늦게 뜸(>{LATE_BAD_US//1000}ms) {len(late)}줄 · 너무 먼저 뜸(>{EARLY_BAD_US//1000}ms) "
          f"{len(early)}줄 · 말 끝나기 전에 사라짐(>{LATE_BAD_US//1000}ms) {len(cut_early)}줄")
    worst = sorted(late + early + cut_early,
                   key=lambda r: -max(r[0] - r[3], r[3] - r[0] - EARLY_BAD_US + LATE_BAD_US,
                                      r[4] - r[1]))
    seen = set()
    for r in worst:
        if r[0] in seen or len(seen) >= show:
            continue
        seen.add(r[0])
        print(f"  {f(r[0])}  자막 {f(r[0])}~{f(r[1])} · 말 {f(r[3])}~{f(r[4])}  "
              f"시작 {(r[0]-r[3])/1000:+.0f}ms 끝 {(r[1]-r[4])/1000:+.0f}ms  {r[2]}")
    out = folder.name + "_sync.json"
    (Path(tempfile.gettempdir()) / out).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
