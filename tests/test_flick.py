import tempfile
import json
import os
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import OpenEXR
from PIL import Image

from apps.flick.core import Frame, FrameCache, Loader, Sequence, decode, discover, frame_bytes, crypto_pixels, crypto_preview_color, CryptoRankCache, inspect_exr, pick_crypto, plan_range
from apps.flick.color import load_config, automatic_input, make_descriptors
from apps.flick.proxy import ProxyStore


class FlickTests(unittest.TestCase):
    def test_crypto_rank_cache_is_bounded(self):
        cache = CryptoRankCache(limit=64)
        rank = tuple(np.zeros((2, 2), np.float32) for _ in range(4))
        cache.put('first', (rank,))
        cache.put('second', (rank,))
        self.assertIsNone(cache.get('first'))
        self.assertIsNotNone(cache.get('second'))
        self.assertEqual(cache.bytes, 64)
        cache.clear()
        self.assertEqual(cache.bytes, 0)

    def test_crypto_matte_reuses_preview_planes_and_refreshes_changed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'crypto.exr'
            bits = np.uint32(0x13851a76)
            ids = np.full((8, 8), bits, np.uint32).view(np.float32)
            zero = np.zeros((8, 8), np.float32)
            header = {'cryptomatte/abcdef0/name': 'CryptoObject',
                      'cryptomatte/abcdef0/manifest': json.dumps({'Cube': '13851a76'})}
            def write(coverage):
                channels = {'CryptoObject00.R': ids,
                            'CryptoObject00.G': np.full((8, 8), coverage, np.float32),
                            'CryptoObject00.B': zero, 'CryptoObject00.A': zero}
                OpenEXR.File(header, channels).write(str(path))
            write(.25)
            decode(path, layer='CryptoObject', scale=2)
            with patch('apps.flick.core.OpenEXR.File', side_effect=AssertionError('reopened EXR')):
                cached = decode(path, layer='CryptoObject', selected=int(bits), scale=2)
            self.assertEqual(int(cached.pixels[0, 0, 0]), 64)
            write(.75)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns+1_000_000_000))
            refreshed = decode(path, layer='CryptoObject', selected=int(bits), scale=2)
            self.assertEqual(int(refreshed.pixels[0, 0, 0]), 191)

    def test_long_range_shows_priority_frame_while_planning(self):
        calls = []
        def decode_frame(path):
            calls.append(int(path.name))
            time.sleep(.015)
            return Frame(np.zeros((2, 2, 4), np.uint8), False, 0)
        def slow_estimate(_path):
            time.sleep(.01)
            return 16
        loader = Loader(budget=40*16, workers=2, decoder=decode_frame, estimator=slow_estimate)
        try:
            loader.open(Sequence(tuple(Path(str(i)) for i in range(40)), tuple(range(40))))
            loader.preload(0, 39, priority=7)
            deadline = time.monotonic()+1
            while loader.frame(7) is None and time.monotonic() < deadline:
                loader.pump()
                time.sleep(.002)
            self.assertIsNotNone(loader.frame(7))
            self.assertEqual(loader.state, 'planning')
            self.wait_loader(loader)
            self.assertTrue(loader.ready)
            self.assertEqual(calls.count(7), 1)
            self.assertEqual(loader.loaded_count, 40)
        finally:
            loader.close()

    def test_cancelled_speculative_frame_cannot_enter_new_sequence(self):
        def slow_decode(path):
            time.sleep(.025)
            return Frame(np.full((2, 2, 4), int(path.name), np.uint8), False, 0)
        loader = Loader(budget=40*16, workers=2, decoder=slow_decode,
                        estimator=lambda _path: 16)
        try:
            loader.open(Sequence(tuple(Path(str(i)) for i in range(40)), tuple(range(40))))
            loader.preload(0, 39, priority=7)
            loader.open(Sequence((Path('99'),), (99,)))
            loader.preload(0, 0)
            self.wait_loader(loader)
            self.assertTrue(loader.ready)
            self.assertEqual(int(loader.frame(0).pixels[0, 0, 0]), 99)
        finally:
            loader.close()

    def test_large_exr_parallel_pack_matches_direct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'large_rgba.exr'
            data = np.arange(1024*1024, dtype=np.uint32).reshape(1024, 1024)
            channels = {name: ((data % 3072).astype(np.float16) / (index+1))
                        for index, name in enumerate('RGBA')}
            OpenEXR.File({}, channels).write(str(path))
            with patch.dict(os.environ, {'FLICK_DISABLE_PARALLEL_PACK': '1'}):
                direct = decode(path).pixels
            parallel = decode(path).pixels
            np.testing.assert_array_equal(parallel, direct)

    def test_frame_layout_cache_refreshes_after_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'changing.exr'
            OpenEXR.File({}, {channel: np.zeros((4, 4), np.float16)
                              for channel in 'RGB'}).write(str(path))
            self.assertEqual(frame_bytes(path, scale=2), 2*2*3*2)
            self.assertEqual(frame_bytes(path, scale=4), 1*1*3*2)
            OpenEXR.File({}, {channel: np.zeros((8, 8), np.float16)
                              for channel in 'RGB'}).write(str(path))
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns+1_000_000_000))
            self.assertEqual(frame_bytes(path, scale=2), 4*4*3*2)

    def test_long_range_header_plan_is_bounded_and_complete(self):
        from threading import Event, Lock
        active = 0
        peak = 0
        guard = Lock()
        def slow_estimate(path):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(.01)
            with guard:
                active -= 1
            return int(path.name)
        paths = [Path(str(i)) for i in range(40)]
        self.assertEqual(plan_range(paths, Event(), slow_estimate), list(range(40)))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)

    def test_reduced_exr_keeps_alpha_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'rgba.exr'
            planes = {channel: np.full((8, 8), value, np.float16)
                      for channel, value in [('R', 1), ('G', 2), ('B', 3), ('A', .5)]}
            OpenEXR.File({}, planes).write(str(path))
            for scale in (1, 2, 4):
                frame = decode(path, scale=scale)
                self.assertEqual(frame.pixels.shape[2], 4)
                self.assertEqual(frame_bytes(path, scale=scale), frame.pixels.nbytes)
                self.assertEqual(float(frame.pixels[0, 0, 3]), .5)

    def test_reused_ram_frame_refreshes_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(Path(directory)/f'{i}.png' for i in range(3))
            for path in paths:
                Image.new('RGB', (4, 4), (10, 10, 10)).save(path)
            loader = Loader(budget=3*4*4*4, workers=1)
            try:
                loader.open(Sequence(paths, (0, 1, 2)))
                loader.preload(0, 2)
                self.wait_loader(loader)
                before = loader.submitted
                Image.new('RGB', (4, 4), (200, 10, 10)).save(paths[1])
                stat = paths[1].stat()
                os.utime(paths[1], ns=(stat.st_atime_ns, stat.st_mtime_ns+1_000_000_000))
                loader.preload(0, 2, reuse=True)
                self.wait_loader(loader)
                self.assertEqual(loader.submitted, before+1)
                self.assertEqual(int(loader.cache.get(1).pixels[0, 0, 0]), 200)
            finally:
                loader.close()

    def test_shifted_work_area_reuses_overlapping_ram_frames(self):
        calls = []
        def fake(path):
            calls.append(int(path.name))
            return Frame(np.zeros((2, 2, 4), np.uint8), False, 0)
        loader = Loader(budget=80, workers=1, decoder=fake, estimator=lambda p: 16)
        try:
            loader.open(Sequence(tuple(Path(str(i)) for i in range(7)), tuple(range(7))))
            loader.preload(0, 4)
            self.wait_loader(loader)
            loader.preload(2, 6, reuse=True)
            self.wait_loader(loader)
            self.assertEqual(calls, [0, 1, 2, 3, 4, 5, 6])
            self.assertEqual(set(loader.cache.items), set(range(2, 7)))
            self.assertEqual(loader.required_bytes, 80)
            loader.preload(2, 6, reuse=True)
            self.assertTrue(loader.ready)
            self.assertEqual(len(calls), 7)
        finally:
            loader.close()

    def test_disk_proxy_preserves_hdr_and_invalidates_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'image.exr'
            plane = np.full((16, 16), 4.5, np.float16)
            OpenEXR.File({}, {c: plane for c in 'RGB'}).write(str(source))
            store = ProxyStore(root/'proxy', limit=1024**2)
            first = store.decode(source, scale=4)
            store.flush()
            second = store.decode(source, scale=4)
            self.assertEqual((store.misses, store.hits), (1, 1))
            self.assertEqual(store.estimate(source, scale=4), second.pixels.nbytes)
            self.assertTrue(second.linear)
            np.testing.assert_array_equal(first.pixels, second.pixels)
            self.assertEqual(float(second.pixels[0, 0, 0]), 4.5)
            plane[:] = 2.0
            OpenEXR.File({}, {c: plane for c in 'RGB'}).write(str(source))
            os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns+1_000_000_000))
            third = store.decode(source, scale=4)
            self.assertEqual(store.misses, 2)
            self.assertEqual(float(third.pixels[0, 0, 0]), 2.0)
            store.flush()
            store.close()

    def test_half_quarter_resolution_and_memory_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'image.exr'
            pixels = np.arange(35, dtype=np.float16).reshape(5, 7)
            OpenEXR.File({}, {c: pixels for c in 'RGB'}).write(str(path))
            for scale, shape in [(1, (5, 7, 4)), (2, (3, 4, 3)), (4, (2, 2, 3))]:
                frame = decode(path, scale=scale)
                self.assertEqual(frame.pixels.shape, shape)
                self.assertEqual(frame_bytes(path, scale=scale), frame.pixels.nbytes)
                self.assertEqual(float(frame.pixels[1, 1, 0]), float(pixels[scale, scale]))

    def test_blender_style_cryptomatte_layer_and_click_pick(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'blender.exr'
            bits = np.uint32(0x13851a76)
            ids = np.full((2, 2), bits, np.uint32).view(np.float32)
            coverage = np.full((2, 2), .75, np.float32)
            zero = np.zeros((2, 2), np.float32)
            header = {'cryptomatte/abcdef0/name': 'CryptoObject',
                      'cryptomatte/abcdef0/manifest': json.dumps({'Cube': '13851a76'})}
            channels = {'R': zero, 'G': zero, 'B': zero,
                        'ViewLayer.CryptoObject00.R': ids,
                        'ViewLayer.CryptoObject00.G': coverage,
                        'ViewLayer.CryptoObject00.B': zero,
                        'ViewLayer.CryptoObject00.A': zero}
            OpenEXR.File(header, channels).write(str(path))
            beauty, layers = inspect_exr(path)
            self.assertTrue(beauty)
            self.assertIn('CryptoObject', layers)
            self.assertEqual(pick_crypto(path, 'CryptoObject', 0, 0), 'Cube')
            preview = decode(path, layer='CryptoObject')
            self.assertEqual(tuple(int(v) for v in preview.pixels[0, 0, :3]),
                             crypto_preview_color(int(bits)))
            matte = decode(path, layer='CryptoObject', selected=int(bits), scale=2)
            self.assertEqual(matte.pixels.shape, (1, 1, 4))
            self.assertEqual(int(matte.pixels[0, 0, 0]), 191)

    def test_preload_priority_displays_current_frame_first(self):
        calls = []
        def fake(path):
            calls.append(int(path.name))
            return Frame(np.zeros((2, 2, 4), np.uint8), False, 0)
        loader = Loader(budget=80, workers=1, decoder=fake, estimator=lambda p: 16)
        try:
            loader.open(Sequence(tuple(Path(str(i)) for i in range(5)), tuple(range(5))))
            loader.preload(0, 4, priority=3)
            self.wait_loader(loader)
            self.assertEqual(calls, [3, 0, 1, 2, 4])
        finally:
            loader.close()

    def test_cryptomatte_coverage_uses_bitcast_id_and_all_ranks(self):
        target = np.uint32(0x13851a76)
        ids = np.full((1, 2), target, dtype=np.uint32).view(np.float32)
        other = np.zeros((1, 2), dtype=np.float32)
        class Plane:
            def __init__(self, pixels):
                self.pixels = pixels
        channels = {}
        for rank, coverage in [(0, .25), (1, .5)]:
            root = f'CryptoObject{rank:02d}.'
            for suffix, pixels in [('R', ids), ('G', np.full((1, 2), coverage, np.float32)),
                                   ('B', other), ('A', other)]:
                channels[root+suffix] = Plane(pixels)
        matte = crypto_pixels(channels, 'CryptoObject', int(target))
        self.assertEqual(matte[0, 0, 0], 191)
        self.assertEqual(matte.shape, (1, 2, 4))

    def test_sequence_isolated_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['shot.0001.png', 'shot.0003.png', 'other.0002.png', 'shot.0002.jpg', 'shot.04.png']:
                (root/name).touch()
            sequence = discover(root/'shot.0003.png')
            self.assertEqual(sequence.numbers, (1, 3))
            self.assertEqual(sequence.missing, 1)

    def test_formats_and_hdr_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for extension in ['png', 'jpg', 'tga']:
                path = root/f'image.{extension}'
                Image.new('RGB', (64, 32), (30, 90, 150)).save(path)
                frame = decode(path)
                self.assertEqual(frame.pixels.shape, (32, 64, 4))
                self.assertLess(abs(int(frame.pixels[0, 0, 1])-90), 3)
                self.assertFalse(frame.linear)
                reduced = decode(path, scale=4)
                self.assertEqual(reduced.pixels.shape, (8, 16, 3))
                self.assertEqual(frame_bytes(path, scale=4), reduced.pixels.nbytes)
                self.assertLess(abs(int(reduced.pixels[0, 0, 1])-90), 3)
            path = root/'image.exr'
            pixels = np.full((32, 64), 4.5, dtype=np.float16)
            OpenEXR.File({}, {c: pixels for c in 'RGB'}).write(str(path))
            frame = decode(path)
            self.assertEqual(frame.pixels.shape, (32, 64, 4))
            self.assertEqual(frame.pixels.dtype, np.float16)
            self.assertEqual(float(frame.pixels[0, 0, 0]), 4.5)
            self.assertTrue(frame.linear)
            self.assertEqual(frame_bytes(path), frame.pixels.nbytes)

    def test_reduced_rgba_png_preserves_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'alpha.png'
            Image.new('RGBA', (32, 16), (10, 20, 30, 100)).save(path)
            frame = decode(path, scale=4)
            self.assertEqual(frame.pixels.shape, (4, 8, 4))
            self.assertEqual(int(frame.pixels[0, 0, 3]), 100)
            self.assertEqual(frame_bytes(path, scale=4), frame.pixels.nbytes)

    def wait_loader(self, loader):
        deadline = time.monotonic()+3
        while loader.state in ('planning', 'loading') and time.monotonic() < deadline:
            loader.pump()
            time.sleep(.002)
        self.assertNotIn(loader.state, ('planning', 'loading'))

    def test_file_number_bounds_with_gaps(self):
        sequence = Sequence(tuple(Path(str(n)) for n in [1001, 1003, 1007]), (1001, 1003, 1007))
        self.assertEqual(sequence.bounds(1002, 1006), (1, 1))
        with self.assertRaises(ValueError):
            sequence.bounds(1004, 1006)
        with self.assertRaises(ValueError):
            sequence.bounds(1007, 1001)

    def test_full_preload_replay_never_decodes_again(self):
        calls = []
        def fake(path):
            calls.append(int(path.name))
            return Frame(np.zeros((2, 2, 4), np.uint8), False, 0)
        loader = Loader(budget=48, decoder=fake, estimator=lambda p: 16)
        try:
            loader.open(Sequence(tuple(Path(str(i)) for i in range(6)), tuple(range(6))))
            loader.preload(2, 4)
            self.wait_loader(loader)
            self.assertTrue(loader.ready)
            self.assertEqual(sorted(calls), [2, 3, 4])
            for index in [2, 3, 4, 3, 2]*20:
                loader.request(index)
                self.assertIsNotNone(loader.cache.get(index))
            self.assertEqual(len(calls), 3)
            self.assertEqual(loader.cache.bytes, 48)
            self.assertEqual(set(loader.cache.items), {2, 3, 4})
        finally:
            loader.close()

    def test_oversized_preload_fails_before_decoding(self):
        calls = []
        loader = Loader(budget=31, decoder=lambda p: calls.append(p), estimator=lambda p: 16)
        try:
            loader.open(Sequence((Path('1'), Path('2')), (1, 2)))
            loader.preload(0, 1)
            self.wait_loader(loader)
            self.assertEqual(loader.state, 'error')
            self.assertEqual(loader.required_bytes, 32)
            self.assertEqual(calls, [])
            self.assertEqual(loader.cache.bytes, 0)
        finally:
            loader.close()

    def test_range_change_discards_running_preload(self):
        def fake(path):
            time.sleep(.025)
            return Frame(np.zeros((2, 2, 4), np.uint8), False, 0)
        loader = Loader(budget=1024, decoder=fake, estimator=lambda p: 16)
        try:
            loader.open(Sequence(tuple(Path(str(i)) for i in range(6)), tuple(range(6))))
            loader.preload(0, 3)
            time.sleep(.01)
            loader.pump()
            loader.preload(4, 5)
            self.wait_loader(loader)
            self.assertTrue(loader.ready)
            self.assertEqual(set(loader.cache.items), {4, 5})
            loader.set_budget(16)
            self.assertFalse(loader.ready)
            self.assertLessEqual(loader.cache.bytes, 16)
        finally:
            loader.close()

    def test_preload_decode_failure_does_not_report_ready(self):
        def broken(path):
            raise ValueError('bad image')
        loader = Loader(budget=1024, decoder=broken, estimator=lambda p: 16)
        try:
            loader.open(Sequence((Path('1'),), (1,)))
            loader.preload(0, 0)
            self.wait_loader(loader)
            self.assertEqual(loader.state, 'error')
            self.assertIn('bad image', loader.error)
            self.assertFalse(loader.ready)
        finally:
            loader.close()

    def test_ocio_builtin_and_external_config(self):
        config = load_config()
        self.assertEqual(automatic_input(config, True), 'Linear Rec.709 (sRGB)')
        self.assertEqual(automatic_input(config, False), 'sRGB Encoded Rec.709 (sRGB)')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.ocio'
            path.write_text(config.serialize(), encoding='utf-8')
            external = load_config(str(path))
            descriptors = make_descriptors(external, 'ACEScg', 'sRGB - Display', 'ACES 1.0 - SDR Video')
            self.assertIn('flickDisplay', descriptors[1].getShaderText())

    def test_cache_lru_budget(self):
        frame = Frame(np.zeros((2, 2, 4), dtype=np.uint8), False, 0)
        cache = FrameCache(32)
        for index in [0, 1]:
            cache.put(index, frame)
        cache.get(0)
        cache.put(2, frame)
        self.assertEqual(set(cache.items), {0, 2})
        self.assertEqual(cache.bytes, 32)
        self.assertFalse(cache.put(3, Frame(np.zeros((100, 100, 4)), False, 0)))

    def test_old_decode_cannot_pollute_new_sequence(self):
        def slow_decode(path):
            time.sleep(.025)
            return Frame(np.full((2, 2, 4), int(path.name), dtype=np.uint8), False, 0)
        loader = Loader(budget=1024, decoder=slow_decode)
        try:
            loader.open(Sequence((Path('1'),), (1,)))
            loader.request(0)
            loader.open(Sequence((Path('2'),), (2,)))
            deadline = time.monotonic()+3
            while time.monotonic() < deadline and loader.cache.get(0) is None:
                loader.request(0)
                self.assertLessEqual(len(loader.jobs), 2)
                time.sleep(.005)
            self.assertEqual(int(loader.cache.get(0).pixels[0, 0, 0]), 2)
        finally:
            loader.close()


if __name__ == '__main__':
    unittest.main()
