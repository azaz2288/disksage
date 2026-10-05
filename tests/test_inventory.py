from pathlib import Path
import csv,io,os,tempfile,time,threading,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import create_app
from app.engine import scan,directory_totals
from app.snapshot import connect,browse

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.files=self.root/'files';self.files.mkdir();self.state=self.root/'state';self.client=TestClient(create_app(self.state,[self.files]))
    def tearDown(self):self.tmp.cleanup()
    def scan(self):
        ident=self.client.post('/api/scans',json={'path':str(self.files)}).json()['id']
        for _ in range(500):
            row=self.client.get('/api/scans/'+ident).json()
            if row['state']!='running':return ident,row['result']
            time.sleep(.01)
        self.fail('scan timed out')
    def test_all_files_are_paginated_searchable_exported_and_survive_restart(self):
        for i in range(650):(self.files/f'file-{i:04}.txt').write_bytes(b'x'*(i+1))
        nested=self.files/'nested';nested.mkdir();(nested/'special.txt').write_text('nested')
        ident,result=self.scan();self.assertEqual(result['inventory_files'],651)
        all_paths=[]
        for offset in range(0,700,100):
            page=self.client.get(f'/api/scans/{ident}/browse?recursive=true&offset={offset}').json()
            self.assertEqual(page['total'],651);all_paths.extend(r['path'] for r in page['items'])
        self.assertEqual(len(set(all_paths)),651)
        page=self.client.get(f'/api/scans/{ident}/browse?q=special&recursive=true').json()
        self.assertEqual(page['total'],1);self.assertIsNotNone(page['items'][0]['allocated'])
        rows=list(csv.reader(io.StringIO(self.client.get(f'/api/scans/{ident}/files.csv').text)))
        self.assertEqual(len(rows),652)
        other=TestClient(create_app(self.state,[self.files]));self.assertEqual(other.get(f'/api/scans/{ident}/browse?recursive=true').json()['total'],651)
        self.assertEqual(other.get('/api/scans').json()[0]['id'],ident)
    def test_inventory_keeps_files_beyond_old_50000_cutoff(self):
        # Synthetic directory enumeration tests the real scan/storage pipeline without 50k OS writes.
        real_stat=Path.lstat;root_info=self.files.lstat()
        class Entry:
            def __init__(self,i):self.name=f'file-{i}.txt';self.path=str(self_path/self.name);self.i=i
            def stat(self,follow_symlinks=False):return os.stat_result((0o100644,self.i+100,1,1,0,0,4,0,0,0))
        self_path=self.files
        class Entries:
            def __enter__(self):return iter(Entry(i) for i in range(50005))
            def __exit__(self,*args):pass
        def fake_lstat(path):
            if path.parent==self.files and path.name.startswith('file-'):
                number=int(path.stem[5:]);return os.stat_result((0o100644,number+100,1,1,0,0,4,0,0,0))
            return real_stat(path)
        with patch('app.engine.os.scandir',return_value=Entries()),patch.object(Path,'lstat',fake_lstat),patch('app.engine.physical_size',return_value=4096),patch('app.engine.fingerprint',return_value='synthetic'):
            result=scan(self.files,[],threading.Event(),lambda _:None,inventory_path=self.root/'inventory.db')
        self.assertTrue(result['complete']);self.assertTrue(result['details_complete']);self.assertEqual(result['files'],50005)
        page=browse(result['_inventory'],str(self.files),'file-50004')
        self.assertEqual(page['total'],1)
    def test_ai_paths_are_opt_in_and_actual_inventory_backed(self):
        (self.files/'application-cache.bin').write_bytes(b'x'*100);ident,_=self.scan()
        summary=self.client.get(f'/api/scans/{ident}/ai-preview').json()
        self.assertFalse(summary['paths_included']);self.assertNotIn('application-cache',str(summary))
        extended=self.client.get(f'/api/scans/{ident}/ai-preview?include_paths=true').json()
        self.assertEqual(extended['largest_files'][0]['path'],str(self.files/'application-cache.bin'))
        with patch('app.main.generate',return_value='分析建议') as provider:
            response=self.client.post(f'/api/scans/{ident}/advice',json={'include_paths':True,'question':'检查缓存'})
            self.assertEqual(response.status_code,200);self.assertIn('application-cache',provider.call_args.args[1])
    def test_volume_root_is_not_its_own_child(self):
        root=Path(self.root.anchor);child=root/'nested';grandchild=child/'deeper'
        totals=directory_totals({root:5,child:10,grandchild:20})
        self.assertEqual(totals[root],35);self.assertEqual(totals[child],30)
    def test_previous_volume_root_snapshot_is_repaired_on_restart(self):
        import json,sqlite3
        from app.snapshot import Inventory,summary
        path=self.root/'legacy.db';volume=Path(self.root.anchor);inventory=Inventory(path)
        inventory.finish({'root':str(volume),'bytes':10,'directories':[{'path':str(volume),'size':20}]},{volume:20})
        restored=summary(path);self.assertEqual(restored['directories'][0]['size'],10)
        with connect(path) as db:self.assertEqual(db.execute('SELECT size FROM directories').fetchone()[0],10)

if __name__=='__main__':unittest.main()
