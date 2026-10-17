"""Isolated synthetic metadata-change demo, never opens normal user inventories."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8902)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='disksage-changes-demo-') as directory:
        root = Path(directory)
        state = root/'state'
        (state/'scans').mkdir(parents=True)
        files = root/'synthetic-project'
        scope = files/'selected'
        scope.mkdir(parents=True)
        outside = files/'outside'
        outside.mkdir()
        os.environ['APP_DATA_DIR'] = str(state)
        from app.engine import scan
        from app.main import create_app
        for name, size in [('gone', 7), ('replace', 2), ('grow', 3), ('time', 4), ('same', 5)]:
            (scope/name).write_bytes(b'x'*size)
        (outside/'historical.bin').write_bytes(b'keep')
        scan(files, [], threading.Event(), lambda _: None, inventory_path=state/'scans'/'baseline.db')
        (scope/'gone').unlink()
        replacement = root/'replacement'
        replacement.write_bytes(b'x'*6)
        os.replace(replacement, scope/'replace')
        (scope/'grow').write_bytes(b'x'*8)
        item = scope/'time'
        old_time = item.stat().st_mtime_ns
        item.write_bytes(b'yyyy')
        os.utime(item, ns=(old_time+2000000000, old_time+2000000000))
        (scope/'added').write_bytes(b'x'*9)
        (outside/'historical.bin').write_bytes(b'not-revalidated')
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(create_app(state, []), host='127.0.0.1',
                                               port=args.port, timeout_graceful_shutdown=3))
        def stop():
            try: input('Synthetic only. Enter stops demo.\n')
            except EOFError: pass
            server.should_exit = True
        threading.Thread(target=stop, daemon=True).start()
        server.run()


if __name__ == '__main__': main()
