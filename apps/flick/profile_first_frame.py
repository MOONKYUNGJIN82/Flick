"""Simulate slow header storage and report first-image latency."""
import argparse
import os
from pathlib import Path
import time

import numpy as np

from .core import Frame, Loader, Sequence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--disable-early-frame', action='store_true')
    args = parser.parse_args()
    if args.disable_early_frame:
        os.environ['FLICK_DISABLE_EARLY_FRAME'] = '1'
    paths = tuple(Path(str(index)) for index in range(40))
    def estimate(_path):
        time.sleep(.01)
        return 16
    def decode_frame(_path):
        time.sleep(.015)
        return Frame(np.zeros((2, 2, 4), np.uint8), False, 15)
    loader = Loader(budget=40*16, workers=2, decoder=decode_frame, estimator=estimate)
    try:
        loader.open(Sequence(paths, tuple(range(40))))
        started = time.perf_counter()
        loader.preload(0, 39, priority=7)
        first = None
        while not loader.ready:
            loader.pump()
            if first is None and loader.frame(7) is not None:
                first = time.perf_counter()-started
            if time.perf_counter()-started > 5:
                raise RuntimeError(loader.error or 'Timed out')
            time.sleep(.002)
        print(f'first_frame={first:.3f}s ready={time.perf_counter()-started:.3f}s '
              f'decodes={loader.submitted}')
    finally:
        loader.close()


if __name__ == '__main__':
    main()
