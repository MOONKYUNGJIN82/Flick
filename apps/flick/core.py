from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import json
import os
import time
import threading
from bisect import bisect_left, bisect_right

import numpy as np
from PIL import Image
import OpenEXR
import Imath  # Legacy header() imports this from native code; load before worker threads.

SUPPORTED = {'.exr', '.jpg', '.jpeg', '.png', '.tga'}
PACK_POOL = ThreadPoolExecutor(4, thread_name_prefix='flick-pack')


@dataclass(frozen=True)
class Sequence:
    paths: tuple[Path, ...]
    numbers: tuple[int, ...]
    missing: int = 0

    def bounds(self, first: int, last: int) -> tuple[int, int]:
        """Inclusive file-number range; missing numbers do not shift boundaries."""
        lo, hi = bisect_left(self.numbers, first), bisect_right(self.numbers, last)-1
        if first > last or lo > hi:
            raise ValueError('지정한 구간에 프레임이 없습니다. 시작·끝 번호를 확인하세요.')
        return lo, hi


def rgb_keys(names):
    names = {n if isinstance(n, str) else n.name for n in names}
    if {'R', 'G', 'B'} <= names:
        return ['R', 'G', 'B'], ''
    prefixes = sorted(n[:-1] for n in names if n.endswith('.R') and 'Crypto' not in n and
                      {n[:-1] + 'G', n[:-1] + 'B'} <= names)
    if prefixes:
        return [prefixes[0]+c for c in 'RGB'], prefixes[0]
    if 'Y' in names:
        return ['Y']*3, ''
    raise ValueError('표시할 RGB 또는 Y 채널이 없습니다.')


def crypto_layers(header):
    """Return embedded Cryptomatte manifests and their channel prefixes."""
    names = {n.name for n in header['channels']}
    result = {}
    for key, value in header.items():
        if not key.startswith('cryptomatte/') or not key.endswith('/name'):
            continue
        layer = str(value)
        manifest = header.get(key[:-4] + 'manifest')
        if not manifest:
            continue
        try:
            entries = {name: int(bits, 16) for name, bits in json.loads(str(manifest)).items()}
        except (ValueError, TypeError):
            continue
        prefix = next((n.split('00.')[0] for n in names if re.search(r'00\.(red|r|R)$', n) and
                       n.split('00.')[0].endswith(layer)), None)
        if prefix is None:
            prefix = layer
        result[layer] = (prefix, entries)
    return result


def crypto_passes(source):
    """Resolve metadata to pixel channels, including Blender's separate EXR parts."""
    definitions = {}
    for part in range(len(source.parts)):
        definitions.update(crypto_layers(source.header(part)))
    result = {}
    for layer, (_, entries) in definitions.items():
        for part in range(len(source.parts)):
            names = {channel.name for channel in source.header(part)['channels']}
            candidates = [m.group(1) for name in names
                          if (m := re.match(r'(.+)00\.(?:R|r|red)$', name)) and m.group(1).endswith(layer)]
            if candidates:
                result[layer] = (part, sorted(candidates)[0], entries)
                break
    return result


def inspect_exr(path):
    """Read only EXR headers for available beauty and Cryptomatte passes."""
    with OpenEXR.File(str(path), header_only=True) as source:
        beauty = False
        layers = crypto_passes(source)
        for part in range(len(source.parts)):
            header = source.header(part)
            try:
                rgb_keys(header['channels'])
                beauty = True
            except ValueError:
                pass
        return beauty, layers


def crypto_channels(channels, prefix):
    for rank in range(100):
        root = f'{prefix}{rank:02d}.'
        suffixes = (('red', 'green', 'blue', 'alpha') if root+'red' in channels else
                    ('r', 'g', 'b', 'a') if root+'r' in channels else ('R', 'G', 'B', 'A'))
        if root+suffixes[0] not in channels:
            if rank == 0:
                raise ValueError('Cryptomatte 채널이 없습니다.')
            break
        yield tuple(channels[root+s].pixels for s in suffixes)


def crypto_preview_color(bits):
    """Match the stable RGB hash used by the Cryptomatte preview shader input."""
    bits = int(bits) & 0xffffffff
    return tuple(((bits >> shift) ^ (bits >> ((shift+13) % 24))) & 255
                 for shift in (0, 8, 16))


class CryptoRankCache:
    """Bounded decoded Cryptomatte planes shared across preview/matte changes."""
    def __init__(self, limit=128*1024**2):
        self.limit = limit
        self.items = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            ranks = self.items.get(key)
            if ranks is not None:
                self.items.move_to_end(key)
            return ranks

    def put(self, key, ranks):
        size = sum(plane.nbytes for rank in ranks for plane in rank)
        if size > self.limit:
            return
        with self.lock:
            previous = self.items.pop(key, None)
            if previous is not None:
                self.bytes -= sum(plane.nbytes for rank in previous for plane in rank)
            self.items[key] = ranks
            self.bytes += size
            while self.bytes > self.limit:
                _, old = self.items.popitem(last=False)
                self.bytes -= sum(plane.nbytes for rank in old for plane in rank)

    def clear(self):
        with self.lock:
            self.items.clear()
            self.bytes = 0


CRYPTO_RANK_CACHE = CryptoRankCache()


def crypto_ranks(channels, prefix, scale):
    # Copy reduced views so the cache does not retain full-resolution planes.
    return tuple(tuple(np.ascontiguousarray(plane[::scale, ::scale]) for plane in rank)
                 for rank in crypto_channels(channels, prefix))


def crypto_pixels_from_ranks(ranks, selected=None):
    """Decode either a false-color preview or coverage matte from bitcast IDs."""
    shape = ranks[0][0].shape
    if any(plane.dtype == object or plane.shape != shape for rank in ranks for plane in rank):
        raise ValueError('Deep EXR은 아직 지원하지 않습니다.')
    if selected is None:
        # The nearest non-empty rank provides a stable, recognizable pick preview.
        ids = np.zeros(shape, dtype=np.uint32)
        coverage = np.zeros(shape, dtype=np.float32)
        for id0, cov0, id1, cov1 in ranks:
            for id_plane, cov_plane in ((id0, cov0), (id1, cov1)):
                take = (coverage == 0) & (cov_plane > 0)
                ids[take] = np.asarray(id_plane, dtype=np.float32).view(np.uint32)[take]
                coverage[take] = cov_plane[take]
        pixels = np.empty((*shape, 4), dtype=np.uint8)
        for axis, shift in enumerate((0, 8, 16)):
            pixels[..., axis] = ((ids >> shift) ^ (ids >> ((shift+13) % 24))).astype(np.uint8)
        pixels[..., :3] = np.where((coverage > 0)[..., None], pixels[..., :3], 0)
        pixels[..., 3] = 255
        return pixels
    matte = np.zeros(shape, dtype=np.float32)
    target = np.uint32(selected)
    for id0, cov0, id1, cov1 in ranks:
        for id_plane, cov_plane in ((id0, cov0), (id1, cov1)):
            matte += np.where(np.asarray(id_plane, dtype=np.float32).view(np.uint32) == target,
                              cov_plane, 0)
    gray = np.rint(np.clip(matte, 0, 1)*255).astype(np.uint8)
    pixels = np.empty((*shape, 4), dtype=np.uint8)
    pixels[..., :3] = gray[..., None]
    pixels[..., 3] = 255
    return pixels


def crypto_pixels(channels, prefix, selected=None, scale=1):
    return crypto_pixels_from_ranks(crypto_ranks(channels, prefix, scale), selected)


def pick_crypto(path, layer, x, y):
    """Find the strongest Cryptomatte ID at one displayed source pixel."""
    with OpenEXR.File(str(path), separate_channels=True) as source:
        passes = crypto_passes(source)
        if layer in passes:
            part, prefix, entries = passes[layer]
            reverse = {bits: name for name, bits in entries.items()}
            best = (0.0, None)
            for rank in crypto_channels(source.channels(part), prefix):
                for id_plane, cov_plane in ((rank[0], rank[1]), (rank[2], rank[3])):
                    if not 0 <= y < id_plane.shape[0] or not 0 <= x < id_plane.shape[1]:
                        return None
                    coverage = float(cov_plane[y, x])
                    if coverage > best[0]:
                        bits = int(np.asarray(id_plane[y, x], dtype=np.float32).view(np.uint32))
                        best = (coverage, reverse.get(bits))
            return best[1]
    return None


@lru_cache(maxsize=8192)
def _frame_layout(path: Path, size: int, modified_ns: int, layer=None, crypto_hint=None):
    """Cache dimensions and channel types across Auto resolution retries."""
    if path.suffix.lower() == '.exr' and layer is not None:
        with OpenEXR.File(str(path), header_only=True) as source:
            passes = crypto_passes(source) if crypto_hint is None else {layer: (*crypto_hint, {})}
            if layer in passes:
                part, prefix, _ = passes[layer]
                if part >= len(source.parts) or not any(c.name.startswith(prefix+'00.') for c in source.header(part)['channels']):
                    passes = crypto_passes(source)
                    if layer not in passes:
                        raise ValueError(f'Cryptomatte 레이어가 없습니다: {layer}')
                    part = passes[layer][0]
                dw = source.header(part)['dataWindow']
                w, h = int(dw[1][0]-dw[0][0]+1), int(dw[1][1]-dw[0][1]+1)
                return w, h, 1, True, True
        raise ValueError(f'Cryptomatte 레이어가 없습니다: {layer}')
    if path.suffix.lower() == '.exr':
        # Most render sequences put Beauty in the first part. The legacy header
        # includes channel types, so one file open is enough for the estimate.
        source = OpenEXR.InputFile(str(path))
        try:
            header = source.header()
            channels = header['channels']
            try:
                keys, _ = rgb_keys(channels)
            except ValueError:
                pass
            else:
                dw = header['dataWindow']
                w, h = dw.max.x-dw.min.x+1, dw.max.y-dw.min.y+1
                component_bytes = 2 if all(channels[key].type.v == 1 for key in keys) else 4
                return w, h, component_bytes, keys[0][:-1]+'A' in channels, False
        finally:
            source.close()
        with OpenEXR.File(str(path), header_only=True) as source:
            for part in range(len(source.parts)):
                header = source.header(part)
                channels = {channel.name: channel for channel in header['channels']}
                try:
                    keys, _ = rgb_keys(channels)
                except ValueError:
                    continue
                # Other parts lack pixel type information here: use a safe upper bound.
                component_bytes = 4
                dw = header['dataWindow']
                w, h = int(dw[1][0]-dw[0][0]+1), int(dw[1][1]-dw[0][1]+1)
                return w, h, component_bytes, keys[0][:-1]+'A' in channels, False
        raise ValueError('표시할 RGB 또는 Y 채널이 없습니다.')
    with Image.open(path) as source:
        return source.width, source.height, 1, source.mode != 'RGB', False


def frame_bytes(path: Path, layer=None, scale=1, crypto_hint=None) -> int:
    """Estimate the exact output allocation, reusing unchanged source headers."""
    path = Path(path)
    stat = path.stat()
    w, h, component_bytes, has_alpha, is_crypto = _frame_layout(
        path, stat.st_size, stat.st_mtime_ns, layer, crypto_hint)
    components = 4 if scale == 1 or has_alpha or is_crypto else 3
    return ((w+scale-1)//scale)*((h+scale-1)//scale)*components*component_bytes


def plan_range(paths, cancel, estimator=frame_bytes):
    sizes = []
    if not paths:
        return sizes
    sample_count = min(4, len(paths))
    started = time.perf_counter()
    for path in paths[:sample_count]:
        if cancel.is_set():
            return None
        sizes.append(estimator(path))
    # Local header reads are faster without thread scheduling. Only switch
    # when a long sequence's first reads show slow storage or high latency.
    if len(paths) < 32 or (time.perf_counter()-started)/sample_count < .002:
        for path in paths[sample_count:]:
            if cancel.is_set():
                return None
            sizes.append(estimator(path))
        return sizes
    # Limit in-flight file opens. Long sequences on slow storage can inspect
    # independent headers concurrently without creating a job per frame.
    with ThreadPoolExecutor(4, thread_name_prefix='flick-plan') as pool:
        for start in range(sample_count, len(paths), 4):
            if cancel.is_set():
                return None
            futures = [pool.submit(estimator, path) for path in paths[start:start+4]]
            for future in futures:
                sizes.append(future.result())
    return sizes


def discover(filename: str | Path) -> Sequence:
    path = Path(filename).resolve()
    if not path.is_file() or path.suffix.lower() not in SUPPORTED:
        raise ValueError('EXR, JPG, PNG 또는 TGA 파일을 선택하세요.')
    match = re.fullmatch(r'(.*?)(\d+)', path.stem)
    if not match:
        return Sequence((path,), (0,))
    prefix, digits = match.groups()
    pattern = re.compile(re.escape(prefix) + r'(\d{' + str(len(digits)) + r'})' + re.escape(path.suffix), re.I)
    pairs = []
    for sibling in path.parent.iterdir():
        found = pattern.fullmatch(sibling.name)
        if found and sibling.is_file():
            pairs.append((int(found[1]), sibling))
    pairs.sort(key=lambda item: (item[0], item[1].name))
    numbers = tuple(item[0] for item in pairs)
    return Sequence(tuple(item[1] for item in pairs), numbers,
                    numbers[-1] - numbers[0] + 1 - len(set(numbers)))


@dataclass
class Frame:
    pixels: np.ndarray
    linear: bool
    milliseconds: float
    note: str = ''


def decode_first_part(path, scale):
    """Read only displayed channels from the first EXR part when possible."""
    source = OpenEXR.InputFile(str(path))
    try:
        header = source.header()
        channels = header['channels']
        try:
            keys, prefix = rgb_keys(channels)
        except ValueError:
            return None
        dw = header['dataWindow']
        width, height = dw.max.x-dw.min.x+1, dw.max.y-dw.min.y+1
        dtype = np.float16 if all(channels[key].type.v == 1 for key in keys) else np.float32
        pixel_type = Imath.PixelType(Imath.PixelType.HALF if dtype == np.float16 else Imath.PixelType.FLOAT)
        raw = source.channels(keys, pixel_type)
        alpha = prefix+'A'
        pixels = np.empty(((height+scale-1)//scale, (width+scale-1)//scale,
                           4 if scale == 1 or alpha in channels else 3), dtype=dtype)
        planes = [np.frombuffer(plane, dtype=dtype).reshape(height, width)[::scale, ::scale]
                  for plane in raw]
        if alpha in channels:
            plane = source.channel(alpha, pixel_type)
            planes.append(np.frombuffer(plane, dtype=dtype).reshape(height, width)[::scale, ::scale])
        if (scale == 1 and width*height >= 1024**2 and
                os.environ.get('FLICK_DISABLE_PARALLEL_PACK') != '1'):
            jobs = [PACK_POOL.submit(np.copyto, pixels[..., axis], plane)
                    for axis, plane in enumerate(planes)]
            if alpha not in channels:
                jobs.append(PACK_POOL.submit(pixels[..., 3].fill, 1))
            for job in jobs:
                job.result()
        else:
            for axis, plane in enumerate(planes):
                pixels[..., axis] = plane
            if scale == 1 and alpha not in channels:
                pixels[..., 3] = 1
        return pixels, f'EXR · {prefix or "RGB"} · part 1 · data window'
    finally:
        source.close()


def decode(path: Path, layer=None, selected=None, scale=1, crypto_hint=None) -> Frame:
    if scale not in (1, 2, 4, 8):
        raise ValueError('Invalid preview resolution')
    start = time.perf_counter()
    note = ''
    if path.suffix.lower() == '.exr':
        if layer is not None:
            use_rank_cache = os.environ.get('FLICK_DISABLE_CRYPTO_PLANE_CACHE') != '1'
            if use_rank_cache:
                stat = path.stat()
                key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, layer, scale)
                ranks = CRYPTO_RANK_CACHE.get(key)
            else:
                ranks = None
            if ranks is None:
                with OpenEXR.File(str(path), separate_channels=True) as source:
                    passes = crypto_passes(source) if crypto_hint is None else {layer: (*crypto_hint, {})}
                    if crypto_hint is not None:
                        part, prefix = crypto_hint
                        if part >= len(source.parts) or not any(k.startswith(prefix+'00.') for k in source.channels(part)):
                            passes = crypto_passes(source)
                    if layer not in passes:
                        raise ValueError(f'Cryptomatte 레이어가 없습니다: {layer}')
                    part, prefix, _ = passes[layer]
                    ranks = crypto_ranks(source.channels(part), prefix, scale)
                if use_rank_cache:
                    CRYPTO_RANK_CACHE.put(key, ranks)
            pixels = crypto_pixels_from_ranks(ranks, selected)
            note = f'Cryptomatte · {layer} · {"Preview" if selected is None else "Matte"}'
            return Frame(pixels, False, (time.perf_counter()-start)*1000, note)
        fast = decode_first_part(path, scale)
        if fast is not None:
            pixels, note = fast
            return Frame(pixels, True, (time.perf_counter()-start)*1000, note)
        with OpenEXR.File(str(path), separate_channels=True) as source:
            for part in range(len(source.parts)):
                channels = source.channels(part)
                names = set(channels)
                try:
                    keys, prefix = rgb_keys(names)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError('표시할 RGB 또는 Y 채널이 없습니다.')
            planes = [channels[k].pixels[::scale, ::scale] for k in keys]
            if any(p.dtype == object or p.ndim != 2 for p in planes):
                raise ValueError('Deep EXR은 아직 지원하지 않습니다.')
            dtype = np.float32 if any(p.dtype != np.float16 for p in planes) else np.float16
            pixels = np.empty((*planes[0].shape, 4 if scale == 1 or prefix+'A' in names else 3), dtype=dtype)
            for i, plane in enumerate(planes):
                pixels[..., i] = plane
            if prefix + 'A' in names:
                pixels[..., 3] = channels[prefix + 'A'].pixels[::scale, ::scale]
            elif scale == 1:
                pixels[..., 3] = 1
            note = f'EXR · {prefix or "RGB"} · part {part+1}/{len(source.parts)} · data window'
            linear = True
    else:
        with Image.open(path) as source:
            rgb_preview = scale > 1 and source.mode == 'RGB'
            output_mode = 'RGB' if rgb_preview else 'RGBA'
            if scale > 1:
                size = ((source.width+scale-1)//scale, (source.height+scale-1)//scale)
                original_size = source.size
                source.draft(output_mode, size)
                if source.size != size:
                    source = (source.reduce(scale) if rgb_preview and source.size == original_size
                              else source.resize(size, Image.Resampling.BILINEAR))
            pixels = np.array(source.convert(output_mode))
        linear = False
    return Frame(np.ascontiguousarray(pixels), linear,
                 (time.perf_counter() - start) * 1000, note)


class FrameCache:
    def __init__(self, budget: int):
        self.budget = budget
        self.items = OrderedDict()
        self.bytes = 0

    def get(self, index):
        frame = self.items.get(index)
        if frame is not None:
            self.items.move_to_end(index)
        return frame

    def put(self, index, frame):
        if frame.pixels.nbytes > self.budget:
            return False
        old = self.items.pop(index, None)
        if old is not None:
            self.bytes -= old.pixels.nbytes
        self.items[index] = frame
        self.bytes += frame.pixels.nbytes
        self.trim()
        return True

    def trim(self):
        while self.bytes > self.budget and self.items:
            _, frame = self.items.popitem(last=False)
            self.bytes -= frame.pixels.nbytes

    def clear(self):
        self.items.clear()
        self.bytes = 0

    def remove(self, index):
        frame = self.items.pop(index, None)
        if frame is not None:
            self.bytes -= frame.pixels.nbytes

    def keep_range(self, first, last):
        for index in list(self.items):
            if not first <= index <= last:
                self.bytes -= self.items.pop(index).pixels.nbytes


class Loader:
    """UI-thread-owned cache with a bounded native decode queue."""
    def __init__(self, budget=2 * 1024**3, workers=None, decoder=decode, estimator=frame_bytes):
        if workers is None:
            workers = min(4, max(2, os.cpu_count() or 2))
        if workers < 1:
            raise ValueError('At least one decode worker is required')
        self.cache = FrameCache(budget)
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix='flick-decode')
        self.workers = workers
        self.decoder = decoder
        self.estimator = estimator
        self.planner = ThreadPoolExecutor(1, thread_name_prefix='flick-headers')
        self.plan = None
        self.cancel_event = threading.Event()
        self.sequence = None
        self.generation = 0
        self.jobs = {}
        self.errors = {}
        self.wanted = []
        self.state = 'idle'
        self.error = ''
        self.selection = None
        self.required_bytes = 0
        self.loaded_count = 0
        self.cursor = 0
        self.order = []
        self.submitted = 0
        self.preview_job = None
        self.preview_index = None
        self.preview_frame = None
        self.speculative_index = None
        self.speculative_frame = None
        self.cache_signatures = {}

    @staticmethod
    def file_signature(path):
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def cancel(self):
        self.generation += 1
        self.cancel_event.set()
        if self.plan:
            self.plan.cancel()
            self.plan = None
        for job in self.jobs:
            job.cancel()
        self.wanted = []
        if self.preview_job:
            self.preview_job.cancel()
        self.preview_job = None
        self.preview_index = None
        self.preview_frame = None
        self.speculative_index = None
        self.speculative_frame = None
        self.state = 'cancelled'

    def preload(self, first, last, priority=None, reuse=False):
        if not self.sequence or not 0 <= first <= last < len(self.sequence.paths):
            raise ValueError('잘못된 로딩 구간입니다.')
        self.cancel()
        if reuse:
            self.cache.keep_range(first, last)
            for index in list(self.cache.items):
                previous = self.cache_signatures.get(index)
                if previous is not None and self.file_signature(self.sequence.paths[index]) != previous:
                    self.cache.remove(index)
        else:
            self.cache.clear()
        self.cache_signatures = {index: self.cache_signatures[index] for index in self.cache.items
                                 if index in self.cache_signatures}
        self.errors.clear()
        self.error = ''
        self.selection = (first, last)
        self.loaded_count = len(self.cache.items)
        self.required_bytes = self.cache.bytes
        self.order = [index for index in range(first, last+1) if index not in self.cache.items]
        if priority in self.order:
            self.order.remove(priority)
            self.order.insert(0, priority)
        self.cursor = 0
        self.cancel_event = threading.Event()
        if not self.order:
            self.state = 'ready'
            return
        self.plan_base_bytes = self.cache.bytes
        self.plan = self.planner.submit(plan_range, [self.sequence.paths[index] for index in self.order],
                                        self.cancel_event, self.estimator)
        self.state = 'planning'
        if len(self.order) >= 32 and os.environ.get('FLICK_DISABLE_EARLY_FRAME') != '1':
            self.speculative_index = self.order[0]
            job = self.pool.submit(self.decoder, self.sequence.paths[self.speculative_index])
            self.jobs[job] = (self.generation, self.speculative_index)
            self.submitted += 1
            self.cursor = 1

    @property
    def ready(self):
        return self.state == 'ready'

    @property
    def total(self):
        return self.selection[1]-self.selection[0]+1 if self.selection else 0

    def fail(self, message):
        self.cancel()
        self.state = 'error'
        self.error = message

    def set_budget(self, budget):
        self.cancel()
        self.cache.budget = budget
        self.cache.trim()
        self.errors.clear()

    def open(self, sequence):
        self.cancel()
        self.cache.clear()
        CRYPTO_RANK_CACHE.clear()
        self.cache_signatures.clear()
        self.errors.clear()
        self.sequence = sequence
        self.wanted = []
        self.selection = None
        self.state = 'idle'
        self.error = ''

    def request(self, index, loop=True):
        if not self.sequence:
            return
        if self.state == 'ready' and index not in self.cache.items:
            if self.preview_index != index:
                if self.preview_job:
                    self.preview_job.cancel()
                self.preview_frame = None
                self.preview_index = index
                self.preview_job = self.pool.submit(self.decoder, self.sequence.paths[index])
                self.submitted += 1
            self.pump()
            return
        if self.state in ('planning', 'loading', 'ready', 'error'):
            self.pump()
            return
        self.wanted = [index]
        self.pump()

    def pump(self):
        if self.preview_job and self.preview_job.done():
            job, self.preview_job = self.preview_job, None
            if not job.cancelled():
                try:
                    self.preview_frame = job.result()
                except Exception as exc:
                    self.errors[self.preview_index] = str(exc)
        if self.plan and self.plan.done():
            plan, self.plan = self.plan, None
            try:
                sizes = plan.result()
                if sizes is not None:
                    self.required_bytes = self.plan_base_bytes + sum(sizes)
                    if self.required_bytes > self.cache.budget:
                        self.fail(f'선택 구간은 {self.required_bytes/1024**3:.2f} GiB가 필요합니다. '
                                  'RAM 한도를 높이거나 구간을 줄이세요.')
                    else:
                        self.state = 'loading'
                        if self.speculative_index in self.errors:
                            self.fail(f'{self.sequence.paths[self.speculative_index].name}: '
                                      f'{self.errors[self.speculative_index]}')
                        elif self.speculative_frame is not None:
                            index = self.speculative_index
                            frame = self.speculative_frame
                            self.speculative_frame = None
                            if self.cache.bytes + frame.pixels.nbytes > self.cache.budget:
                                self.fail('실제 프레임 용량이 RAM 한도를 넘었습니다. 구간을 줄이세요.')
                            else:
                                self.cache.put(index, frame)
                                self.cache_signatures[index] = self.file_signature(self.sequence.paths[index])
                                self.loaded_count += 1
            except Exception as exc:
                self.fail(f'프레임 정보 읽기 실패: {exc}')
        for job, (generation, index) in list(self.jobs.items()):
            if not job.done():
                continue
            del self.jobs[job]
            if generation != self.generation or job.cancelled():
                continue
            try:
                frame = job.result()
                if self.state == 'loading':
                    if self.cache.bytes + frame.pixels.nbytes > self.cache.budget:
                        self.fail('실제 프레임 용량이 RAM 한도를 넘었습니다. 구간을 줄이세요.')
                        continue
                    self.cache.put(index, frame)
                    self.cache_signatures[index] = self.file_signature(self.sequence.paths[index])
                    self.loaded_count += 1
                    if self.loaded_count == self.total:
                        self.state = 'ready'
                elif self.state == 'planning' and index == self.speculative_index:
                    self.speculative_frame = frame
                elif index in self.wanted:
                    if not self.cache.put(index, frame):
                        self.errors[index] = '한 프레임이 캐시 한도보다 큽니다. RAM 한도를 높이세요.'
                    else:
                        self.cache_signatures[index] = self.file_signature(self.sequence.paths[index])
            except Exception as exc:
                self.errors[index] = str(exc)
                if self.state == 'loading':
                    self.fail(f'{self.sequence.paths[index].name}: {exc}')
        if self.state == 'loading':
            while len(self.jobs) < self.workers and self.cursor < len(self.order):
                index = self.order[self.cursor]
                self.cursor += 1
                job = self.pool.submit(self.decoder, self.sequence.paths[index])
                self.jobs[job] = (self.generation, index)
                self.submitted += 1
            return
        if self.state in ('planning', 'ready', 'error'):
            return
        active = {index for generation, index in self.jobs.values() if generation == self.generation}
        for index in self.wanted:
            if len(self.jobs) >= self.workers:
                break
            if index not in self.cache.items and index not in active and index not in self.errors:
                job = self.pool.submit(self.decoder, self.sequence.paths[index])
                self.jobs[job] = (self.generation, index)
                self.submitted += 1

    def close(self):
        self.cancel()
        self.planner.shutdown(wait=False, cancel_futures=True)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def frame(self, index):
        return (self.cache.get(index) or
                (self.speculative_frame if index == self.speculative_index else None) or
                (self.preview_frame if index == self.preview_index else None))
