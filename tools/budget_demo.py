"""Isolated synthetic refresh budget demo; no normal inventories or cleanup."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8900)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='disksage-budget-demo-') as directory:
        root = Path(directory)
        state = root / 'state'
        (state / 'scans').mkdir(parents=True)
        files = root / 'synthetic-project'
        selected, outside = files / 'selected', files / 'outside'
        selected.mkdir(parents=True)
        outside.mkdir()
        os.environ['APP_DATA_DIR'] = str(state)
        from app.engine import scan
        from app.main import create_app
        import app.refresh as refresh_module
        (selected / 'sample.bin').write_bytes(b'old')
        (outside / 'historical.bin').write_bytes(b'keep')
        scan(files, [], threading.Event(), lambda _: None,
             inventory_path=state / 'scans' / 'baseline.db')
        (selected / 'sample.bin').write_bytes(b'new-six')
        (selected / 'added.bin').write_bytes(b'12')
        original = refresh_module.scan
        def synthetic_delay(*a, **kw):
            # Intentional demo delay, NOT a disk-performance measurement.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                a[2].check()
                time.sleep(.01)
            return original(*a, **kw)
        refresh_module.scan = synthetic_delay
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(create_app(state, []), host='127.0.0.1', port=args.port))
        def stop():
            try: input('Synthetic only. Enter stops demo.\n')
            except EOFError: pass
            server.should_exit = True
        threading.Thread(target=stop, daemon=True).start()
        server.run()


if __name__ == '__main__': main()
