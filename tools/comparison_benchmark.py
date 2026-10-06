"""Synthetic inventory DB benchmark; no filesystem scan and no source contents."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.snapshot import Inventory
from app.comparison import compare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows',type=int,default=50000)
    args=parser.parse_args()
    if not 1<=args.rows<=1000000:parser.error('rows must be 1..1000000')
    with tempfile.TemporaryDirectory(prefix='disksage-diff-bench-') as directory:
        root=Path(directory);observed=root/'synthetic-observed'
        paths=[]
        for label,size in (('before',10),('after',12)):
            path=root/f'{label}.db';inventory=Inventory(path)
            inventory.db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?)',
                                    ((str(observed/f'file-{i:07}.bin'),str(observed),f'file-{i:07}.bin',size,4096,'.bin',0,'synthetic','1',str(i)) for i in range(args.rows)))
            inventory.finish({'root':str(observed),'details_complete':True,'coverage_complete':True,'complete':True},{observed:args.rows*size})
            paths.append(path)
        start=time.perf_counter();result=compare(*paths,offset=max(0,args.rows-100));seconds=time.perf_counter()-start
        assert result['total']==args.rows and result['delta_bytes']==2*args.rows
        assert len(result['items'])==min(100,args.rows)
        tracemalloc.start();compare(*paths,offset=max(0,args.rows-100));_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        print(json.dumps({'python':sys.version.split()[0],'rows_per_snapshot':args.rows,'total_changes':result['total'],
                          'last_page_seconds':round(seconds,6),'python_peak_bytes':peak,
                          'scope':'Synthetic SQLite logical-size comparison including aggregates; not OS scan throughput/native RSS or production SLA'},indent=2))


if __name__=='__main__':main()
