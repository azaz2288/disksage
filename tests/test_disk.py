from pathlib import Path
import os
import tempfile
import time
import unittest
from fastapi.testclient import TestClient
from app.main import create_app


class DiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.temp = self.base / "temp"
        self.temp.mkdir()
        self.client = TestClient(create_app(self.base / "state", [self.temp]))
    def tearDown(self):
        self.tmp.cleanup()
    def old_file(self, name="old.tmp", folder=None):
        path = (folder or self.temp) / name
        path.write_bytes(b"test bytes")
        os.utime(path, (time.time()-9*86400,)*2)
        return path
    def scan(self, path=None):
        ident = self.client.post('/api/scans', json={"path":str(path or self.temp)}).json()['id']
        for _ in range(200):
            job = self.client.get('/api/scans/'+ident).json()
            if job['state'] != 'running':
                return ident, job['result']
            time.sleep(.01)
        self.fail("Scan did not finish")
    def test_scan_is_read_only_and_counts_bytes(self):
        path=self.old_file()
        _, result=self.scan()
        self.assertEqual(result['bytes'],10)
        self.assertTrue(path.exists())
        self.assertEqual(len(result['candidates']),1)
    def test_fresh_and_documents_not_candidates(self):
        self.old_file('document.txt')
        (self.temp/'fresh.tmp').write_text('fresh')
        self.assertEqual(self.scan()[1]['candidates'],[])
    def test_outside_temp_is_not_candidate(self):
        self.old_file(folder=self.base)
        self.assertEqual(self.scan(self.base)[1]['candidates'],[])
    def test_quarantine_and_restore_survive_restart(self):
        path=self.old_file()
        ident,result=self.scan()
        candidate=result['candidates'][0]['id']
        response=self.client.post('/api/cleanup',json={"scan_id":ident,"ids":[candidate],"confirm":True})
        self.assertEqual(response.status_code,200)
        self.assertFalse(path.exists())
        other=TestClient(create_app(self.base/'state',[self.temp]))
        self.assertEqual(other.post('/api/history/'+candidate+'/restore').status_code,200)
        self.assertEqual(path.read_bytes(),b'test bytes')
    def test_changed_file_is_rejected(self):
        path=self.old_file()
        ident,result=self.scan()
        path.write_text('changed contents')
        response=self.client.post('/api/cleanup',json={"scan_id":ident,"ids":[result['candidates'][0]['id']],"confirm":True})
        self.assertEqual(response.status_code,409)
        self.assertTrue(path.exists())
    def test_arbitrary_path_and_missing_confirmation_rejected(self):
        self.old_file()
        ident,_=self.scan()
        self.assertEqual(self.client.post('/api/cleanup',json={"scan_id":ident,"ids":["C:/Windows"],"confirm":True}).status_code,400)
        self.assertEqual(self.client.post('/api/cleanup',json={"scan_id":ident,"ids":["x"]}).status_code,400)
    def test_restore_never_overwrites(self):
        path=self.old_file()
        ident,result=self.scan()
        candidate=result['candidates'][0]['id']
        self.client.post('/api/cleanup',json={"scan_id":ident,"ids":[candidate],"confirm":True})
        path.write_text('new file')
        self.assertEqual(self.client.post('/api/history/'+candidate+'/restore').status_code,409)
        self.assertEqual(path.read_text(),'new file')
    def test_cross_site_and_host_blocked(self):
        self.assertEqual(self.client.post('/api/scans',headers={"origin":"https://evil.example"},json={"path":str(self.temp)}).status_code,403)
        self.assertEqual(self.client.get('/api/config',headers={"host":"evil.example"}).status_code,403)
    def test_ai_summary_has_no_paths(self):
        self.old_file('secret-name.tmp')
        ident,_=self.scan()
        response=self.client.get('/api/scans/'+ident+'/ai-preview')
        self.assertNotIn('secret-name',response.text)
        self.assertNotIn(str(self.temp),response.text)
    def test_symlink_is_skipped(self):
        target=self.old_file(folder=self.base)
        try:
            (self.temp/'link.tmp').symlink_to(target)
        except OSError:
            self.skipTest('Symlinks require privileges on Windows')
        _,result=self.scan()
        self.assertEqual(result['bytes'],0)
        self.assertEqual(result['skipped'],1)


if __name__=='__main__':
    unittest.main()
