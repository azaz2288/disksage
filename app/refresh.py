"""Explicit subtree observation, not automatic change detection or a live full scan."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .engine import scan, safe_chain, directory_totals
from .snapshot import connect


def validate(db, selected):
    row = db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()
    value = json.loads(row[0]) if row else None
    if not value or not value.get('coverage_complete') or not value.get('details_complete') or value.get('refresh'):
        raise ValueError('需要可访问范围完整的全量历史基线；不能串联局部刷新')
    root = Path(value['root'])
    selected = Path(selected)
    if not selected.is_absolute() or '..' in selected.parts or selected == root:
        raise ValueError('请选择基线中的子目录；根目录请使用完整扫描')
    try:
        selected.relative_to(root)
    except ValueError:
        raise ValueError('目录不在基线范围内') from None
    if not db.execute('SELECT 1 FROM directories WHERE path=?', (str(selected),)).fetchone():
        raise ValueError('目录未登记在基线中')
    if not safe_chain(selected, root) or not selected.is_dir():
        raise ValueError('选中目录已消失或变为链接；请重新完整扫描')
    return value


def check_scope(baseline, selected):
    with connect(baseline) as source:
        return validate(source, selected)


def refresh(baseline, selected, target, temp_roots, cancel, progress, exclude_paths=()):
    baseline, selected, target = Path(baseline), Path(selected), Path(target)
    if target.exists() or target.is_symlink() or target.absolute() == baseline.absolute():
        raise ValueError('输出已存在，拒绝覆盖')
    # Keep one baseline read transaction across selection, scan and merge.
    with connect(baseline) as source:
        source.execute('BEGIN')
        old = validate(source, selected)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.refresh-', dir=target.parent) as workspace:
            staging = Path(workspace) / 'inventory.db'
            observed = scan(selected, temp_roots, cancel, progress, inventory_path=staging, exclude_paths=exclude_paths)
            if cancel.is_set() or not observed['coverage_complete']:
                raise ValueError('局部扫描已取消或覆盖不完整，未发布新清单；基线保留')
            prefix = str(selected) + os.sep
            outside = 'path!=? AND substr(path,1,?)!=?'
            args = (str(selected), len(prefix), prefix)
            reused = source.execute('SELECT count(*) FROM files WHERE ' + outside, args).fetchone()[0]
            with closing(sqlite3.connect(staging)) as output:
                output.row_factory = sqlite3.Row
                output.execute('BEGIN')
                # Bounded batches; never touch or stat reused source paths.
                cursor = source.execute('SELECT * FROM files WHERE ' + outside, args)
                while rows := cursor.fetchmany(1000):
                    if cancel.is_set(): raise ValueError('刷新已取消，未发布新清单')
                    output.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?)', rows)
                old_dirs = [Path(r[0]) for r in source.execute('SELECT path FROM directories WHERE ' + outside, args)]
                new_dirs = [Path(r[0]) for r in output.execute('SELECT path FROM directories')]
                direct = {p: 0 for p in old_dirs + new_dirs}
                for row in output.execute('SELECT parent,sum(size) size FROM files GROUP BY parent'):
                    direct[Path(row['parent'])] = row['size']
                sizes = directory_totals(direct)
                output.execute('DELETE FROM directories')
                output.executemany('INSERT INTO directories VALUES(?,?,?,?)',
                                   ((str(p), str(p.parent), p.name, size) for p, size in sizes.items()))
                output.execute('DELETE FROM identities')
                output.execute('INSERT INTO identities SELECT DISTINCT device,inode FROM files')
                count, total = output.execute('SELECT count(*),coalesce(sum(size),0) FROM files').fetchone()
                unique, allocated, failures = output.execute('''SELECT coalesce(sum(size),0),
                    coalesce(sum(allocated),0),coalesce(sum(allocated IS NULL),0) FROM
                    (SELECT max(size) size,max(allocated) allocated FROM files GROUP BY device,inode)''').fetchone()
                value = {**observed, 'root': old['root'], 'files': count, 'bytes': total,
                         'unique_bytes': unique, 'allocated_bytes': allocated, 'allocation_errors': failures,
                         'coverage_complete': False, 'inventory_files': count, 'candidates': [],
                         'directories': [{'path': str(p), 'size': size} for p, size in sorted(sizes.items(), key=lambda r: (-r[1], str(r[0])))[:100]],
                         'largest': [dict(r) for r in output.execute('SELECT path,size FROM files ORDER BY size DESC,path LIMIT 200')],
                         'extensions': [dict(r) for r in output.execute('SELECT extension,count(*) files,sum(size) bytes FROM files GROUP BY extension ORDER BY bytes DESC,extension')],
                         '_inventory': str(target),
                         'refresh': {'baseline_id': baseline.stem, 'baseline_finished_at': old['finished_at'],
                                     'path': str(selected), 'scanned_files': observed['files'], 'reused_files': reused,
                                     'observed_at': observed['finished_at'], 'selected_coverage_complete': True,
                                     'outside_revalidated': False},
                         'measurement': '局部刷新混合时间清单：仅选中子树重新观察，其余沿用基线；不是全根当前覆盖，无清理候选'}
                output.execute("UPDATE metadata SET value=? WHERE key='summary'",
                               (json.dumps({k:v for k,v in value.items() if not k.startswith('_')}, ensure_ascii=False),))
                output.commit()
            if cancel.is_set(): raise ValueError('刷新已取消，未发布新清单')
            with staging.open('r+b') as ready:
                os.fsync(ready.fileno())
            if cancel.is_set(): raise ValueError('刷新已取消，未发布新清单')
            # Exclusive publication also protects against a late competing writer.
            os.link(staging, target)
            return value
