"""Break down first-part EXR read and preview packing costs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import Imath
import numpy as np
import OpenEXR


def profile(path: Path, scale: int, repeats: int):
    samples = []
    for _ in range(repeats):
        opened = time.perf_counter()
        source = OpenEXR.InputFile(str(path))
        header = source.header()
        dw = header['dataWindow']
        width, height = dw.max.x-dw.min.x+1, dw.max.y-dw.min.y+1
        channels = header['channels']
        dtype = np.float16 if all(channels[key].type.v == 1 for key in 'RGB') else np.float32
        pixel_type = Imath.PixelType(Imath.PixelType.HALF if dtype == np.float16 else Imath.PixelType.FLOAT)
        read = time.perf_counter()
        raw = source.channels(list('RGB'), pixel_type)
        unpacked = time.perf_counter()
        pixels = np.empty(((height+scale-1)//scale, (width+scale-1)//scale, 4), dtype=dtype)
        for axis, plane in enumerate(raw):
            pixels[..., axis] = np.frombuffer(plane, dtype=dtype).reshape(height, width)[::scale, ::scale]
        pixels[..., 3] = 1
        packed = time.perf_counter()
        source.close()
        samples.append(((read-opened)*1000, (unpacked-read)*1000, (packed-unpacked)*1000,
                        pixels.nbytes/1024**2))
    median = np.median(samples, axis=0)
    print(f'open/header {median[0]:.2f}ms; read/decompress {median[1]:.2f}ms; '
          f'pack/downsample {median[2]:.2f}ms; output {median[3]:.1f}MiB')


def compare_pack(path: Path, scale: int, repeats: int, pack_workers: int):
    source = OpenEXR.InputFile(str(path))
    try:
        dw = source.header()['dataWindow']
        width, height = dw.max.x-dw.min.x+1, dw.max.y-dw.min.y+1
        dtype = np.float16 if all(source.header()['channels'][key].type.v == 1 for key in 'RGB') else np.float32
        kind = Imath.PixelType(Imath.PixelType.HALF if dtype == np.float16 else Imath.PixelType.FLOAT)
        raw = source.channels(list('RGB'), kind)
    finally:
        source.close()
    planes = [np.frombuffer(item, dtype=dtype).reshape(height, width)[::scale, ::scale] for item in raw]
    def assign():
        out = np.empty((*planes[0].shape, 4), dtype=dtype)
        for axis, plane in enumerate(planes):
            out[..., axis] = plane
        out[..., 3] = 1
        return out
    def stack():
        return np.stack((*planes, np.ones_like(planes[0])), axis=-1)
    def rgb():
        out = np.empty((*planes[0].shape, 3), dtype=dtype)
        for axis, plane in enumerate(planes):
            out[..., axis] = plane
        return out
    with ThreadPoolExecutor(pack_workers) as pool:
        def threaded():
            out = np.empty((*planes[0].shape, 4), dtype=dtype)
            jobs = [pool.submit(np.copyto, out[..., axis], plane) for axis, plane in enumerate(planes)]
            jobs.append(pool.submit(out[..., 3].fill, 1))
            for job in jobs:
                job.result()
            return out
        methods = [('assign', assign), ('stack', stack), ('rgb', rgb), ('threaded', threaded)]
        for name, method in methods:
            method()
            times = []
            for _ in range(repeats):
                start = time.perf_counter()
                output = method()
                times.append((time.perf_counter()-start)*1000)
            print(f'{name}: {np.median(times):.2f}ms, {output.nbytes/1024**2:.1f}MiB')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('--scale', type=int, default=4, choices=(1, 2, 4, 8))
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--compare-pack', action='store_true')
    parser.add_argument('--pack-workers', type=int, default=4)
    args = parser.parse_args()
    OpenEXR.set_global_thread_count(8)
    if args.compare_pack:
        compare_pack(args.image, args.scale, args.repeats, args.pack_workers)
    else:
        profile(args.image, args.scale, args.repeats)
