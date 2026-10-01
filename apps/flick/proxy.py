"""Versioned, bounded disk cache for reduced-resolution playback frames."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
import pickle
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid

import numpy as np

from .core import Frame, decode, frame_bytes


CACHE_VERSION = 3
DEFAULT_LIMIT = 10 * 1024**3


def default_cache_dir() -> Path:
    override = os.environ.get('FLICK_PROXY_CACHE_DIR')
    if override:
        return Path(override)
    if sys.platform == 'darwin':
        from PySide6.QtCore import QStandardPaths
        return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / 'proxy'
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'Flick' / 'proxy'


def proxy_note(path, layer, selected):
    if layer:
        return f'Cryptomatte · {layer} · {"Preview" if selected is None else "Matte"}'
    return 'EXR · proxy' if path.suffix.lower() == '.exr' else 'Image · proxy'


class ProxyStore:
    def __init__(self, root=None, limit=DEFAULT_LIMIT):
        self.root = Path(root) if root is not None else default_cache_dir()
        self.limit = limit
        self.lock = threading.Lock()
        self.writes = 0
        self.hits = 0
        self.misses = 0
        self.writer = ThreadPoolExecutor(1, thread_name_prefix='flick-proxy')
        self.pending = set()
        self.pending_bytes = 0

    def cache_path(self, path, layer, selected, scale):
        path = Path(path).resolve()
        stat = path.stat()
        identity = [CACHE_VERSION, str(path), stat.st_size, stat.st_mtime_ns,
                    layer, selected, scale]
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()
        return self.root / digest[:2] / ('flick-v3-' + digest + '.npy')

    def estimate(self, path, layer=None, selected=None, scale=1, crypto_hint=None):
        if scale > 1:
            try:
                pixels = np.load(self.cache_path(path, layer, selected, scale),
                                 mmap_mode='r', allow_pickle=False)
                if pixels.ndim == 3 and pixels.shape[2] in (3, 4) and pixels.dtype in (np.uint8, np.float16, np.float32):
                    size = pixels.nbytes
                    del pixels
                    return size
            except (OSError, ValueError, EOFError, pickle.UnpicklingError):
                pass
        return frame_bytes(path, layer=layer, scale=scale, crypto_hint=crypto_hint)

    def decode(self, path, layer=None, selected=None, scale=1, crypto_hint=None):
        if scale == 1:
            return decode(path, layer=layer, selected=selected, scale=scale, crypto_hint=crypto_hint)
        started = time.perf_counter()
        target = self.cache_path(path, layer, selected, scale)
        try:
            pixels = np.load(target, allow_pickle=False)
            if pixels.ndim != 3 or pixels.shape[2] not in (3, 4) or pixels.dtype not in (np.uint8, np.float16, np.float32):
                raise ValueError('Invalid proxy pixels')
            os.utime(target, None)
            with self.lock:
                self.hits += 1
            return Frame(pixels, Path(path).suffix.lower() == '.exr' and layer is None,
                         (time.perf_counter()-started)*1000, proxy_note(Path(path), layer, selected))
        except (OSError, ValueError, EOFError, pickle.UnpicklingError):
            pass
        with self.lock:
            self.misses += 1
        frame = decode(path, layer=layer, selected=selected, scale=scale, crypto_hint=crypto_hint)
        if frame.pixels.nbytes > self.limit:
            return frame
        self.schedule(target, frame.pixels)
        return frame

    def schedule(self, target, pixels):
        # Bound queued decoded frames while the disk writer catches up.
        with self.lock:
            if len(self.pending) >= 8 or self.pending_bytes + pixels.nbytes > 128*1024**2:
                return
            self.pending_bytes += pixels.nbytes
            try:
                future = self.writer.submit(self.save, target, pixels)
            except RuntimeError:
                self.pending_bytes -= pixels.nbytes
                return
            self.pending.add(future)
        future.add_done_callback(lambda done: self.finished(done, pixels.nbytes))

    def finished(self, future, size):
        with self.lock:
            self.pending.discard(future)
            self.pending_bytes -= size

    def flush(self):
        with self.lock:
            pending = list(self.pending)
        for future in pending:
            future.result()

    def close(self):
        self.writer.shutdown(wait=False)

    def save(self, target, pixels):
        temporary = target.with_name(target.stem + '.' + uuid.uuid4().hex + '.tmp')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open('wb') as output:
                np.save(output, pixels, allow_pickle=False)
            os.replace(temporary, target)
            with self.lock:
                self.writes += 1
                if self.writes == 1 or self.writes % 32 == 0:
                    self.prune()
        except OSError:
            # A full or read-only cache must never prevent normal playback.
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def prune(self):
        """Caller holds lock; remove only this store's oldest proxy files."""
        files = []
        total = 0
        for path in self.root.glob('*/flick-v*-*.npy'):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime_ns, path, stat.st_size))
            total += stat.st_size
        if total <= self.limit:
            return
        for _, path, size in sorted(files):
            try:
                path.unlink()
                total -= size
            except OSError:
                continue
            if total <= self.limit:
                break
