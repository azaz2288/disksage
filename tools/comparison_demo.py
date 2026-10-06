"""Temporary, synthetic UI demonstration. Never opens the normal app database."""
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8892)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='disksage-comparison-demo-') as directory:
        root = Path(directory)
        state = root / 'state'
        (state / 'scans').mkdir(parents=True)
        files = root / 'synthetic-project'
        files.mkdir()
        os.environ['APP_DATA_DIR'] = str(state)
        from app.engine import scan
        from app.main import create_app
        for i in range(130):
            (files / f'asset-{i:03}.bin').write_bytes(b'old')
        (files / 'not-observed-later.txt').write_bytes(b'12345')
        scan(files, [], threading.Event(), lambda _: None, inventory_path=state / 'scans' / 'before.db')
        for i in range(130):
            (files / f'asset-{i:03}.bin').write_bytes(b'new-long')
        (files / 'not-observed-later.txt').unlink()
        (files / 'new-asset.txt').write_bytes(b'xx')
        scan(files, [], threading.Event(), lambda _: None, inventory_path=state / 'scans' / 'after.db')
        import uvicorn
        uvicorn.run(create_app(state, [files]), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
