from pathlib import Path
import json,tempfile,threading,time,tracemalloc,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.engine import scan
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    for folder in range(25):
        target=root/str(folder);target.mkdir()
        for i in range(100):(target/f'{i}.txt').write_bytes(b'x'*1024)
    tracemalloc.start();started=time.perf_counter()
    result=scan(root,[],threading.Event(),lambda _:None)
    _,peak=tracemalloc.get_traced_memory()
    print(json.dumps({'files':result['files'],'bytes':result['bytes'],'seconds':round(time.perf_counter()-started,3),'python_peak_bytes':peak,'complete':result['complete']}))
