"""강제 정렬 — 이미 아는 문장을 소리에 대고, 낱말마다 실제로 발음한 구간을 찾는다.

## 왜 필요한가

whisper 는 **받아쓰기** 모델이라 낱말 시각은 덤으로 추정한다. 컷이 촘촘한 영상에서는
그 추정이 컷 경계를 넘나들어 낱말이 엉뚱한 클립에 붙는다. 실측(2026-09-16, 0916 촬영본):
whisper 가 "특히" 를 01:49.26 에 시작한다고 했는데 실제 소리는 무음 뒤 01:49.78 에
시작했다. 그 사이 무음을 컷이 잘라내면서 "특히" 가 앞 클립으로 계산돼
"생각하시면 돼요 특히 초보 / 트레이너 분들은요" 로 갈라졌다.

지금까지는 소리 크기로 낱말을 다시 펼쳤는데(script.redistribute_words) 그건 "소리가
있는 곳"만 알지 "어느 글자의 소리인가"는 모른다. 강제 정렬은 한국어 음향 모델이
글자 하나하나를 소리에 대 보고 가장 그럴듯한 경로를 찾는다(CTC 트렐리스).

## 모델

kresnik/wav2vec2-large-xlsr-korean — WhisperX 가 한국어 정렬에 쓰는 모델이다.
처음 한 번 내려받으면(약 1.3GB) 이후 오프라인으로 돈다. 없으면 조용히 예전 방식으로
돌아간다 (available() 가 False).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import unicodedata
from pathlib import Path

import numpy as np

from .config import WORK_DIR

US = 1_000_000
SR = 16_000
MODEL_ID = "kresnik/wav2vec2-large-xlsr-korean"
VERSION = "fa3"
PAD_US = 400_000              # 문장 앞뒤로 이만큼 여유를 두고 소리를 자른다

# ── 낱말 사이 헛시작 ──
# 실측(2026-09-17, 0916 03:10): "뿌려도 회원 입에서 (멈춤) 회이… (멈춤) 회원 입에서 나올"
# whisper 는 "회원 입에서" 를 한 번만 받아적었고, 예전에는 그 삼킨 시간이 낱말 하나를
# 길게 늘려서(filler.drop_retakes 의 길이 이상치) 잡혔다. 강제 정렬이 낱말을 제 소리에
# 딱 붙이면서 그 신호가 사라졌다 — 실패한 시도는 이제 **낱말 사이 빈틈**에 남는다.
# 빈틈에서 모델이 읽어낸 글자가 **바로 뒤 낱말들의 첫머리**와 같으면 헛시작이다.
GAP_MIN_US = 500_000          # 이보다 짧은 틈은 숨 고르기
GAP_PAD_US = 60_000           # 잘라낼 때 앞뒤 낱말에 남길 여유
HEAD_SYL = 4                  # 뒤 낱말 첫머리 몇 음절과 비교하나
HEAD_MATCH = 3                # 그중 이만큼 이어서 같으면 헛시작

_state: dict = {}


def available() -> bool:
    """모델이 이 PC 에 받아져 있는가 (인터넷에 안 나간다)."""
    if "ok" in _state:
        return _state["ok"]
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL_ID, local_files_only=True)
        _state["ok"] = True
    except Exception:
        _state["ok"] = False
    return _state["ok"]


def _load():
    if "model" in _state:
        return _state["model"], _state["proc"], _state["mode"]
    import torch
    from huggingface_hub import snapshot_download
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    path = snapshot_download(MODEL_ID, local_files_only=True)
    proc = Wav2Vec2Processor.from_pretrained(path)
    model = Wav2Vec2ForCTC.from_pretrained(path).eval()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    vocab = proc.tokenizer.get_vocab()
    # 어휘가 완성형 음절인지 자모인지 본다
    syl = sum(1 for k in vocab if len(k) == 1 and 0xAC00 <= ord(k) <= 0xD7A3)
    jamo = sum(1 for k in vocab if len(k) == 1 and 0x1100 <= ord(k) <= 0x11FF)
    mode = "syllable" if syl >= jamo else "jamo"
    _state.update(model=model, proc=proc, mode=mode)
    return model, proc, mode


def _audio(video: str | Path) -> np.ndarray:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def audio_cached(video: str | Path) -> np.ndarray:
    """같은 파일을 두 번 디코딩하지 않는다 (10GB 4K 는 한 번에 몇 분)."""
    key = str(video)
    if _state.get("wave_key") != key:
        _state["wave_key"], _state["wave"] = key, _audio(video)
    return _state["wave"]


def refine_spans(video: str | Path, items: list[tuple[int, int, str]],
                 pad_us: int = 400_000, group: int = 6,
                 max_shift_us: int = 4_000_000) -> list[tuple[int, int] | None]:
    """줄마다 (대략 시작, 대략 끝, 글) 을 주면 **실제 발음 구간**을 돌려준다.

    촬영대본 줄 자막은 대본 글자를 받아쓰기 글자에 대어 시각을 추정하는데,
    말이 대본과 달라지면 그 추정이 통째로 밀린다 — 2026-09-23 촬영본에서
    273줄 중 141줄이 0.3초 넘게 어긋났다(대본 닮음 87%). 여기서는 그 줄의
    글자를 **소리에 직접** 정렬해 시각을 다시 잡는다.

    못 잡은 줄은 None 을 돌려주니 부르는 쪽에서 원래 추정을 쓰면 된다.
    """
    import torch

    out: list[tuple[int, int] | None] = [None] * len(items)
    if not items or not available():
        return out
    model, proc, mode = _load()
    vocab = proc.tokenizer.get_vocab()
    blank = proc.tokenizer.pad_token_id
    wave = audio_cached(video)
    total_us = int(len(wave) / SR * US)

    for i0 in range(0, len(items), group):
        chunk_items = items[i0:i0 + group]
        a_us = max(0, min(t0 for t0, _, _ in chunk_items) - pad_us)
        b_us = min(total_us, max(t1 for _, t1, _ in chunk_items) + pad_us)
        if b_us - a_us < 300_000 or b_us - a_us > 60 * US:
            continue
        toks, owner = [], []
        for k, (_, _, text) in enumerate(chunk_items):
            for t in _tokens("".join(ch for ch in text if not ch.isspace()), vocab, mode):
                toks.append(t)
                owner.append(k)
        if not toks:
            continue
        chunk = wave[int(a_us / US * SR): int(b_us / US * SR)]
        if len(chunk) < SR // 10:
            continue
        with torch.inference_mode():
            em = torch.log_softmax(
                model(proc(chunk, sampling_rate=SR, return_tensors="pt").input_values)
                .logits[0], dim=-1).cpu().numpy()
        if em.shape[0] < len(toks):
            continue
        spans = _trellis(em, toks, blank)
        upf = (b_us - a_us) / em.shape[0]
        first: dict[int, int] = {}
        last: dict[int, int] = {}
        for (f0, f1), k in zip(spans, owner):
            if f0 is None:
                continue
            first.setdefault(k, f0)
            last[k] = f1
        for k, (t0, t1, _) in enumerate(chunk_items):
            if k not in first:
                continue
            ns = int(a_us + first[k] * upf)
            ne = int(a_us + last[k] * upf)
            if ne <= ns or abs(ns - t0) > max_shift_us:
                continue                      # 엉뚱한 데 붙었으면 버린다
            out[i0 + k] = (ns, ne)
    return out


def _tokens(word: str, vocab: dict, mode: str) -> list[int]:
    w = word
    if mode == "jamo":
        w = unicodedata.normalize("NFD", w)
    return [vocab[c] for c in w if c in vocab]


def _trellis(emission: np.ndarray, tokens: list[int], blank: int):
    """CTC 강제 정렬. 토큰마다 (시작 프레임, 끝 프레임) — 끝은 포함 안 함.

    동적계획은 PyTorch 튜토리얼과 같다: 한 프레임마다 "같은 글자에 머문다(빈칸)"
    와 "다음 글자로 넘어간다" 중 점수가 높은 쪽. 끝까지 채운 뒤 되짚어서 각 글자가
    **튀어나온 프레임**을 찾는다. CTC 는 글자를 한두 프레임에 몰아 찍고 나머지는
    빈칸으로 두므로, 그 프레임에서 앞뒤로 그 글자가 여전히 가장 센 동안만 넓힌다
    (뒤따르는 빈칸=무음까지 글자에 붙이면 낱말 끝이 쉼으로 늘어난다).
    """
    T, J = emission.shape[0], len(tokens)
    tok = np.asarray(tokens)
    trellis = np.full((T + 1, J + 1), -np.inf, dtype=np.float32)
    trellis[0, 0] = 0.0
    for t in range(T):
        trellis[t + 1, 0] = trellis[t, 0] + emission[t, blank]
        stay = trellis[t, 1:] + emission[t, blank]
        change = trellis[t, :-1] + emission[t, tok]
        trellis[t + 1, 1:] = np.maximum(stay, change)

    hit = [None] * J
    t, j = T, J
    while j > 0 and t > 0:
        stayed = trellis[t - 1, j] + emission[t - 1, blank]
        changed = trellis[t - 1, j - 1] + emission[t - 1, tok[j - 1]]
        if changed >= stayed:
            hit[j - 1] = t - 1
            j -= 1
        t -= 1

    best = emission.argmax(axis=1)
    spans = []
    for k, f in enumerate(hit):
        if f is None:
            spans.append((None, None))
            continue
        s = e = f
        while s - 1 >= 0 and best[s - 1] == tok[k] and (k == 0 or hit[k - 1] is None
                                                        or s - 1 > hit[k - 1]):
            s -= 1
        while e + 1 < T and best[e + 1] == tok[k] and (k + 1 >= J or hit[k + 1] is None
                                                        or e + 1 < hit[k + 1]):
            e += 1
        spans.append((s, e + 1))
    return spans


def _key(video, tr) -> str:
    h = hashlib.sha1()
    h.update(VERSION.encode())
    h.update(str(Path(video).stat().st_size).encode())
    for s in tr.segs:
        h.update(f"{s.start}|{s.end}|{s.text}".encode("utf-8"))
    return h.hexdigest()[:20]


def _gap_retakes(em: np.ndarray, first: dict, last: dict, words, vocab_inv: dict,
                 blank: int, a_us: int, upf: float) -> list[list[int]]:
    """낱말 사이 빈틈에서 뒤 낱말 첫머리를 미리 한 번 말한 헛시작을 찾는다."""
    import difflib
    out = []
    best = em.argmax(axis=1)
    order = sorted(first)
    for wi, nj in zip(order, order[1:]):
        g0, g1 = last[wi], first[nj]
        if (g1 - g0) * upf < GAP_MIN_US:
            continue
        said, prev = [], None
        for t in range(g0, g1):
            k = int(best[t])
            if k != prev and k != blank:
                said.append(vocab_inv.get(k, ""))
            prev = k
        said = [c for c in said if len(c) == 1 and 0xAC00 <= ord(c) <= 0xD7A3]
        # 빈칸 사이로 같은 글자가 두 번 찍히는 건 한 글자다 ("원 · 원")
        said = "".join(c for i, c in enumerate(said) if i == 0 or c != said[i - 1])
        head = "".join(w.text for w in words[nj:])
        head = "".join(c for c in head if 0xAC00 <= ord(c) <= 0xD7A3)[:HEAD_SYL]
        if len(said) < 2 or len(head) < 2 or head[0] not in said:
            continue
        # 헛시작은 흐리게 발음돼 글자가 한둘 틀린다 (실측 "회원 입에서" → "회원에적").
        # 그래서 이어진 일치가 아니라 **순서대로 몇 글자 들어 있나**(공통 부분열)로 본다.
        sm = difflib.SequenceMatcher(None, said[said.index(head[0]):], head, autojunk=False)
        common = sum(b.size for b in sm.get_matching_blocks())
        if common >= min(HEAD_MATCH, len(head)):
            out.append([int(a_us + g0 * upf + GAP_PAD_US), int(a_us + g1 * upf - GAP_PAD_US)])
    return out


def align(tr, video: str | Path) -> int:
    """문장마다 낱말 시각을 실제 발음 구간으로 바꾼다. 옮긴 낱말 수를 돌려준다.

    낱말 사이 헛시작은 문장마다 ``s.fa_retakes`` 에 [시작, 끝] 으로 남긴다
    (자르는 건 잔말·NG 단계가 한다 — mark 가 구멍을 매번 초기화하기 때문).
    """
    cache = WORK_DIR / "align" / f"{_key(video, tr)}.json"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        moved = 0
        for s, row in zip(tr.segs, saved):
            for w, (a, b) in zip(s.words, row["words"]):
                if (w.start, w.end) != (a, b):
                    moved += 1
                w.start, w.end = a, b
            s.fa_retakes = [tuple(x) for x in row["retakes"]]
            if s.words:
                s.start, s.end = s.words[0].start, s.words[-1].end
        return moved

    import torch
    model, proc, mode = _load()
    vocab = proc.tokenizer.get_vocab()
    vocab_inv = {v: k for k, v in vocab.items()}
    blank = proc.tokenizer.pad_token_id
    wave = audio_cached(video)
    total_us = int(len(wave) / SR * US)
    moved = 0
    saved = []
    for s in tr.segs:
        s.fa_retakes = []
        if not s.words:
            saved.append({"words": [], "retakes": []})
            continue
        a_us = max(0, min(s.start, s.words[0].start) - PAD_US)
        b_us = min(total_us, max(s.end, s.words[-1].end) + PAD_US)
        chunk = wave[int(a_us / US * SR): int(b_us / US * SR)]
        toks, owner = [], []
        for wi, w in enumerate(s.words):
            for t in _tokens(w.text, vocab, mode):
                toks.append(t)
                owner.append(wi)
        if len(chunk) < SR // 10 or not toks:
            saved.append({"words": [(w.start, w.end) for w in s.words], "retakes": []})
            continue
        with torch.inference_mode():
            inp = proc(chunk, sampling_rate=SR, return_tensors="pt").input_values
            logits = model(inp).logits[0]
            em = torch.log_softmax(logits, dim=-1).cpu().numpy()
        if em.shape[0] < len(toks):
            saved.append({"words": [(w.start, w.end) for w in s.words], "retakes": []})
            continue
        spans = _trellis(em, toks, blank)
        us_per_frame = (b_us - a_us) / em.shape[0]
        first, last = {}, {}
        for (f0, f1), wi in zip(spans, owner):
            if f0 is None:
                continue
            first.setdefault(wi, f0)
            last[wi] = f1
        new = []
        for wi, w in enumerate(s.words):
            if wi in first:
                ns = int(a_us + first[wi] * us_per_frame)
                # 끝은 **다음 낱말이 시작하기 직전**까지. CTC 는 글자를 한 프레임에만
                # 찍어서 마지막 글자 프레임으로 끝을 잡으면 "잘" 이 0.02초짜리가 된다.
                # 다만 사이에 쉼이 길면 쉼까지 낱말에 붙이지 않게 0.35초에서 멈춘다.
                tail = last[wi] + int(350_000 / us_per_frame)
                nxt = next((first[k] for k in range(wi + 1, len(s.words)) if k in first), None)
                end_f = min(tail, nxt) if nxt is not None else last[wi] + int(200_000 / us_per_frame)
                ne = int(a_us + end_f * us_per_frame)
                new.append([ns, max(ne, ns + 40_000)])
            else:
                new.append(None)
        # 글자가 하나도 안 잡힌 낱말(숫자·영문)은 이웃 사이에 끼워 넣는다
        for wi, v in enumerate(new):
            if v is None:
                prev_end = new[wi - 1][1] if wi > 0 and new[wi - 1] else s.words[wi].start
                nxt = next((x for x in new[wi + 1:] if x), None)
                nxt_start = nxt[0] if nxt else s.words[wi].end
                new[wi] = [prev_end, max(prev_end + 20_000, nxt_start)]
        s.fa_retakes = [tuple(x) for x in
                        _gap_retakes(em, first, last, s.words, vocab_inv, blank, a_us, us_per_frame)]
        for w, (ns, ne) in zip(s.words, new):
            if abs(w.start - ns) > 30_000:
                moved += 1
            w.start, w.end = ns, ne
        s.start, s.end = s.words[0].start, s.words[-1].end
        saved.append({"words": new, "retakes": [list(x) for x in s.fa_retakes]})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(saved), encoding="utf-8")
    return moved
