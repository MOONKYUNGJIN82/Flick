"""Measure real multipart EXR Beauty, Cryptomatte preview, matte and picking."""
import argparse
from pathlib import Path
import statistics
import time

from .core import decode, inspect_exr, pick_crypto


def measure(label, function, repeats):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = function()
        samples.append((time.perf_counter()-started)*1000)
    print(f'{label}: median={statistics.median(samples):.1f}ms '
          f'p95={sorted(samples)[int(.95*(len(samples)-1))]:.1f}ms '
          f'output={result.pixels.nbytes/1024**2:.1f}MiB' if hasattr(result, 'pixels') else
          f'{label}: median={statistics.median(samples):.1f}ms '
          f'p95={sorted(samples)[int(.95*(len(samples)-1))]:.1f}ms')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('--scale', type=int, default=4, choices=(1, 2, 4, 8))
    parser.add_argument('--repeats', type=int, default=7)
    args = parser.parse_args()
    _, layers = inspect_exr(args.image)
    layer = next(iter(layers))
    selected = next(iter(layers[layer][2].values()))
    measure('Beauty', lambda: decode(args.image, scale=args.scale), args.repeats)
    measure('Crypto preview', lambda: decode(args.image, layer=layer, scale=args.scale), args.repeats)
    measure('Crypto matte', lambda: decode(args.image, layer=layer, selected=selected,
                                          scale=args.scale), args.repeats)
    measure('Click pick', lambda: pick_crypto(args.image, layer, 48, 32), args.repeats)
