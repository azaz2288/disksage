"""Synthetic inventories only; no user disk scan or cleanup."""
import hashlib
from contextlib import closing
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

import test_refresh
from app.refresh import refresh


class RefreshBudgetTests(unittest.TestCase):
    setUp = test_refresh.RefreshTests.setUp
    tearDown = test_refresh.RefreshTests.tearDown

    def invoke(self, **kwargs):
        return refresh(self.base, self.scope, self.state / 'scans' / 'new.db', [],
                       kwargs.pop('cancel', threading.Event()), lambda _: None, **kwargs)

    def intact(self):
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)
        self.assertFalse((self.state / 'scans' / 'new.db').exists())
        self.assertEqual(list((self.state / 'scans').glob('.refresh-*')), [])

    def test_invalid_budget_rejected_before_filesystem_access(self):
        for value in [True, False, 0, -1, 3601, 1.5, '3', None, float('nan')]:
            with self.subTest(value=value), patch('app.refresh.connect') as source:
                with self.assertRaises(ValueError): self.invoke(timeout_seconds=value)
                source.assert_not_called()
        self.intact()

    def test_default_budget_and_custom_budget_persist(self):
        value = self.invoke()
        self.assertEqual(value['refresh']['timeout_seconds'], 300)
        self.assertGreaterEqual(value['refresh']['elapsed_before_sync_seconds'], 0)
        self.assertLess(value['refresh']['elapsed_before_sync_seconds'], 300)
        custom = refresh(self.base, self.scope, self.state / 'scans' / 'custom.db', [],
                         threading.Event(), lambda _: None, timeout_seconds=1)
        self.assertEqual(custom['refresh']['timeout_seconds'], 1)

    def test_deadline_expired_before_scan_no_publication(self):
        from app.refresh_budget import RefreshTimedOut
        with patch('app.refresh_budget.monotonic', side_effect=[0, 2]):
            with self.assertRaises(RefreshTimedOut): self.invoke(timeout_seconds=1)
        self.intact()

    def test_one_total_budget_includes_scan_and_sync(self):
        from app.refresh_budget import RefreshTimedOut
        clock = [0.0]
        from app.refresh import scan as real_scan, _sync_staging_file as sync
        def scanned(*args, **kwargs):
            value = real_scan(*args, **kwargs)
            clock[0] = .75
            return value
        def synced(path):
            sync(path)
            clock[0] = 1.01
        with patch('app.refresh_budget.monotonic', side_effect=lambda: clock[0]), \
             patch('app.refresh.scan', side_effect=scanned), \
             patch('app.refresh._sync_staging_file', side_effect=synced):
            with self.assertRaises(RefreshTimedOut): self.invoke(timeout_seconds=1)
        self.intact()

    def test_cancel_is_classified_and_latched(self):
        from app.refresh_budget import RefreshBudget, RefreshCancelled
        event = threading.Event()
        budget = RefreshBudget(event, 300)
        event.set()
        self.assertTrue(budget.is_set())
        event.clear()
        with self.assertRaises(RefreshCancelled): budget.check()
        event.set()
        with self.assertRaises(RefreshCancelled): self.invoke(cancel=event)
        self.intact()

    def test_sqlite_aggregation_interrupts_with_cancel_or_timeout(self):
        from app.refresh_budget import RefreshCancelled, RefreshTimedOut
        # Populate only a synthetic baseline inventory, without enumerating a disk.
        with closing(sqlite3.connect(self.base)) as db:
            db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?)',
                           [(str(self.sibling / f'{i}.bin'), str(self.sibling), f'{i}.bin',
                             1, 1, '.bin', 0, 'synthetic', '0', str(i + 100)) for i in range(2500)])
            db.commit()
        self.digest = hashlib.sha256(self.base.read_bytes()).hexdigest()
        real_connect = sqlite3.connect
        for kind in ['cancel', 'timeout']:
            event, clock, traces = threading.Event(), [0.0], []
            def hooked(*args, **kwargs):
                db = real_connect(*args, **kwargs)
                def trace(sql):
                    if 'GROUP BY parent' in sql:
                        traces.append(sql)
                        if kind == 'cancel': event.set()
                        else: clock[0] = 2
                db.set_trace_callback(trace)
                return db
            with self.subTest(kind=kind), patch('sqlite3.connect', side_effect=hooked), \
                 patch('app.refresh_budget.monotonic', side_effect=lambda: clock[0]):
                with self.assertRaises(RefreshCancelled if kind == 'cancel' else RefreshTimedOut):
                    self.invoke(cancel=event, timeout_seconds=1)
                self.assertTrue(traces)
                self.intact()

    def test_api_budget_strict_validation(self):
        for value in [True, False, 0, 3601, 1.1, '5', None]:
            response = self.client.post('/api/scans/base/refresh',
                                        json={'path': str(self.scope), 'timeout_seconds': value})
            self.assertEqual(response.status_code, 422, response.text)
        self.intact()

    def test_api_terminal_states_and_sanitized_errors(self):
        from app.refresh_budget import RefreshCancelled, RefreshTimedOut
        for error, state in [(RefreshCancelled('private-path'), 'cancelled'),
                             (RefreshTimedOut('private-path'), 'timed_out')]:
            with patch('app.main.refresh_scope', side_effect=error) as called:
                response = self.client.post('/api/scans/base/refresh',
                                            json={'path': str(self.scope), 'timeout_seconds': 9})
                self.assertEqual(response.status_code, 200)
                for _ in range(500):
                    job = self.client.get('/api/scans/' + response.json()['id']).json()
                    if job['state'] != 'running': break
                    time.sleep(.01)
                self.assertEqual(job['state'], state, job)
                self.assertNotIn('private-path', str(job))
                self.assertNotIn('result', job)
                self.assertEqual(called.call_args.kwargs['timeout_seconds'], 9)
        self.intact()

    def test_scan_inventory_sqlite_progress_and_nonbudget_errors(self):
        from app.refresh_budget import RefreshCancelled
        event = threading.Event()
        from app.snapshot import Inventory
        original = Inventory.finish
        def finish(inventory, result, sizes):
            event.set()
            # A VM-heavy statement proves the scan connection is interruptible.
            inventory.db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n').fetchone()
            return original(inventory, result, sizes)
        with patch.object(Inventory, 'finish', finish):
            with self.assertRaises(RefreshCancelled): self.invoke(cancel=event)
        self.intact()
        with patch('app.refresh.scan', side_effect=sqlite3.OperationalError('real failure')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'real failure'): self.invoke()
        self.intact()

    def test_timeout_preserves_late_competing_output(self):
        from app.refresh_budget import RefreshTimedOut
        clock = [0]
        target = self.state / 'scans' / 'new.db'
        def sync(_):
            target.write_bytes(b'competitor')
            clock[0] = 2
        with patch('app.refresh_budget.monotonic', side_effect=lambda: clock[0]), \
             patch('app.refresh._sync_staging_file', side_effect=sync):
            with self.assertRaises(RefreshTimedOut): self.invoke(timeout_seconds=1)
        self.assertEqual(target.read_bytes(), b'competitor')
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)
        self.assertEqual(list(target.parent.glob('.refresh-*')), [])

    def test_api_actual_cancel_event_is_observed_and_no_output_published(self):
        entered = threading.Event()
        from app.refresh import scan as original
        def waiting(*args, **kwargs):
            entered.set()
            deadline = time.monotonic() + 5
            while not args[2].is_set() and time.monotonic() < deadline: time.sleep(.005)
            return original(*args, **kwargs)
        with patch('app.refresh.scan', side_effect=waiting):
            response = self.client.post('/api/scans/base/refresh', json={'path': str(self.scope)})
            self.assertEqual(response.status_code, 200)
            ident = response.json()['id']
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.client.post(f'/api/scans/{ident}/cancel').status_code, 200)
            for _ in range(500):
                job = self.client.get(f'/api/scans/{ident}').json()
                if job['state'] != 'running': break
                time.sleep(.01)
            self.assertEqual(job['state'], 'cancelled', job)
            self.assertFalse((self.state / 'scans' / (ident + '.db')).exists())
        self.intact()
