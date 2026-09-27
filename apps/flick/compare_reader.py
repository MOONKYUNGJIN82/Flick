"""Compare channel-selective and all-channel EXR readers on one image."""
import argparse
from pathlib import Path
import time

import numpy as np
import OpenEXR

from .core import decode_first_part, rgb_keys


parser = argparse.ArgumentParser()
parser.add_argument('image', type=Path)
parser.add_argument('--scale', type=int, default=4)
args = parser.parse_args()
OpenEXR.set_global_thread_count(8)


def modern():
    with OpenEXR.File(str(args.image), separate_channels=True) as source:
        channels = source.channels(0)
        keys, prefix = rgb_keys(channels)
        planes = [channels[k].pixels[::args.scale, ::args.scale] for k in keys]
        pixels = np.empty((*planes[0].shape, 4), dtype=planes[0].dtype)
        for axis, plane in enumerate(planes):
            pixels[..., axis] = plane
        pixels[..., 3] = channels[prefix+'A'].pixels[::args.scale, ::args.scale] if prefix+'A' in channels else 1
        return pixels


def interleaved():
    with OpenEXR.File(str(args.image), separate_channels=False) as source:
        channels = source.channels(0)
        # The modern reader coalesces RGB(A) into one NumPy array.
        array = next(channel.pixels for channel in channels.values()
                     if channel.pixels.ndim == 3 and channel.pixels.shape[2] in (3, 4))
        reduced = np.ascontiguousarray(array[::args.scale, ::args.scale])
        if args.scale == 1 and reduced.shape[2] == 3:
            output = np.empty((*reduced.shape[:2], 4), dtype=reduced.dtype)
            output[..., :3] = reduced
            output[..., 3] = 1
            return output
        return reduced


for label, function in [('selective', lambda: decode_first_part(args.image, args.scale)[0]),
                        ('all channels', modern), ('interleaved', interleaved)]:
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        pixels = function()
        samples.append(time.perf_counter()-start)
    print(label, f'median={np.median(samples):.3f}s', f'pixels={pixels.nbytes/1024**2:.1f}MiB')
