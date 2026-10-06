"""Synthetic historic snapshots only; never access real inventory or source disks."""
from pathlib import Path
import csv
import hashlib
import io
import os
import tempfile
import threading
import unittest
import random
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

# The module-level app must not load the user's default data/scans.
_bootstrap = tempfile.TemporaryDirectory(prefix="disksage-comparison-bootstrap-")
os.environ["APP_DATA_DIR"] = _bootstrap.name
from fastapi.testclient import TestClient
from app.main import create_app
from app.engine import scan


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = self.root / "files"
        self.files.mkdir()
        self.state = self.root / "state"
        (self.state / "scans").mkdir(parents=True)
        (self.files / "folder").mkdir()
        (self.files / "folder2").mkdir()
        (self.files / "folder" / "resize.txt").write_bytes(b"old")
        (self.files / "removed.txt").write_bytes(b"12345")
        (self.files / "unchanged.txt").write_bytes(b"same")
        self.capture("before")
        (self.files / "folder" / "resize.txt").write_bytes(b"new-long")
        (self.files / "removed.txt").unlink()
        (self.files / "folder2" / "added.txt").write_bytes(b"xx")
        self.capture("after")
        self.client = TestClient(create_app(self.state, [self.files]))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def capture(self, ident, **kwargs):
        return scan(self.files, [], threading.Event(), lambda _: None,
                    inventory_path=self.state / "scans" / f"{ident}.db", **kwargs)

    def get(self, **params):
        return self.client.get("/api/comparisons", params={"baseline": "before", "current": "after", **params})

    def test_observed_add_remove_resize_counts_and_signed_delta(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["counts"], {"added": 1, "removed": 1, "resized": 1})
        self.assertEqual(result["delta_bytes"], 2)
        self.assertTrue(result["coverage_complete"])
        items = {Path(row["path"]).name: row for row in result["items"]}
        self.assertEqual(items["resize.txt"]["delta_bytes"], 5)
        self.assertIsNone(items["removed.txt"]["current_size"])
        self.assertEqual(items["added.txt"]["baseline_size"], None)
        self.assertNotIn("unchanged.txt", items)

    def test_stable_pages_filters_literal_search_and_directory_boundary(self):
        first, second = self.get(limit=1).json(), self.get(offset=1, limit=1).json()
        self.assertNotEqual(first["items"][0]["path"], second["items"][0]["path"])
        self.assertTrue(first["has_more"])
        self.assertEqual(self.get(offset=999).json()["items"], [])
        page = self.get(path=str(self.files / "folder")).json()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["kind"], "resized")
        self.assertEqual(self.get(kind="removed").json()["total"], 1)
        self.assertEqual(self.get(q="%_'").json()["total"], 0)
        self.assertEqual(self.get(q="RESIZE").json()["total"], 1)

    def test_self_reverse_empty_and_no_content_diff_claim(self):
        self.assertEqual(self.get(current="before").json()["total"], 0)
        reversed_result = self.get(baseline="after", current="before").json()
        self.assertEqual(reversed_result["delta_bytes"], -2)
        (self.files / "unchanged.txt").write_bytes(b"else")
        self.capture("same-length")
        other = TestClient(create_app(self.state, [self.files]))
        page = other.get("/api/comparisons", params={"baseline": "after", "current": "same-length"}).json()
        self.assertEqual(page["total"], 0)
        other.close()

    def test_invalid_scope_ids_root_and_query_controls_rejected(self):
        for params, code in (({"baseline": "unknown"}, 404), ({"path": str(self.root)}, 400),
                             ({"path": str(self.files / "not-scanned")}, 404),
                             ({"offset": -1}, 422), ({"limit": 501}, 422), ({"kind": "SQL"}, 422)):
            with self.subTest(params=params):
                self.assertEqual(self.get(**params).status_code, code)
        outside = self.root / "different-root"
        outside.mkdir()
        scan(outside, [], threading.Event(), lambda _: None,
             inventory_path=self.state / "scans" / "outside.db")
        other = TestClient(create_app(self.state, []))
        response = other.get("/api/comparisons", params={"baseline": "before", "current": "outside"})
        self.assertEqual(response.status_code, 400)
        other.close()

    def test_partial_comparison_has_explicit_warning_not_deletion_claim(self):
        self.capture("partial", max_files=1)
        other = TestClient(create_app(self.state, []))
        page = other.get("/api/comparisons", params={"baseline": "before", "current": "partial"}).json()
        self.assertFalse(page["coverage_complete"])
        self.assertTrue(page["warnings"])
        self.assertFalse(page["current"]["complete"])
        other.close()

    def test_export_all_changes_filtered_not_just_first_page(self):
        response = self.client.get("/api/comparisons.csv", params={"baseline": "before", "current": "after"})
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(int(r["delta_bytes"]) for r in rows), 2)
        self.assertIn("baseline_coverage_complete", rows[0])
        filtered = self.client.get("/api/comparisons.csv", params={"baseline": "before", "current": "after", "kind": "resized"})
        self.assertEqual(len(list(csv.reader(io.StringIO(filtered.text)))), 2)

    def test_query_and_export_do_not_read_sources_or_mutate_snapshots(self):
        paths = list((self.state / "scans").glob("*.db"))
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        with patch("app.engine.scan", side_effect=AssertionError("No new scans")), \
             patch.object(Path, "lstat", side_effect=AssertionError("No source metadata reads")):
            self.assertEqual(self.get().status_code, 200)
            self.assertEqual(self.client.get("/api/comparisons.csv", params={"baseline": "before", "current": "after"}).status_code, 200)
        self.assertEqual(hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])

    def test_removed_directory_and_zero_length_files(self):
        folder = self.files / 'deleted-folder'
        folder.mkdir()
        (folder / 'zero.txt').write_bytes(b'')
        self.capture('folder-before')
        (folder / 'zero.txt').unlink()
        folder.rmdir()
        self.capture('folder-after')
        other = TestClient(create_app(self.state, []))
        result = other.get('/api/comparisons', params={'baseline':'folder-before','current':'folder-after','path':str(folder)}).json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['baseline_size'], 0)
        self.assertEqual(result['delta_bytes'], 0)
        other.close()

    def test_invalid_csv_scope_fails_before_streaming_headers(self):
        for params, code in (({'baseline':'before','current':'after','path':str(self.root)},400),
                             ({'baseline':'missing','current':'after'},404)):
            response=self.client.get('/api/comparisons.csv',params=params)
            self.assertEqual(response.status_code,code)
            self.assertIn('application/json',response.headers['content-type'])

    def test_export_can_resume_on_another_serial_worker_thread(self):
        from app.comparison import export
        stream=export(self.state/'scans'/'before.db',self.state/'scans'/'after.db')
        with ThreadPoolExecutor(max_workers=1) as first, ThreadPoolExecutor(max_workers=1) as second:
            header=first.submit(next,stream).result()
            body=second.submit(lambda:list(stream)).result()
        self.assertTrue(header.startswith('\ufeffpath,'))
        self.assertEqual(len(body),3)

    def test_seeded_snapshot_rows_match_independent_dictionary_oracle(self):
        from app.snapshot import Inventory
        from app.comparison import compare
        rng=random.Random(20261007)
        for index in range(30):
            old={str(self.files/f'oracle-{n}.bin'):rng.randrange(20) for n in range(40) if rng.random()<.7}
            new={str(self.files/f'oracle-{n}.bin'):rng.randrange(20) for n in range(40) if rng.random()<.7}
            paths=[]
            for label,values in (('old',old),('new',new)):
                path=self.root/f'oracle-{index}-{label}.db';inventory=Inventory(path)
                for ident,(name,size) in enumerate(values.items()):
                    inventory.file(Path(name),types.SimpleNamespace(st_size=size,st_mtime=0,st_dev=1,st_ino=ident),0,'synthetic')
                inventory.finish({'root':str(self.files),'details_complete':True,'coverage_complete':True,'complete':True},{self.files:sum(values.values())})
                paths.append(path)
            expected={p:('added' if p not in old else 'removed' if p not in new else 'resized',new.get(p,0)-old.get(p,0))
                      for p in old.keys()|new.keys() if p not in old or p not in new or old[p]!=new[p]}
            actual=compare(*paths,limit=500)
            with self.subTest(index=index):
                self.assertEqual({r['path']:(r['kind'],r['delta_bytes']) for r in actual['items']},expected)
                self.assertEqual(actual['delta_bytes'],sum(new.values())-sum(old.values()))
                self.assertEqual([r['path'] for r in actual['items']],sorted(expected))


if __name__ == "__main__":
    unittest.main()
