"""Read-only, paged comparisons of observed paths and logical sizes, not contents."""
from contextlib import contextmanager
import csv
import io
import json
import os
from pathlib import Path
import sqlite3


class ComparisonError(ValueError):
    pass


class ScopeNotFound(ComparisonError):
    pass


CHANGES = """WITH changes AS (
    SELECT b.path, CASE WHEN a.path IS NULL THEN 'removed' ELSE 'resized' END kind,
           b.size baseline_size, a.size current_size,
           coalesce(a.size,0)-b.size delta_bytes
    FROM main.files b LEFT JOIN newer.files a ON a.path=b.path
    WHERE a.path IS NULL OR a.size!=b.size
    UNION ALL
    SELECT a.path, 'added', NULL, a.size, a.size
    FROM newer.files a LEFT JOIN main.files b ON b.path=a.path WHERE b.path IS NULL
) """


def lexical(path):
    # Do not resolve/stat the scanned source, which may no longer exist.
    return Path(os.path.normpath(str(path)))


@contextmanager
def session(baseline, current, selected="", query="", kind="all"):
    if kind not in ("all", "added", "removed", "resized"):
        raise ComparisonError("未知变化类型")
    # Only inventory paths supplied by the application are opened, both read-only.
    # Starlette may resume a synchronous streaming generator on a different
    # worker thread. This private connection is consumed serially, never shared.
    db = sqlite3.connect(Path(baseline).absolute().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        db.execute("ATTACH DATABASE ? AS newer", (Path(current).absolute().as_uri() + "?mode=ro",))
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        summaries = []
        for schema in ("main", "newer"):
            row = db.execute(f"SELECT value FROM {schema}.metadata WHERE key='summary'").fetchone()
            if row is None:
                raise ComparisonError("扫描清单未完成，不能比较")
            value = json.loads(row[0])
            if not value.get("details_complete"):
                raise ComparisonError("扫描没有完整保存已观察文件的明细，不能比较")
            summaries.append(value)
        old, new = summaries
        root = lexical(old["root"])
        if root != lexical(new["root"]):
            raise ComparisonError("仅能比较相同扫描根目录；不同范围不能解释为文件增删")
        scope = lexical(selected) if selected else root
        if not scope.is_relative_to(root):
            raise ComparisonError("筛选目录必须位于扫描根目录内")
        known = any(db.execute(f"SELECT 1 FROM {schema}.directories WHERE path=?", (str(scope),)).fetchone()
                    for schema in ("main", "newer"))
        if not known:
            raise ScopeNotFound("目录未出现在任一历史扫描清单中")
        prefix = str(scope).rstrip("\\/") + os.sep
        where = "substr(path,1,?)=? AND instr(lower(path),lower(?))>0"
        args = [len(prefix), prefix, query]
        if kind != "all":
            where += " AND kind=?"
            args.append(kind)
        coverage = all(v.get("coverage_complete", False) for v in summaries)
        info = {"root": str(root), "path": str(scope), "coverage_complete": coverage,
                "baseline": {k: old.get(k) for k in ("finished_at", "complete", "coverage_complete", "files", "errors", "skipped")},
                "current": {k: new.get(k) for k in ("finished_at", "complete", "coverage_complete", "files", "errors", "skipped")},
                "warnings": [] if coverage else ["至少一次扫描覆盖不完整；新增或未再观察到可能来自取消、权限或扫描范围差异，不能断言真实增删。"],
                "measurement": "仅比较同路径的已观察逻辑大小；未再观察到不证明已删除，同大小内容变化不检测，扫描非原子快照。"}
        yield db, CHANGES + "SELECT * FROM changes WHERE " + where, args, info
    finally:
        db.close()


def compare(baseline, current, selected="", query="", kind="all", offset=0, limit=100):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ComparisonError("分页范围无效")
    with session(baseline, current, selected, query, kind) as (db, sql, args, info):
        counts = dict.fromkeys(("added", "removed", "resized"), 0)
        delta = 0
        # Group in SQLite; only aggregate scalars and the selected page reach Python.
        for row in db.execute("SELECT kind,count(*) n,sum(delta_bytes) delta FROM (" + sql + ") GROUP BY kind", args):
            counts[row["kind"]] = row["n"]
            delta += row["delta"]
        total = sum(counts.values())
        items = [dict(r) for r in db.execute(sql + " ORDER BY path COLLATE BINARY LIMIT ? OFFSET ?", args + [limit, offset])]
        return {**info, "items": items, "counts": counts, "delta_bytes": delta,
                "total": total, "offset": offset, "limit": limit, "has_more": offset + limit < total}


def export(baseline, current, selected="", query="", kind="all"):
    # session validates before the first byte, also when called directly.
    with session(baseline, current, selected, query, kind) as (db, sql, args, info):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["path", "kind", "baseline_size", "current_size", "delta_bytes",
                         "baseline_coverage_complete", "current_coverage_complete"])
        yield "\ufeff" + stream.getvalue()
        for row in db.execute(sql + " ORDER BY path COLLATE BINARY", args):
            stream.seek(0)
            stream.truncate(0)
            path = row["path"]
            if path.lstrip().startswith(("=", "+", "-", "@")) or path.startswith(("\t", "\r", "\n")):
                path = "'" + path
            writer.writerow([path, row["kind"], row["baseline_size"], row["current_size"], row["delta_bytes"],
                             bool(info["baseline"]["coverage_complete"]), bool(info["current"]["coverage_complete"])])
            yield stream.getvalue()
