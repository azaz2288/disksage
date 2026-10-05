from pathlib import Path
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from .common import prepare, mount_ui, database, data_root
from .engine import scan, safe_chain, fingerprint, cleanup_eligible
from .llm import generate, provider_status


class ScanRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class CleanupRequest(BaseModel):
    scan_id: str
    ids: list[str] = Field(min_length=1, max_length=500)
    confirm: bool = False


def create_app(root=None, temp_roots=None):
    root = Path(root or data_root("disksage"))
    roots = [Path(p).absolute() for p in (temp_roots or [tempfile.gettempdir()])]
    app = prepare(FastAPI(title="DiskSage", version="0.1.0"), root)
    lock = threading.RLock()
    jobs = {}
    with database(root) as db:
        db.execute("CREATE TABLE IF NOT EXISTS moves(id TEXT PRIMARY KEY, source TEXT, destination TEXT, size INTEGER, fingerprint TEXT, state TEXT, created REAL)")

    @app.get("/api/config")
    def config():
        return {"default_path": str(Path.home()), "temp_roots": [str(p) for p in roots], "llm": provider_status()}

    @app.post("/api/scans")
    def start(body: ScanRequest):
        path = Path(body.path).absolute()
        if not path.is_dir():
            raise HTTPException(400, "扫描目录不存在")
        with lock:
            if any(j["state"] == "running" for j in jobs.values()):
                raise HTTPException(409, "已有扫描进行中，请等待或取消")
            # Bound in-memory retained results.
            if len(jobs) >= 10:
                del jobs[next(iter(jobs))]
            ident = uuid.uuid4().hex
            jobs[ident] = {"state": "running", "progress": {}, "cancel": threading.Event()}
        def update(value):
            with lock:
                jobs[ident]["progress"] = value
        def run():
            try:
                result = scan(path, roots, jobs[ident]["cancel"], update)
                with lock:
                    jobs[ident].update(state="completed" if result["complete"] else "partial", result=result)
            except (ValueError, OSError) as error:
                with lock:
                    jobs[ident].update(state="failed", error=str(error))
        threading.Thread(target=run, daemon=True).start()
        return {"id": ident}

    @app.get("/api/scans/{ident}")
    def status(ident: str):
        with lock:
            if ident not in jobs:
                raise HTTPException(404, "扫描不存在或服务已重启，请重新扫描")
            return {k: v for k, v in jobs[ident].items() if k != "cancel"}

    @app.post("/api/scans/{ident}/cancel")
    def cancel(ident: str):
        with lock:
            if ident not in jobs:
                raise HTTPException(404, "扫描不存在")
            jobs[ident]["cancel"].set()
        return {"ok": True}

    @app.post("/api/cleanup")
    def cleanup(body: CleanupRequest):
        if not body.confirm:
            raise HTTPException(400, "必须确认清理预览")
        with lock:
            job = jobs.get(body.scan_id, {})
            if job.get("state") not in {"completed", "partial"}:
                raise HTTPException(409, "请先完成扫描")
            candidates = {c["id"]: c for c in job["result"]["candidates"]}
            if len(set(body.ids)) != len(body.ids) or any(i not in candidates for i in body.ids):
                raise HTTPException(400, "候选记录不匹配，禁止任意路径清理")
            # Validate entire selection before moving the first item.
            prepared = []
            for ident in body.ids:
                candidate = candidates[ident]
                path = Path(candidate["path"])
                try:
                    info = path.lstat()
                    if fingerprint(info) != candidate["fingerprint"] or not cleanup_eligible(path, info, roots, time.time()):
                        raise OSError("changed")
                    allowed = next(r for r in roots if safe_chain(path, r))
                    quarantine = allowed / ".disksage-quarantine"
                    quarantine.mkdir(exist_ok=True)
                    if not safe_chain(quarantine, allowed):
                        raise OSError("unsafe quarantine")
                    destination = quarantine / ident
                    if destination.exists():
                        raise OSError("collision")
                    prepared.append((candidate, path, destination))
                except (OSError, StopIteration):
                    raise HTTPException(409, "文件已变化、已被清理或路径不安全，请重新扫描") from None
            outcomes = []
            for candidate, path, destination in prepared:
                ident = candidate["id"]
                with database(root) as db:
                    db.execute("INSERT INTO moves VALUES(?,?,?,?,?,?,?)", (ident, str(path), str(destination), candidate["size"], candidate["fingerprint"], "pending", time.time()))
                try:
                    if fingerprint(path.lstat()) != candidate["fingerprint"]:
                        raise OSError("changed")
                    os.rename(path, destination)
                    state = "quarantined"
                except OSError:
                    state = "failed"
                with database(root) as db:
                    db.execute("UPDATE moves SET state=? WHERE id=?", (state, ident))
                outcomes.append({"id": ident, "state": state})
            return {"items": outcomes, "note": "隔离文件仍占用磁盘；本版本不永久删除"}

    @app.get("/api/history")
    def history():
        with database(root) as db:
            # Reconcile a crash after rename but before updating the intent record.
            for row in db.execute("SELECT * FROM moves WHERE state='pending'").fetchall():
                source, dest = Path(row["source"]), Path(row["destination"])
                if not source.exists() and dest.exists():
                    db.execute("UPDATE moves SET state='quarantined' WHERE id=?", (row["id"],))
            return [dict(r) for r in db.execute("SELECT * FROM moves ORDER BY created DESC LIMIT 100")]

    @app.post("/api/history/{ident}/restore")
    def restore(ident: str):
        with lock, database(root) as db:
            row = db.execute("SELECT * FROM moves WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise HTTPException(404, "记录不存在")
            source, destination = Path(row["source"]), Path(row["destination"])
            if row["state"] != "quarantined" or source.exists():
                raise HTTPException(409, "无法恢复：原位置有文件或记录不在隔离状态")
            allowed = next((r for r in roots if safe_chain(destination, r) and safe_chain(source.parent, r)), None)
            if not allowed:
                raise HTTPException(409, "恢复路径不安全")
            try:
                if fingerprint(destination.lstat()) != row["fingerprint"]:
                    raise OSError("changed")
                # Exclusive destination creation prevents overwriting newly created files.
                os.link(destination, source)
                destination.unlink()
            except OSError:
                raise HTTPException(409, "隔离文件已变化、磁盘不支持硬链接或原位置发生冲突") from None
            db.execute("UPDATE moves SET state='restored' WHERE id=?", (ident,))
            return {"ok": True}

    @app.get("/api/scans/{ident}/ai-preview")
    def ai_preview(ident: str):
        result = status(ident).get("result")
        if not result:
            raise HTTPException(409, "请先完成扫描")
        return {"logical_bytes": result["bytes"], "file_count": result["files"], "unreadable_count": result["errors"],
                "temporary_candidate_count": len(result["candidates"]),
                "temporary_candidate_bytes": sum(c["size"] for c in result["candidates"]),
                "largest_file_bytes": [f["size"] for f in result["largest"][:10]], "paths_included": False}

    @app.post("/api/scans/{ident}/advice")
    def advice(ident: str):
        summary = ai_preview(ident)
        answer = generate("你是磁盘分析助手。仅解释汇总统计与保守建议；不能判断具体文件可删，不得生成执行命令。提醒隔离不会释放空间。本系统不提供永久删除。", json.dumps(summary, ensure_ascii=False))
        return {"answer": answer, "sent": summary}

    mount_ui(app)
    return app


app = create_app()
