"""faster-whisper 로 대본을 뽑는다.

주의사항 두 가지 — 둘 다 안 지키면 서버가 죽는다.

1. **ASR 직렬화.** ctranslate2/numba 는 스레드 안전하지 않다. 동시에 두 번
   호출하면 segfault 로 프로세스가 통째로 날아간다 → asyncio.Lock 으로 직렬화.
2. **캐시 키는 content hash.** mtime 기준으로 잡으면 같은 파일을 다시 업로드할
   때마다 mtime 이 바뀌어 매번 miss 난다. 20분 영상 재분석 = 수 분 낭비.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from .config import WORK_DIR
from .transcript import Seg, Transcript, Word

US = 1_000_000

# ASR 은 한 번에 하나만. 모듈 레벨 Lock 이라 프로세스 전체에서 공유된다.
_ASR_LOCK = asyncio.Lock()

_model = None
_model_key: tuple[str, str, str] | None = None

DEFAULT_MODEL = os.environ.get("CAPCUT_ASR_MODEL", "large-v3-turbo")

# 프로젝트 폴더 안에 「모델」 폴더가 있으면 인터넷에서 내려받지 않고 그걸 쓴다.
#
# 처음 실행할 때 1.6GB 를 받는데, 회선이 느리거나 막히면 「대본 추출」에서
# 몇 시간이고 멈춘 것처럼 보인다 (2026-09-30 직원 PC 에서 실제로 났다).
# 그래서 모델 파일을 손으로 넣어 줄 수 있게 열어 둔다.
#   <프로젝트 폴더>\모델\model.bin · config.json · tokenizer.json ·
#                      vocabulary.json · preprocessor_config.json
LOCAL_MODEL_DIR = Path(__file__).resolve().parent.parent / "모델"


def local_model_ready() -> bool:
    return (LOCAL_MODEL_DIR / "model.bin").exists()


def content_hash(path: str | Path, chunk: int = 4 << 20) -> str:
    """파일 내용 기반 해시.

    전체를 읽으면 2GB 영상에서 수십 초가 걸린다. 크기 + 앞 4MB + 뒤 4MB 면
    실사용에서 충돌이 날 일이 없고 즉시 끝난다.
    """
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, os.SEEK_END)
            h.update(f.read(chunk))
    return h.hexdigest()[:20]


def _cache_path(key: str, model: str) -> Path:
    d = WORK_DIR / "asr"
    d.mkdir(parents=True, exist_ok=True)
    safe = model.replace("/", "_")
    return d / f"{key}_{safe}.json"


def load_cached(path: str | Path, model: str = DEFAULT_MODEL) -> Transcript | None:
    cp = _cache_path(content_hash(path), model)
    if not cp.exists():
        return None
    try:
        return Transcript.from_dict(json.loads(cp.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, OSError):
        return None  # 캐시가 깨졌으면 그냥 다시 뽑는다


def _get_model(model: str, device: str, compute_type: str):
    """모델은 한 번만 로드해서 재사용. 로딩만 수십 초 걸린다."""
    global _model, _model_key
    key = (model, device, compute_type)
    if _model is not None and _model_key == key:
        return _model
    from faster_whisper import WhisperModel

    # 손으로 넣어 둔 모델 폴더가 있으면 그쪽을 연다. 캐시 키와 화면 표시는
    # 원래 이름("large-v3-turbo")을 그대로 두어야 하므로 여는 순간에만 바꾼다.
    src = model
    if model == "large-v3-turbo" and local_model_ready():
        src = str(LOCAL_MODEL_DIR)

    _model = WhisperModel(src, device=device, compute_type=compute_type,
                          cpu_threads=min(os.cpu_count() or 4, 16))
    _model_key = key
    return _model


def _pick_device() -> tuple[str, str]:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


def transcribe_sync(
    path: str | Path,
    model: str = DEFAULT_MODEL,
    language: str = "ko",
    on_progress=None,
    duration_us: int = 0,
) -> Transcript:
    """블로킹 ASR. 서버에서는 transcribe() 를 쓸 것."""
    path = Path(path)
    device, compute = _pick_device()
    m = _get_model(model, device, compute)

    segments, info = m.transcribe(
        str(path),
        language=language,
        word_timestamps=True,          # 단어 타임스탬프 — 자막 싱크에 필요
        vad_filter=True,               # 무음에서 헛것을 받아적는 걸 막는다
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,  # 켜면 한 번 헛디딜 때 뒤가 줄줄이 망가진다
    )

    total = duration_us or int((info.duration or 0) * US)
    segs: list[Seg] = []
    for s in segments:  # generator — 여기서 실제 연산이 돈다
        text = (s.text or "").strip()
        if not text:
            continue
        words = [
            Word(start=int(round(w.start * US)),
                 end=int(round(w.end * US)),
                 text=(w.word or "").strip())
            for w in (s.words or []) if (w.word or "").strip()
        ]
        segs.append(Seg(
            start=int(round(s.start * US)),
            end=int(round(s.end * US)),
            text=text,
            words=words,
        ))
        if on_progress and total:
            on_progress(min(1.0, (s.end * US) / total))

    return Transcript(segs=segs, language=info.language or language,
                      duration_us=total, model=model)


async def transcribe(
    path: str | Path,
    model: str = DEFAULT_MODEL,
    language: str = "ko",
    use_cache: bool = True,
    on_progress=None,
    duration_us: int = 0,
) -> tuple[Transcript, bool]:
    """(대본, 캐시적중여부). ASR 은 Lock 으로 직렬화된다."""
    path = Path(path)
    key = content_hash(path)

    if use_cache:
        cached = load_cached(path, model)
        if cached is not None:
            return cached, True

    async with _ASR_LOCK:
        # Lock 을 기다리는 동안 다른 요청이 같은 파일을 끝냈을 수 있다
        if use_cache:
            cached = load_cached(path, model)
            if cached is not None:
                return cached, True

        loop = asyncio.get_running_loop()
        tr = await loop.run_in_executor(
            None,
            lambda: transcribe_sync(path, model, language, on_progress, duration_us),
        )

    if use_cache:
        try:
            _cache_path(key, model).write_text(
                json.dumps(tr.to_dict(), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # 캐시 저장 실패는 치명적이지 않다
    return tr, False
