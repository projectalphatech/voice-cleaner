"""Job API for the voice cleaner. Only the Cloudflare Worker should call it.

Run: AUDIO_TOKEN=<secret> uvicorn server:app --host 127.0.0.1 --port 8090
"""
import os
import queue
import re
import secrets
import shutil
import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

import clean

TOKEN = os.environ["AUDIO_TOKEN"]  # refuse to boot without a token
DATA = Path(os.environ.get("AUDIO_DATA", "jobs"))
DATA.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 500 * 2**20

# ponytail: job status lives in memory (lost on restart) and job files are never deleted; add a cron cleanup before real traffic
jobs = {}
q = queue.Queue()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def auth(header):
    if not secrets.compare_digest(header or "", f"Bearer {TOKEN}"):
        raise HTTPException(401)


def work():
    while True:
        jid, opts = q.get()
        job = jobs[jid]
        job["status"] = "processing"
        try:
            job["info"] = clean.clean(str(job["src"]), str(job["dst"]), **opts)
            job["status"] = "done"
        except Exception as e:
            job["status"], job["error"] = "error", str(e)[-500:]
            traceback.print_exc()


threading.Thread(target=work, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True, "queued": q.qsize()}


@app.post("/jobs")
async def create(file: UploadFile = File(...), target: int = Form(-16), denoise: bool = Form(True),
                 leveler: bool = Form(True), fmt: str = Form("mp3"), authorization: str | None = Header(None)):
    auth(authorization)
    if target not in clean.TARGETS or fmt not in clean.FORMATS:
        raise HTTPException(400, "unsupported target or format")
    jid = uuid.uuid4().hex
    d = DATA / jid
    d.mkdir()
    src, size = d / "input", 0  # no extension: ffmpeg probes the content, untrusted names never touch disk
    with open(src, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                break
            f.write(chunk)
    if size > MAX_BYTES or size == 0:
        shutil.rmtree(d)
        raise HTTPException(413 if size else 400, "file too large" if size else "empty file")
    stem = re.sub(r"[^\w-]+", "_", Path(file.filename or "audio").stem)[:60] or "audio"
    jobs[jid] = {"status": "queued", "src": src, "dst": d / f"clean.{fmt}", "name": f"{stem}-clean.{fmt}"}
    q.put((jid, {"target": target, "denoise": denoise, "leveler": leveler}))
    return {"id": jid}


def get(jid, authorization):
    auth(authorization)
    if jid not in jobs:
        raise HTTPException(404)
    return jobs[jid]


@app.get("/jobs/{jid}")
def status(jid: str, authorization: str | None = Header(None)):
    job = get(jid, authorization)
    return {"status": job["status"], "error": job.get("error"), "info": job.get("info"), "position": q.qsize()}


@app.get("/jobs/{jid}/result")
def result(jid: str, authorization: str | None = Header(None)):
    job = get(jid, authorization)
    if job["status"] != "done":
        raise HTTPException(409, "not ready")
    return FileResponse(job["dst"], filename=job["name"])
