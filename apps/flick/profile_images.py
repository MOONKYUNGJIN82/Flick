"""Compare Pillow preview reductions on supplied JPG/PNG/TGA images."""
import argparse
from pathlib import Path
import statistics
import time

import numpy as np
from PIL import Image

from .core import decode


def measure(path, scale, repeats):
    for method in ('current', 'resize', 'reduce', 'reduce_rgb'):
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            if method == 'current':
                pixels = decode(path, scale=scale).pixels
            else:
                with Image.open(path) as source:
                    size = ((source.width+scale-1)//scale, (source.height+scale-1)//scale)
                    reduced = (source.resize(size, Image.Resampling.BILINEAR)
                               if method == 'resize' else source.reduce(scale))
                    pixels = np.asarray(reduced.convert('RGB' if method == 'reduce_rgb' else 'RGBA'))
            samples.append((time.perf_counter()-started)*1000)
        print(f'{path.name} {method}: {statistics.median(samples):.2f}ms '
              f'{pixels.nbytes/1024**2:.1f}MiB')
    with Image.open(path) as source:
        size = ((source.width+scale-1)//scale, (source.height+scale-1)//scale)
        reference = np.asarray(source.resize(size, Image.Resampling.BILINEAR).convert('RGB'),
                               dtype=np.int16)
    candidate = decode(path, scale=scale).pixels[..., :3].astype(np.int16)
    difference = np.abs(reference-candidate)
    print(f'{path.name} vs bilinear RGB: mean absolute {difference.mean():.2f}, '
          f'99th percentile {np.percentile(difference, 99):.0f}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('images', nargs='+', type=Path)
    parser.add_argument('--scale', type=int, default=4, choices=(2, 4, 8))
    parser.add_argument('--repeats', type=int, default=10)
    args = parser.parse_args()
    for image in args.images:
        measure(image, args.scale, args.repeats)
