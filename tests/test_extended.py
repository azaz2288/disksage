from pathlib import Path
import tempfile
import threading
import time
import unittest
import os
from unittest.mock import patch
from app.common import database
from fastapi.testclient import TestClient
from app.engine import scan,find_duplicates
from app.main import create_app

class ExtendedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.cancel=threading.Event()
    def tearDown(self):self.tmp.cleanup()
    def test_cancel_and_limits_have_partial_accurate_total(self):
        for i in range(20):(self.root/f'{i}.txt').write_bytes(b'x'*10)
        result=scan(self.root,[],self.cancel,lambda _:None,max_files=5)
        self.assertFalse(result['complete']);self.assertEqual(result['bytes'],50)
        self.assertEqual(result['_directories'][str(self.root)],50)
        self.cancel.set();self.assertFalse(scan(self.root,[],self.cancel,lambda _:None)['complete'])
    def test_extension_and_duplicate_content(self):
        (self.root/'folder').mkdir();(self.root/'a.txt').write_bytes(b'same')
        (self.root/'folder/b.txt').write_bytes(b'same');(self.root/'c.bin').write_bytes(b'else')
        result=scan(self.root,[],self.cancel,lambda _:None)
        self.assertEqual(result['extensions'][0]['bytes'],8)
        duplicates=find_duplicates(result,self.cancel,lambda _:None)
        self.assertEqual(len(duplicates['groups']),1);self.assertEqual(duplicates['groups'][0]['potential_bytes'],4)
    def test_changed_duplicate_is_not_trusted(self):
        a=self.root/'a.txt';b=self.root/'b.txt';a.write_bytes(b'same');b.write_bytes(b'same')
        result=scan(self.root,[],self.cancel,lambda _:None);b.write_bytes(b'new content')
        output=find_duplicates(result,self.cancel,lambda _:None)
        self.assertEqual(output['groups'],[]);self.assertEqual(output['errors'],1)
    def test_purge_requires_exact_confirmation_and_is_recorded(self):
        path=self.root/'old.tmp';path.write_bytes(b'old');os_time=time.time()-9*86400
        import os;os.utime(path,(os_time,os_time))
        client=TestClient(create_app(self.root/'state',[self.root]))
        ident=client.post('/api/scans',json={'path':str(self.root)}).json()['id']
        for _ in range(100):
            job=client.get('/api/scans/'+ident).json()
            if job['state']!='running':break
            time.sleep(.01)
        candidate=job['result']['candidates'][0]['id']
        response=client.post('/api/cleanup',json={'scan_id':ident,'ids':[candidate],'confirm':True})
        self.assertEqual(response.status_code,200)
        self.assertEqual(client.post('/api/history/'+candidate+'/purge',json={'confirmation':'yes'}).status_code,400)
        self.assertEqual(client.post('/api/history/'+candidate+'/purge',json={'confirmation':'永久删除'}).status_code,200)
        self.assertEqual(client.get('/api/history').json()[0]['state'],'purged')
        self.assertEqual(client.post('/api/history/'+candidate+'/restore').status_code,409)
    def test_hardlinks_have_unique_allocation_and_no_duplicate_copies(self):
        a=self.root/'first.bin';a.write_bytes(b'x'*8192)
        try:os.link(a,self.root/'second.bin')
        except OSError:self.skipTest('Filesystem does not support hard links')
        result=scan(self.root,[],self.cancel,lambda _:None)
        self.assertEqual(result['bytes'],16384);self.assertEqual(result['unique_bytes'],8192)
        self.assertGreaterEqual(result['allocated_bytes'],0)
        self.assertEqual(find_duplicates(result,self.cancel,lambda _:None)['groups'],[])
    def test_permission_error_marks_partial_coverage(self):
        (self.root/'blocked').mkdir();original=os.scandir
        def guarded(path):
            if Path(path).name=='blocked':raise PermissionError('synthetic inaccessible directory')
            return original(path)
        with patch('app.engine.os.scandir',side_effect=guarded):result=scan(self.root,[],self.cancel,lambda _:None)
        self.assertEqual(result['errors'],1)
    def test_journal_and_batch_restore_recovery_without_overwriting(self):
        data=self.root/'state';client=TestClient(create_app(data,[self.root]))
        path=self.root/'old.tmp';path.write_bytes(b'old');os.utime(path,(time.time()-9*86400,)*2)
        ident=client.post('/api/scans',json={'path':str(self.root)}).json()['id']
        for _ in range(200):
            job=client.get('/api/scans/'+ident).json()
            if job['state']!='running':break
            time.sleep(.01)
        key=job['result']['candidates'][0]['id'];batch=client.post('/api/cleanup',json={'scan_id':ident,'ids':[key],'confirm':True}).json()['batch']
        with database(data) as db:
            db.execute("UPDATE moves SET state='pending' WHERE id=?",(key,))
        self.assertEqual(client.get('/api/history').json()[0]['state'],'quarantined')
        with database(data) as db:
            row=db.execute('SELECT * FROM moves WHERE id=?',(key,)).fetchone();destination=Path(row['destination'])
            db.execute("UPDATE moves SET state='restoring' WHERE id=?",(key,))
        os.link(destination,path)
        self.assertEqual(client.post('/api/batches/'+batch+'/restore').json()['items'][0]['state'],'restored')
        self.assertEqual(path.read_bytes(),b'old');self.assertFalse(destination.exists())
    def test_quota_rejects_before_moving(self):
        data=self.root/'state';client=TestClient(create_app(data,[self.root]))
        path=self.root/'old.tmp';path.write_bytes(b'old');os.utime(path,(time.time()-9*86400,)*2)
        ident=client.post('/api/scans',json={'path':str(self.root)}).json()['id']
        for _ in range(200):
            job=client.get('/api/scans/'+ident).json()
            if job['state']!='running':break
            time.sleep(.01)
        with database(data) as db:db.execute('INSERT INTO moves VALUES(?,?,?,?,?,?,?,?)',('quota','synthetic','synthetic',2*1024**3,'fake','quarantined',time.time(),'batch'))
        key=job['result']['candidates'][0]['id']
        self.assertEqual(client.post('/api/cleanup',json={'scan_id':ident,'ids':[key],'confirm':True}).status_code,413)
        self.assertTrue(path.exists())

if __name__=='__main__':unittest.main()
