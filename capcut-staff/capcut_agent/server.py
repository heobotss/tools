"""로컬 웹 서버. FastAPI + 정적 HTML 1장.

    python -m capcut_agent.server        → http://127.0.0.1:8777

바깥에 열지 않는다. 127.0.0.1 전용이고 인증도 없다 — 로컬 도구다.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import asr as asr_mod
from . import slides as _slides
from .config import WORK_DIR, find_draft_root
from .style import PRESETS
from .pipeline import STEP_LABEL, STEPS, Options, run

APP_DIR = Path(__file__).parent
STATIC = APP_DIR / "static"
UPLOADS = WORK_DIR / "uploads"

# job_id → {"path": Path, "name": str}
JOBS: dict[str, dict] = {}


def _load_jobs() -> None:
    """서버를 다시 띄워도 이전 업로드본을 계속 쓸 수 있게 복구한다."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    for p in UPLOADS.iterdir():
        if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv", ".m4v", ".webm"):
            JOBS.setdefault(p.stem, {"path": p, "name": p.name})


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _load_jobs()
    yield


app = FastAPI(title="캡컷 에이전트", docs_url=None, redoc_url=None,
              lifespan=_lifespan)


# ── 정적 ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/env")
async def env() -> JSONResponse:
    try:
        root = str(find_draft_root())
        ok = True
    except FileNotFoundError as e:
        root, ok = str(e), False
    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = f"cuda ({torch.cuda.get_device_name(0)})"
    except ImportError:
        pass
    return JSONResponse({
        "draft_root": root,
        "draft_root_ok": ok,
        "model": asr_mod.DEFAULT_MODEL,
        "device": device,
        "python": sys.version.split()[0],
        "steps": [{"key": k, "label": STEP_LABEL[k]} for k in STEPS],
        # 용도별 프리셋. 배속이 여기서 갈린다 (유튜브 1.2× / VOD 1.0×)
        "decks": [{"key": d.key, "label": d.label} for d in _slides.DECKS],
        "presets": [{"key": p.key, "label": p.label,
                     "speed": p.speed, "note": p.note}
                    for p in PRESETS.values()],
        # 화면을 열었을 때 미리 골라져 있을 용도.
        # 강의만 편집하는 PC 는 실행 bat 에 `set CAPCUT_PRESET=vod` 한 줄을 넣어
        # 기본값을 바꾼다 (유튜브로 둔 채 돌려 장표가 안 붙는 사고를 막는다).
        "preset": (os.environ.get("CAPCUT_PRESET", "").strip()
                   if os.environ.get("CAPCUT_PRESET", "").strip() in PRESETS
                   else next(iter(PRESETS))),
    })


# ── 업로드 ────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    ext = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".mkv", ".m4v", ".webm"):
        raise HTTPException(400, f"지원하지 않는 형식입니다: {ext}")

    UPLOADS.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    dest = UPLOADS / f"{job_id}{ext}"

    # 큰 파일이 올 수 있으니 통째로 메모리에 올리지 않는다
    with open(dest, "wb") as out:
        while chunk := await file.read(1 << 22):
            out.write(chunk)

    JOBS[job_id] = {"path": dest, "name": file.filename or dest.name}
    return JSONResponse({
        "job_id": job_id,
        "name": JOBS[job_id]["name"],
        "size": dest.stat().st_size,
    })


@app.get("/api/jobs/{job_id}/video")
async def video(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "업로드를 찾을 수 없습니다")
    # FileResponse 가 Range 요청을 처리해준다 → 프리뷰에서 탐색이 된다
    return FileResponse(job["path"], media_type="video/mp4")


# ── 처리 (SSE) ────────────────────────────────────────────────────
def _parse_opts(raw: str) -> Options:
    try:
        d = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        d = {}
    o = Options()
    for k, v in d.items():
        if k == "protect" and isinstance(v, list):
            o.protect = [(float(a), float(b)) for a, b in v]
        elif hasattr(o, k) and v is not None:
            cur = getattr(o, k)
            try:
                setattr(o, k, type(cur)(v) if not isinstance(cur, bool) else bool(v))
            except (TypeError, ValueError):
                pass
    return o


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str, opts: str = Query("")):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "업로드를 찾을 수 없습니다")

    options = _parse_opts(opts)
    if not options.draft_name:
        # 업로드본은 job id 로 저장돼 있다. 그대로 두면 드래프트 이름이
        # "a858bdaf91ce_0815_2025" 처럼 나와서 목록에서 알아볼 수가 없다.
        stem = Path(job["name"]).stem or "video"
        options.draft_name = f"{stem}_{time.strftime('%m%d_%H%M')}"

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def emit(ev: dict) -> None:
        await queue.put(ev)

    async def worker() -> None:
        try:
            await run(job["path"], options, emit)
        except Exception as e:  # 파이프라인이 죽어도 UI 는 이유를 알아야 한다
            await queue.put({"type": "error",
                             "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put(None)

    async def gen():
        task = asyncio.create_task(worker())
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield _sse(ev)
            yield _sse({"type": "end"})
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ── 편의 ─────────────────────────────────────────────────────────
@app.get("/api/drafts")
async def drafts() -> JSONResponse:
    """캡컷 드래프트 목록. 최근에 손댄 순."""
    import json as _json

    root = find_draft_root()
    out = []
    for d in sorted(root.iterdir(), key=lambda x: x.name):
        f = d / "draft_content.json"
        if not d.is_dir() or d.name.startswith(".") or not f.exists():
            continue
        row = {"name": d.name, "mtime": f.stat().st_mtime}
        try:
            c = _json.loads(f.read_text(encoding="utf-8"))
            vt = [t for t in c["tracks"] if t["type"] == "video"]
            tt = [t for t in c["tracks"] if t["type"] == "text"]
            row["cuts"] = len(vt[0]["segments"])
            row["slides"] = len(vt[1]["segments"]) if len(vt) > 1 else 0
            row["subs"] = len(tt[0]["segments"]) if tt else 0
            row["dur"] = max(g["target_timerange"]["start"]
                             + g["target_timerange"]["duration"]
                             for g in vt[0]["segments"]) / 1_000_000
        except Exception:
            pass
        out.append(row)
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return JSONResponse({"drafts": out, "capcut": _capcut_running()})


def _capcut_running() -> bool:
    from tools.relay_slides import capcut_running
    return capcut_running()


@app.get("/api/relay/stream")
async def relay_stream(draft: str = Query(...), out: str = Query("")):
    """장표만 다시 깔기. 진행 상황을 그대로 흘려보낸다."""
    import contextlib
    import io
    import queue as _q
    import threading

    from tools.relay_slides import run as _relay

    q: _q.Queue = _q.Queue()

    class _Pipe(io.TextIOBase):
        def write(self, txt):
            for line in txt.splitlines():
                if line.strip():
                    q.put(line.rstrip())
            return len(txt)

    def worker():
        try:
            with contextlib.redirect_stdout(_Pipe()):
                code = asyncio.run(_relay(draft, out or f"{draft}_v2", "",
                                          inplace=False, align_subs=True))
            q.put({"done": code == 0})
        except Exception as e:            # noqa: BLE001
            q.put(f"[에러] {e}")
            q.put({"done": False})

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            try:
                item = await asyncio.get_running_loop().run_in_executor(
                    None, q.get, True, 1.0)
            except Exception:
                yield _sse({"type": "ping"})
                continue
            if isinstance(item, dict):
                yield _sse({"type": "done", "ok": item["done"]})
                return
            yield _sse({"type": "line", "text": item})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/open")
async def open_folder(payload: dict) -> JSONResponse:
    """드래프트 폴더를 탐색기로 연다. 드래프트 루트 안쪽만 허용."""
    folder = Path(payload.get("folder", "")).resolve()
    try:
        root = find_draft_root().resolve()
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    if root not in folder.parents or not folder.is_dir():
        raise HTTPException(400, "드래프트 폴더가 아닙니다")

    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return JSONResponse({"ok": True})


@app.delete("/api/jobs/{job_id}")
async def drop_job(job_id: str) -> JSONResponse:
    job = JOBS.pop(job_id, None)
    if job and Path(job["path"]).exists():
        Path(job["path"]).unlink(missing_ok=True)
    return JSONResponse({"ok": True})


def _open_when_ready(port: int, timeout_s: float = 180.0) -> None:
    """포트가 **실제로** 응답할 때부터 브라우저를 연다.

    예전엔 실행 배치가 3초를 기다렸다 브라우저를 띄웠는데, 컴퓨터를 막 켠
    직후에는 모듈 로딩이 그보다 오래 걸려서 "사이트에 연결할 수 없음"이 떴다.
    기다리는 쪽을 서버 자신이 맡으면 그 창이 나올 수가 없다.
    """
    import socket
    import webbrowser

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
        except OSError:
            time.sleep(0.3)


def main() -> None:
    """실행 배치는 한글을 한 글자도 담지 않는다.

    cmd 는 .bat 을 바이트 오프셋으로 되짚어 읽어서, 한글이 섞이면 위치가
    어긋나 REM 주석 줄까지 명령으로 실행해 버린다. 안내 문구는 전부 여기서 찍는다.
    """
    import threading
    import uvicorn

    UPLOADS.mkdir(parents=True, exist_ok=True)
    port = 8777

    print()
    print(f"  캡컷 에이전트  →  http://127.0.0.1:{port}", flush=True)
    if "--no-browser" not in sys.argv:
        print("  준비되면 브라우저가 저절로 열립니다. 잠시 기다려 주세요.", flush=True)
        threading.Thread(target=_open_when_ready, args=(port,),
                         daemon=True).start()
    print()
    print("  [이 창을 닫으면 프로그램이 꺼집니다]", flush=True)
    print()

    # uvicorn 은 바인드 실패를 스스로 삼키고 영문 로그만 남긴다. 미리 본다.
    import socket

    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        print()
        print(f"  [문제] {port} 번 포트를 이미 다른 창이 쓰고 있습니다.")
        print("  켜져 있는 '캡컷 에이전트' 검은 창을 닫고 다시 실행해 주세요.")
        print("  (또는 브라우저에서 http://127.0.0.1:8777 을 그냥 열어보세요)")
        print()
        return
    finally:
        probe.close()

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        print()
        print("  " + "─" * 50)
        print("  서버가 종료되었습니다.")
        print("  위에 Error 나 Traceback 이 보이면 그 내용을 알려주세요.")
        print("  " + "─" * 50)


if __name__ == "__main__":
    main()
