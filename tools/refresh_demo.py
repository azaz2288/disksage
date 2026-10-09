"""Isolated synthetic scope-refresh UI; never loads normal app inventories."""
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8896)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='disksage-refresh-demo-') as directory:
        root = Path(directory)
        state = root / 'state'
        (state / 'scans').mkdir(parents=True)
        files = root / 'synthetic-project'
        scope, outside = files / 'selected', files / 'outside'
        scope.mkdir(parents=True)
        outside.mkdir()
        os.environ['APP_DATA_DIR'] = str(state)
        from app.engine import scan
        from app.main import create_app
        (scope / 'sample.bin').write_bytes(b'old')
        (outside / 'historical.bin').write_bytes(b'keep')
        scan(files, [], threading.Event(), lambda _: None, inventory_path=state / 'scans' / 'baseline.db')
        (scope / 'sample.bin').write_bytes(b'new-six')
        (scope / 'added.bin').write_bytes(b'12')
        (outside / 'historical.bin').write_bytes(b'outside-changed-not-revalidated')
        import uvicorn
        uvicorn.run(create_app(state, []), host='127.0.0.1', port=args.port)

if __name__ == '__main__': main()
