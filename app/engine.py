"""Read-only bounded scan and conservative, reversible temp-file cleanup."""
from pathlib import Path
import heapq
import os
import stat
import time
import uuid

DAY = 86400


def reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_chain(path: Path, root: Path):
    """Reject links/junctions in every component beneath the allowed root."""
    try:
        path.relative_to(root)
        current = path
        while True:
            if reparse(current.lstat()):
                return False
            if current == root:
                return True
            current = current.parent
    except (OSError, ValueError):
        return False


def fingerprint(info):
    return f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"


def cleanup_eligible(path, info, roots, now):
    if path.suffix.lower() not in {".tmp", ".log"} or now - info.st_mtime < 7 * DAY:
        return False
    return any(safe_chain(path, root) for root in roots)


def scan(root: Path, temp_roots, cancel, progress):
    root = root.absolute()
    if not root.is_dir() or not safe_chain(root, Path(root.anchor)):
        raise ValueError("请选择真实目录；不能扫描符号链接或目录联接")
    top = []
    candidates = []
    sizes = {}
    file_count = errors = skipped = 0
    total = 0
    now = time.time()
    stack = [(root, False)]
    complete = True
    while stack:
        if cancel.is_set():
            complete = False
            break
        path, visited = stack.pop()
        if visited:
            parent = path.parent
            if parent in sizes:
                sizes[parent] += sizes[path]
            continue
        if len(sizes) >= 50000 or file_count >= 500000:
            complete = False
            break
        sizes[path] = 0
        stack.append((path, True))
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if cancel.is_set() or file_count >= 500000:
                        complete = False
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                        item = Path(entry.path)
                        if reparse(info) or entry.name == ".disksage-quarantine":
                            skipped += 1
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            stack.append((item, False))
                        elif stat.S_ISREG(info.st_mode):
                            file_count += 1
                            total += info.st_size
                            sizes[path] += info.st_size
                            pair = (info.st_size, str(item))
                            if len(top) < 200:
                                heapq.heappush(top, pair)
                            elif pair > top[0]:
                                heapq.heapreplace(top, pair)
                            if len(candidates) < 500 and cleanup_eligible(item, info, temp_roots, now):
                                # Windows DirEntry.stat may report inode=0; match the lstat API used at execution.
                                snapshot = item.lstat()
                                if cleanup_eligible(item, snapshot, temp_roots, now):
                                    candidates.append({"id": uuid.uuid4().hex, "path": str(item), "size": snapshot.st_size,
                                                       "fingerprint": fingerprint(snapshot), "age_days": int((now-snapshot.st_mtime)/DAY)})
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        progress({"files": file_count, "bytes": total, "errors": errors, "current": str(path)})
    return {"root": str(root), "bytes": total, "files": file_count, "errors": errors, "skipped": skipped,
            "complete": complete, "directories": [{"path": str(p), "size": n} for p, n in
                                                   sorted(sizes.items(), key=lambda x: x[1], reverse=True)[:100]],
            "largest": [{"path": p, "size": n} for n, p in sorted(top, reverse=True)], "candidates": candidates,
            "finished_at": now, "measurement": "逻辑文件大小；硬链接可能重复统计，压缩/稀疏文件不代表实际分配空间"}
