"""Compare EXR decode concurrency on a supplied sequence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import time

import OpenEXR

from .core import decode, discover


parser = argparse.ArgumentParser()
parser.add_argument('image')
parser.add_argument('--scale', type=int, default=4)
args = parser.parse_args()
paths = discover(args.image).paths
for global_threads, workers in [(0, 1), (2, 1), (2, 2), (4, 2), (8, 2), (4, 4), (8, 4)]:
    OpenEXR.set_global_thread_count(global_threads)
    started = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        sizes = list(pool.map(lambda p: decode(p, scale=args.scale).pixels.nbytes, paths))
    print(f'OpenEXR threads={global_threads} decode workers={workers}: '
          f'{time.perf_counter()-started:.3f}s, {sum(sizes)/1024**2:.1f}MiB')
