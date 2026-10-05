from pathlib import Path
from contextlib import contextmanager
import os
import sqlite3
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


def prepare(app: FastAPI, root: Path):
    root.mkdir(parents=True, exist_ok=True)
    @app.middleware("http")
    async def local_only(request, call_next):
        host = request.url.hostname
        if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "拒绝跨站操作"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
        return response
    return app


def mount_ui(app):
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="ui")


@contextmanager
def database(root):
    connection = sqlite3.connect(root / "app.db", timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_upload(file: UploadFile, maximum: int):
    raw = file.file.read(maximum + 1)
    if len(raw) > maximum:
        raise HTTPException(413, "文件超过大小限制")
    if not raw:
        raise HTTPException(400, "不能导入空文件")
    return raw


def decode_text(raw: bytes):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    else:
        encodings = ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            if "\x00" in text:
                continue
            return text.replace("\r\n", "\n").replace("\r", "\n"), encoding
        except UnicodeError:
            pass
    raise HTTPException(400, "无法识别文本编码，请转换为 UTF-8")


def data_root(name):
    return Path(os.environ.get("APP_DATA_DIR", str(Path(__file__).parent.parent / "data"))).resolve()
