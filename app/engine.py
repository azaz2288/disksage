"""Read-only bounded scan and conservative, reversible temp-file cleanup."""
from pathlib import Path
import heapq
import os
import stat
import time
import uuid
import hashlib
from .allocation import physical_size
from .snapshot import Inventory,connect

DAY = 86400


def reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool((getattr(info, "st_file_attributes", 0) or 0) & 0x400)


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


def scan(root: Path, temp_roots, cancel, progress, max_files=None, max_dirs=None, inventory_path=None, exclude_paths=()):
    inventory=Inventory(inventory_path) if inventory_path else None
    try:return _scan(root,temp_roots,cancel,progress,max_files,max_dirs,inventory_path,exclude_paths,inventory)
    finally:
        if inventory:inventory.close()


def _scan(root: Path, temp_roots, cancel, progress, max_files=None, max_dirs=None, inventory_path=None, exclude_paths=(),inventory=None):
    root = root.absolute()
    if not root.is_dir() or not safe_chain(root, Path(root.anchor)):
        raise ValueError("请选择真实目录；不能扫描符号链接或目录联接")
    top = []
    excluded={str(Path(p).absolute()) for p in exclude_paths}
    candidates = []
    sizes = {}
    direct = {}
    items = []
    extensions = {}
    identities = set()
    unique_bytes = allocated_bytes = allocation_errors = 0
    started = time.monotonic()
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
        if (max_dirs is not None and len(sizes)>=max_dirs) or (max_files is not None and file_count>=max_files):
            complete = False
            break
        sizes[path] = 0
        direct[path] = 0
        stack.append((path, True))
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if cancel.is_set() or (max_files is not None and file_count>=max_files):
                        complete = False
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                        item = Path(entry.path)
                        if reparse(info) or entry.name == ".disksage-quarantine" or str(item) in excluded:
                            skipped += 1
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            stack.append((item, False))
                        elif stat.S_ISREG(info.st_mode):
                            info = item.lstat()
                            file_count += 1
                            total += info.st_size
                            sizes[path] += info.st_size
                            direct[path] += info.st_size
                            ext=item.suffix.lower() or '(无扩展名)'
                            bucket=extensions.setdefault(ext,{'extension':ext,'files':0,'bytes':0})
                            bucket['files']+=1;bucket['bytes']+=info.st_size
                            identity=(info.st_dev,info.st_ino)
                            first=inventory.identity(info) if inventory else identity not in identities
                            allocated=None
                            if first:
                                if not inventory:identities.add(identity)
                                unique_bytes+=info.st_size
                                allocated=physical_size(item,info)
                                if allocated is None:allocation_errors+=1
                                else:allocated_bytes+=allocated
                            if not inventory and len(items)<50000:
                                items.append({'path':str(item),'size':info.st_size,'fingerprint':fingerprint(info),'identity':list(identity)})
                            if inventory:inventory.file(item,info,physical_size(item,info) if not first else allocated,fingerprint(info))
                            if file_count%5000==0:progress({'files':file_count,'bytes':total,'errors':errors,'current':str(path)})
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
                    except OSError as error:
                        errors += 1
                        if inventory:inventory.error(entry.path,error)
        except OSError as error:
            errors += 1
            if inventory:inventory.error(path,error)
        progress({"files": file_count, "bytes": total, "errors": errors, "current": str(path)})
    # Recompute bottom-up from direct sizes, also valid for cancelled scans.
    sizes=dict(direct)
    for path in sorted(sizes,key=lambda p:len(p.parts),reverse=True):
        if path.parent in sizes:sizes[path.parent]+=sizes[path]
    result={"root": str(root), "bytes": total, "files": file_count, "errors": errors, "skipped": skipped,
            "complete": complete, "directories": [{"path": str(p), "size": n} for p, n in
                                                   sorted(sizes.items(), key=lambda x: x[1], reverse=True)[:100]],
            "largest": [{"path": p, "size": n} for n, p in sorted(top, reverse=True)], "candidates": candidates,
            "finished_at": time.time(), "elapsed_seconds":round(time.monotonic()-started,3),
            "extensions":sorted(extensions.values(),key=lambda e:e['bytes'],reverse=True),
            "unique_bytes":unique_bytes,"allocated_bytes":allocated_bytes,"allocation_errors":allocation_errors,
            "_items":items if not inventory else [],"_directories":{str(p):n for p,n in sizes.items()} if not inventory else {},"details_complete":bool(inventory) or len(items)==file_count,
            'coverage_complete':complete and errors==0,'inventory_files':file_count if inventory else len(items),
            "measurement": "逻辑大小、去重硬链接大小、系统分配大小（Windows GetCompressedFileSize / Unix st_blocks）；变化中的目录非原子快照"}
    if inventory:
        result['_inventory']=str(inventory_path);inventory.finish(result,sizes)
    return result


def find_duplicates(result, cancel, progress):
    by_size={};errors=0;read_bytes=0
    if result.get('_inventory'):
        with connect(result['_inventory']) as db:
            sizes=[r[0] for r in db.execute('SELECT size FROM files WHERE size>0 AND size<=? GROUP BY size HAVING count(*)>1',(1024**3,))]
            items=[]
            for size in sizes:
                items.extend({'path':r['path'],'size':r['size'],'fingerprint':r['fingerprint'],'identity':[r['device'],r['inode']]} for r in db.execute('SELECT * FROM files WHERE size=?',(size,)))
    else:items=result['_items']
    for item in items:
        if item['size'] and item['size']<=1024**3:by_size.setdefault(item['size'],[]).append(item)
    groups=[]
    for size,items in by_size.items():
        # Hard links are already the same underlying file, not independently reclaimable copies.
        unique={tuple(i['identity']):i for i in items}
        if len(unique)<2:continue
        hashes={}
        for item in unique.values():
            if cancel.is_set() or read_bytes>10*1024**3:return {'groups':groups,'complete':False,'errors':errors,'read_bytes':read_bytes}
            path=Path(item['path'])
            try:
                if not safe_chain(path,Path(result['root'])) or fingerprint(path.lstat())!=item['fingerprint']:raise OSError('changed')
                digest=hashlib.sha256()
                with path.open('rb') as source:
                    while chunk:=source.read(1024*1024):
                        if read_bytes+len(chunk)>10*1024**3:return {'groups':groups,'complete':False,'errors':errors,'read_bytes':read_bytes,'reason':'达到10GiB读取预算'}
                        digest.update(chunk);read_bytes+=len(chunk)
                        if cancel.is_set():break
                if cancel.is_set():return {'groups':groups,'complete':False,'errors':errors,'read_bytes':read_bytes}
                if fingerprint(path.lstat())!=item['fingerprint']:raise OSError('changed')
                hashes.setdefault(digest.hexdigest(),[]).append(item['path'])
            except OSError:errors+=1
            progress({'read_bytes':read_bytes,'errors':errors})
        for digest,paths in hashes.items():
            if len(paths)>1:groups.append({'sha256':digest,'paths':paths,'size':size,'potential_bytes':size*(len(paths)-1)})
    return {'groups':sorted(groups,key=lambda g:g['potential_bytes'],reverse=True),'complete':result['details_complete'],'errors':errors,'read_bytes':read_bytes}
