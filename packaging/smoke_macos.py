"""Launch the bundled Mac app and verify an EXR frame reaches its GPU viewer."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import OpenEXR


def main():
    executable = Path('dist/Flick.app/Contents/MacOS/Flick').resolve()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for number in (1, 2):
            pixels = np.full((32, 32), number / 2, np.float16)
            OpenEXR.File({}, {channel: pixels for channel in 'RGB'}).write(
                str(root / f'shot.{number:04d}.exr'))
        report = root / 'report.json'
        env = os.environ.copy()
        env['FLICK_VERIFY_REPORT'] = str(report)
        result = subprocess.run(
            [str(executable), str(root / 'shot.0001.exr')],
            env=env, capture_output=True, text=True, timeout=40)
        if result.returncode:
            raise RuntimeError(f'Flick exited {result.returncode}: {result.stderr[-2000:]}')
        data = json.loads(report.read_text(encoding='utf-8'))
        assert data['loaded'] and data['gpu_valid'] and data['preload_ready'], data
        assert data['cached_frames'] == 2 and data['ocio_active'], data
        assert not data['gpu_error'] and not data['ocio_error'], data
        print('macOS EXR/GPU/OCIO smoke passed:', data)


if __name__ == '__main__':
    main()
