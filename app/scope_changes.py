"""Compare saved file metadata, before unrelated historical rows are reused.

No source filesystem access or content hashes. The caller supplies the same
baseline read transaction used for the refresh and an observed-only inventory.
Both connections inherit the refresh SQLite progress/timeout handler.
"""
import os


def summarize(source, observed, selected, checkpoint):
    prefix = str(selected) + os.sep
    checkpoint()
    observed.execute('''CREATE TEMP TABLE previous_scope(
        path TEXT PRIMARY KEY,size INTEGER,device INTEGER,inode INTEGER,fingerprint TEXT)''')
    cursor = source.execute('''SELECT path,size,device,inode,fingerprint FROM files
        WHERE substr(path,1,?)=?''', (len(prefix), prefix))
    while rows := cursor.fetchmany(1000):
        checkpoint()
        observed.executemany('INSERT INTO previous_scope VALUES(?,?,?,?,?)', rows)
    checkpoint()
    before_files, before_bytes = observed.execute(
        'SELECT count(*),coalesce(sum(size),0) FROM previous_scope').fetchone()
    after_files, after_bytes = observed.execute(
        'SELECT count(*),coalesce(sum(size),0) FROM files').fetchone()
    # Exclusive precedence: replacement can also resize/retimestamp a file.
    # Zero/unknown inode is not evidence of a stable, changed file identity.
    rows = observed.execute('''WITH classified AS (
        SELECT CASE
            WHEN p.path IS NULL THEN 'added'
            WHEN p.inode!=0 AND n.inode!=0 AND (p.device!=n.device OR p.inode!=n.inode) THEN 'replaced'
            WHEN p.size!=n.size THEN 'resized'
            WHEN p.fingerprint!=n.fingerprint THEN 'metadata_changed'
            ELSE 'unchanged' END kind
        FROM files n LEFT JOIN previous_scope p ON n.path=p.path
        UNION ALL
        SELECT 'removed' FROM previous_scope p LEFT JOIN files n ON p.path=n.path WHERE n.path IS NULL
    ) SELECT kind,count(*) FROM classified GROUP BY kind''')
    counts = dict.fromkeys(('added', 'removed', 'replaced', 'resized', 'metadata_changed', 'unchanged'), 0)
    for kind, count in rows:
        counts[kind] = count
    checkpoint()
    observed.execute('DROP TABLE previous_scope')
    return {'schema_version': 1, 'basis': 'saved_file_metadata', 'content_verified': False,
            'counts': counts, 'before_files': before_files, 'after_files': after_files,
            'before_bytes': before_bytes, 'after_bytes': after_bytes,
            'delta_bytes': after_bytes-before_bytes}
