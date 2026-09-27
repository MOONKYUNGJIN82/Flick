"""Measure actual sequence decode throughput without claiming display FPS."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time

import OpenEXR
from .core import decode, discover


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image', help='Any image in the sequence')
    parser.add_argument('--frames', type=int, default=48)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--exr-threads', type=int, default=2)
    args = parser.parse_args()
    sequence = discover(args.image)
    paths = sequence.paths[:max(1, args.frames)]
    OpenEXR.set_global_thread_count(args.exr_threads)
    start = time.perf_counter()
    total_bytes = 0
    # Consume each result inside the worker so decoded frames do not accumulate.
    def measure(path):
        return decode(path).pixels.nbytes
    with ThreadPoolExecutor(args.workers) as pool:
        for size in pool.map(measure, paths):
            total_bytes += size
    elapsed = time.perf_counter()-start
    print(json.dumps({'frames': len(paths), 'seconds': round(elapsed, 3),
                      'decode_fps': round(len(paths)/elapsed, 2),
                      'decoded_MiB_per_second': round(total_bytes/1024**2/elapsed, 2),
                      'note': 'Includes file reads and decode. OS cache may be warm; excludes GPU/display.'}, indent=2))


if __name__ == '__main__':
    main()
