"""Compare Auto-resolution header checks on many distinct local file paths."""
import argparse
import os
from pathlib import Path
import time
import uuid

from .core import _frame_layout, frame_bytes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('--frames', type=int, default=300)
    args = parser.parse_args()
    source = args.image.resolve()
    folder = source.parent / ('flick-headers-' + uuid.uuid4().hex)
    folder.mkdir()
    paths = []
    try:
        for index in range(args.frames):
            link = folder / f'{index:06d}{source.suffix}'
            os.link(source, link)
            paths.append(link)
        scales = (1, 2, 4, 8)
        started = time.perf_counter()
        uncached = []
        for scale in scales:
            _frame_layout.cache_clear()
            uncached.extend(frame_bytes(path, scale=scale) for path in paths)
        cold = time.perf_counter()-started
        _frame_layout.cache_clear()
        started = time.perf_counter()
        cached = [frame_bytes(path, scale=scale) for scale in scales for path in paths]
        warm = time.perf_counter()-started
        assert uncached == cached
        print(f'{len(paths)} paths x {len(scales)} scales: '
              f'uncached {cold:.3f}s; cached {warm:.3f}s')
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        folder.rmdir()


if __name__ == '__main__':
    main()
