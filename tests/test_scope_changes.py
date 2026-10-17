"""Metadata observations only, using synthetic inventories and temporary trees."""
import hashlib
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

_bootstrap = tempfile.TemporaryDirectory(prefix='disksage-changes-bootstrap-')
os.environ['APP_DATA_DIR'] = _bootstrap.name
from fastapi.testclient import TestClient
from app.engine import scan
from app.main import create_app
from app.refresh import refresh


class ScopeChangesTests(unittest.TestCase):
    def inventories(self, before, after):
        source, output = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
        self.addCleanup(source.close)
        self.addCleanup(output.close)
        for db, rows in ((source, before), (output, after)):
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE files(path TEXT PRIMARY KEY,size INTEGER,device INTEGER,inode INTEGER,fingerprint TEXT)')
            db.executemany('INSERT INTO files VALUES(?,?,?,?,?)', rows)
        return source, output

    def classify(self, before, after):
        from app.scope_changes import summarize
        source, output = self.inventories(before, after)
        return summarize(source, output, Path(os.sep) / 'part', lambda: None)

    def test_exclusive_precedence_and_component_boundary(self):
        p = str(Path(os.sep) / 'part') + os.sep
        old = [(p+n, size, 1, ino, fp) for n, size, ino, fp in
               [('gone', 7, 1, 'g'), ('replace', 2, 2, 'r'), ('grow', 3, 3, 's'),
                ('time', 4, 4, 'old'), ('same', 5, 5, 'same')]]
        old.append((str(Path(os.sep) / 'part-two' / 'external'), 1000, 1, 9, 'outside'))
        new = [(p+n, size, 1, ino, fp) for n, size, ino, fp in
               [('added', 9, 9, 'a'), ('replace', 6, 22, 'new'), ('grow', 8, 3, 'changed'),
                ('time', 4, 4, 'new'), ('same', 5, 5, 'same')]]
        result = self.classify(old, new)
        self.assertEqual(result['counts'], dict(added=1, removed=1, replaced=1, resized=1, metadata_changed=1, unchanged=1))
        self.assertEqual((result['before_files'], result['after_files']), (5, 5))
        self.assertEqual((result['before_bytes'], result['after_bytes'], result['delta_bytes']), (21, 32, 11))
        self.assertFalse(result['content_verified'])

    def test_empty_and_unknown_identity_are_not_claimed_as_replacements(self):
        self.assertEqual(sum(self.classify([], [])['counts'].values()), 0)
        p = str(Path(os.sep) / 'part' / 'zero')
        for old_inode, new_inode in ((0, 4), (4, 0), (0, 0)):
            result = self.classify([(p, 0, 1, old_inode, 'old')], [(p, 0, 2, new_inode, 'new')])
            self.assertEqual(result['counts']['metadata_changed'], 1)
            self.assertEqual(result['counts']['replaced'], 0)

    def test_seeded_independent_dictionary_reference(self):
        rng = random.Random(1703)
        p = str(Path(os.sep) / 'part') + os.sep
        for _ in range(40):
            before, after = {}, {}
            for i in range(55):
                for rows in (before, after):
                    if rng.random() > .25:
                        rows[p+str(i)] = (rng.randrange(8), rng.randrange(2), rng.randrange(8), str(rng.randrange(4)))
            expected = dict.fromkeys(('added', 'removed', 'replaced', 'resized', 'metadata_changed', 'unchanged'), 0)
            for path in before.keys() | after.keys():
                a, b = before.get(path), after.get(path)
                if a is None: kind = 'added'
                elif b is None: kind = 'removed'
                elif a[2] and b[2] and a[1:3] != b[1:3]: kind = 'replaced'
                elif a[0] != b[0]: kind = 'resized'
                elif a[3] != b[3]: kind = 'metadata_changed'
                else: kind = 'unchanged'
                expected[kind] += 1
            actual = self.classify([(p, *r) for p, r in before.items()], [(p, *r) for p, r in after.items()])
            self.assertEqual(actual['counts'], expected)
            self.assertEqual(actual['delta_bytes'], sum(r[0] for r in after.values())-sum(r[0] for r in before.values()))

    def test_batches_check_cancellation_and_leave_source_read_only(self):
        from app.scope_changes import summarize
        p = str(Path(os.sep) / 'part') + os.sep
        source, output = self.inventories([(p+str(i), 1, 1, i+1, 'f') for i in range(2100)], [])
        source.execute('PRAGMA query_only=ON')
        checks = []
        def checkpoint():
            checks.append(1)
            if len(checks) == 3: raise ValueError('synthetic cancellation')
        with self.assertRaisesRegex(ValueError, 'synthetic cancellation'):
            summarize(source, output, Path(os.sep) / 'part', checkpoint)
        self.assertEqual(source.execute('SELECT count(*) FROM files').fetchone()[0], 2100)

    def test_multiple_batches_complete_without_python_whole_inventory_list(self):
        from app.scope_changes import summarize
        p = str(Path(os.sep) / 'part') + os.sep
        rows = [(p+str(i), 2, 1, i+1, 'f') for i in range(2101)]
        source, output = self.inventories(rows, rows)
        source.execute('PRAGMA query_only=ON')
        checks = []
        result = summarize(source, output, Path(os.sep) / 'part', lambda: checks.append(1))
        self.assertEqual(result['counts']['unchanged'], 2101)
        self.assertEqual(result['before_bytes'], 4202)
        self.assertEqual(result['delta_bytes'], 0)
        self.assertGreaterEqual(len(checks), 6)
        self.assertEqual(output.execute("SELECT count(*) FROM sqlite_temp_master WHERE name='previous_scope'").fetchone()[0], 0)

    def test_sql_interruption_and_normal_sql_error_propagate(self):
        from app.scope_changes import summarize
        source, output = self.inventories([], [])
        output.set_progress_handler(lambda: 1, 1)
        with self.assertRaises(sqlite3.OperationalError):
            summarize(source, output, Path(os.sep) / 'part', lambda: None)
        output.set_progress_handler(None, 0)
        output.execute('DROP TABLE files')
        with self.assertRaises(sqlite3.OperationalError):
            summarize(source, output, Path(os.sep) / 'part', lambda: None)

    def test_real_same_size_timestamp_change_restart_and_no_content_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files, state = root/'files', root/'state'
            selected = files/'part'
            selected.mkdir(parents=True)
            (files/'part-two').mkdir()
            (files/'part-two'/'sibling.bin').write_bytes(b'outside')
            item = selected/'sample.bin'
            item.write_bytes(b'old')
            (state/'scans').mkdir(parents=True)
            base, target = state/'scans'/'base.db', state/'scans'/'new.db'
            scan(files, [], threading.Event(), lambda _: None, inventory_path=base)
            digest = hashlib.sha256(base.read_bytes()).hexdigest()
            old_time = item.stat().st_mtime_ns
            item.write_bytes(b'new')
            os.utime(item, ns=(old_time+2000000000, old_time+2000000000))
            with patch('pathlib.Path.open', side_effect=AssertionError('source content must not be read')):
                # Only staging fsync is allowed to open a file; no content hash.
                with patch('app.refresh._sync_staging_file'):
                    result = refresh(base, selected, target, [], threading.Event(), lambda _: None)
            self.assertEqual(result['refresh']['changes']['counts']['metadata_changed'], 1)
            self.assertEqual(result['refresh']['changes']['before_files'], 1)
            self.assertFalse(result['coverage_complete'])
            self.assertEqual(result['candidates'], [])
            with TestClient(create_app(state, [files])) as client:
                restored = client.get('/api/scans/new').json()['result']
                self.assertEqual(restored['refresh']['changes'], result['refresh']['changes'])
                self.assertEqual(client.get('/api/scans/new/export').json()['refresh']['changes'], result['refresh']['changes'])
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), digest)

    def test_change_summary_failure_does_not_publish_or_modify_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root/'files'/'part'
            selected.mkdir(parents=True)
            (selected/'sample').write_bytes(b'x')
            base, target = root/'base.db', root/'new.db'
            scan(root/'files', [], threading.Event(), lambda _: None, inventory_path=base)
            digest = hashlib.sha256(base.read_bytes()).hexdigest()
            with patch('app.refresh.summarize_changes', side_effect=sqlite3.OperationalError('synthetic')):
                with self.assertRaises(sqlite3.OperationalError):
                    refresh(base, selected, target, [], threading.Event(), lambda _: None)
            self.assertFalse(target.exists())
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), digest)

    def test_preserved_metadata_content_change_is_explicitly_undetectable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = root/'files'/'part'/'sample'
            item.parent.mkdir(parents=True)
            item.write_bytes(b'old')
            base, target = root/'base.db', root/'new.db'
            scan(root/'files', [], threading.Event(), lambda _: None, inventory_path=base)
            info = item.stat()
            item.write_bytes(b'new')
            os.utime(item, ns=(info.st_atime_ns, info.st_mtime_ns))
            result = refresh(base, item.parent, target, [], threading.Event(), lambda _: None)
            self.assertEqual(result['refresh']['changes']['counts']['unchanged'], 1)
            self.assertFalse(result['refresh']['changes']['content_verified'])


if __name__ == '__main__': unittest.main()
