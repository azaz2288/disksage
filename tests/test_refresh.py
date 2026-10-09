"""Only synthetic temporary source trees and inventories."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

_bootstrap = tempfile.TemporaryDirectory(prefix='disksage-refresh-bootstrap-')
os.environ['APP_DATA_DIR'] = _bootstrap.name
from fastapi.testclient import TestClient
from app.engine import scan
from app.main import create_app
from app.snapshot import connect, summary


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = self.root / 'files'
        self.scope = self.files / 'part'
        self.sibling = self.files / 'part-two'
        self.scope.mkdir(parents=True)
        self.sibling.mkdir()
        (self.scope / 'old.bin').write_bytes(b'old')
        (self.sibling / 'outside.bin').write_bytes(b'keep')
        self.state = self.root / 'state'
        (self.state / 'scans').mkdir(parents=True)
        self.base = self.state / 'scans' / 'base.db'
        scan(self.files, [], threading.Event(), lambda _: None, inventory_path=self.base)
        self.digest = hashlib.sha256(self.base.read_bytes()).hexdigest()
        self.client = TestClient(create_app(self.state, [self.files]))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def refreshed(self):
        response = self.client.post('/api/scans/base/refresh', json={'path': str(self.scope)})
        self.assertEqual(response.status_code, 200, response.text)
        ident = response.json()['id']
        for _ in range(500):
            job = self.client.get('/api/scans/' + ident).json()
            if job['state'] != 'running':
                return ident, job
            time.sleep(.01)
        self.fail('refresh timed out')

    def test_selected_scope_replaced_sibling_retained_and_baseline_unchanged(self):
        (self.scope / 'old.bin').unlink()
        (self.scope / 'new.bin').write_bytes(b'123456')
        (self.sibling / 'outside.bin').write_bytes(b'changed-outside')
        from app.refresh import refresh
        real_scandir = os.scandir
        observed = []
        def enumerated(path):
            if isinstance(path, (str, os.PathLike)) and Path(path).is_relative_to(self.files):
                observed.append(Path(path))
            return real_scandir(path)
        with patch('app.engine.os.scandir', side_effect=enumerated):
            ident, job = self.refreshed()
        self.assertEqual(job['state'], 'completed', job)
        self.assertEqual(observed, [self.scope])
        result = job['result']
        self.assertEqual(result['bytes'], 10)
        self.assertEqual(result['files'], 2)
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['refresh']['reused_files'], 1)
        self.assertEqual(result['refresh']['scanned_files'], 1)
        self.assertEqual(result['refresh']['baseline_id'], 'base')
        self.assertEqual(result['candidates'], [])
        page = self.client.get(f'/api/scans/{ident}/browse?recursive=true').json()
        self.assertEqual({Path(i['path']).name: i['size'] for i in page['items']}, {'outside.bin': 4, 'new.bin': 6})
        restarted = TestClient(create_app(self.state, [self.files]))
        try:
            self.assertEqual(restarted.get(f'/api/scans/{ident}').json()['result']['refresh'], result['refresh'])
        finally:
            restarted.close()
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_outside_root_root_and_unregistered_scope_refused(self):
        for path in (self.files, self.root, self.files / 'unknown', self.scope / '..'):
            with self.subTest(path=path):
                response = self.client.post('/api/scans/base/refresh', json={'path': str(path)})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.post('/api/scans/missing/refresh', json={'path': str(self.scope)}).status_code, 404)

    def test_output_failure_or_cancel_does_not_publish_or_modify_baseline(self):
        from app.refresh import refresh
        for mode in ('cancel', 'link'):
            target = self.state / 'scans' / (mode + '.db')
            event = threading.Event()
            if mode == 'cancel': event.set()
            context = patch('app.refresh.os.link', side_effect=OSError('synthetic publication failure'))
            with context if mode == 'link' else patch('app.refresh.os.link', wraps=os.link):
                with self.assertRaises((ValueError, OSError)):
                    refresh(self.base, self.scope, target, [], event, lambda _: None)
            self.assertFalse(target.exists())
            self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)
        self.assertEqual(list(self.state.joinpath('scans').glob('.refresh-*')), [])

    def test_existing_destination_is_not_overwritten(self):
        from app.refresh import refresh
        target = self.state / 'scans' / 'existing.db'
        target.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            refresh(self.base, self.scope, target, [], threading.Event(), lambda _: None)
        self.assertEqual(target.read_bytes(), b'preserve')

    def test_partial_baseline_and_mixed_freshness_baseline_refused(self):
        from app.refresh import refresh
        incomplete = self.state / 'scans' / 'partial.db'
        scan(self.files, [], threading.Event(), lambda _: None, max_files=1, inventory_path=incomplete)
        with self.assertRaises(ValueError):
            refresh(incomplete, self.scope, self.state / 'bad.db', [], threading.Event(), lambda _: None)
        ident, job = self.refreshed()
        self.assertEqual(job['state'], 'completed', job)
        response = self.client.post(f'/api/scans/{ident}/refresh', json={'path': str(self.scope)})
        self.assertEqual(response.status_code, 400)

    def test_hardlink_identity_global_totals_and_empty_directories(self):
        from app.refresh import refresh
        (self.scope / 'old.bin').unlink()
        os.link(self.sibling / 'outside.bin', self.scope / 'linked.bin')
        (self.scope / 'empty').mkdir()
        target = self.state / 'scans' / 'linked.db'
        result = refresh(self.base, self.scope, target, [], threading.Event(), lambda _: None)
        self.assertEqual(result['bytes'], 8)
        self.assertEqual(result['unique_bytes'], 4)
        with connect(target) as db:
            self.assertEqual(db.execute('SELECT size FROM directories WHERE path=?', (str(self.files),)).fetchone()[0], 8)
            self.assertEqual(db.execute('SELECT size FROM directories WHERE path=?', (str(self.scope / 'empty'),)).fetchone()[0], 0)
        self.assertEqual(summary(target)['refresh']['reused_files'], 1)

    def test_directory_read_failure_and_sync_failure_leave_no_output(self):
        from app.refresh import refresh
        real_scandir = os.scandir
        def refused(path):
            if isinstance(path, (str, os.PathLike)) and Path(path) == self.scope:
                raise PermissionError('synthetic selected-tree denial')
            return real_scandir(path)
        for mode in ('read', 'sync'):
            target = self.state / 'scans' / (mode + '.db')
            fault = patch('app.engine.os.scandir', side_effect=refused) if mode == 'read' else patch('app.refresh._sync_staging_file', side_effect=OSError('synthetic'))
            with fault:
                with self.assertRaises((ValueError, OSError)):
                    refresh(self.base, self.scope, target, [], threading.Event(), lambda _: None)
            self.assertFalse(target.exists())
            self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_late_competing_output_preserved(self):
        from app.refresh import refresh
        target = self.state / 'scans' / 'race.db'
        real_link = os.link
        def competing(source, destination):
            Path(destination).write_bytes(b'competitor')
            return real_link(source, destination)
        with patch('app.refresh.os.link', side_effect=competing):
            with self.assertRaises(FileExistsError):
                refresh(self.base, self.scope, target, [], threading.Event(), lambda _: None)
        self.assertEqual(target.read_bytes(), b'competitor')
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_cancel_after_observation_and_before_publication_preserves_baseline(self):
        from app.refresh import refresh
        event = threading.Event()
        target = self.state / 'scans' / 'late-cancel.db'
        def update(_): event.set()
        with self.assertRaises(ValueError):
            refresh(self.base, self.scope, target, [], event, update)
        self.assertFalse(target.exists())
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_directory_link_refused(self):
        from app.refresh import refresh
        (self.scope / 'old.bin').unlink()
        self.scope.rmdir()
        try:
            self.scope.symlink_to(self.sibling, target_is_directory=True)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable')
        with self.assertRaises(ValueError):
            refresh(self.base, self.scope, self.state / 'unsafe.db', [], threading.Event(), lambda _: None)

    def test_cancel_during_sync_does_not_publish(self):
        from app.refresh import refresh, _sync_staging_file
        event = threading.Event()
        target = self.state / 'scans' / 'sync-cancel.db'
        def cancel_during_sync(path):
            event.set()
            _sync_staging_file(path)
        with patch('app.refresh._sync_staging_file', side_effect=cancel_during_sync):
            with self.assertRaises(ValueError):
                refresh(self.base, self.scope, target, [], event, lambda _: None)
        self.assertFalse(target.exists())
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_api_serializes_jobs_and_sanitizes_failure(self):
        entered, release = threading.Event(), threading.Event()
        def failing(*args, **kwargs):
            entered.set()
            release.wait(3)
            raise OSError('synthetic-sensitive-internal-path')
        with patch('app.main.refresh_scope', side_effect=failing):
            response = self.client.post('/api/scans/base/refresh', json={'path': str(self.scope)})
            self.assertEqual(response.status_code, 200)
            ident = response.json()['id']
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.post('/api/scans/base/refresh', json={'path': str(self.scope)}).status_code, 409)
                self.assertEqual(self.client.post('/api/scans/base/refresh', json={'path': str(self.scope)}, headers={'Origin': 'https://foreign.example'}).status_code, 403)
            finally:
                release.set()
            for _ in range(300):
                job = self.client.get('/api/scans/' + ident).json()
                if job['state'] != 'running': break
                time.sleep(.01)
            self.assertEqual(job['state'], 'failed')
            self.assertNotIn('synthetic-sensitive', job['error'])
        self.assertFalse((self.state / 'scans' / (ident + '.db')).exists())
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_seeded_dictionary_reference_and_baseline_snapshot(self):
        from app.refresh import refresh
        import random
        rng = random.Random(912)
        for case in range(12):
            folder = self.scope / f'dir-{case}'
            folder.mkdir()
            expected = {str(self.sibling / 'outside.bin'): 4, str(self.scope / 'old.bin'): 3}
            for i in range(8):
                item = folder / f'file-{i}.dat'
                size = rng.randrange(0, 64)
                item.write_bytes(b'x' * size)
            target = self.state / 'scans' / f'oracle-{case}.db'
            # Selected-tree oracle reads only the synthetic scope, independently of merger.
            for item in self.scope.rglob('*'):
                if item.is_file(): expected[str(item)] = item.stat().st_size
            refresh(self.base, self.scope, target, [], threading.Event(), lambda _: None)
            with connect(target) as db:
                actual = {r['path']: r['size'] for r in db.execute('SELECT path,size FROM files')}
                self.assertEqual(actual, expected)
                self.assertEqual(db.execute('SELECT size FROM directories WHERE path=?', (str(self.files),)).fetchone()[0], sum(expected.values()))
            self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)


if __name__ == '__main__': unittest.main()
