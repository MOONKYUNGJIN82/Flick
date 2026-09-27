"""Integration checks for proxy loading and a Blender-generated Cryptomatte EXR."""
import os
from pathlib import Path
import time

import numpy as np
import OpenEXR
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from apps.flick.core import decode, inspect_exr, pick_crypto
from apps.flick.main import Window
from apps.flick.proxy import ProxyStore


root = Path(__file__).resolve().parents[2] / 'build' / 'flick-check'
blender = root / 'blender_multilayer.exr'
assert blender.exists(), 'Generate the Blender fixture first.'
beauty, layers = inspect_exr(blender)
assert beauty and {'CryptoObject', 'CryptoMaterial'} <= set(layers)
assert pick_crypto(blender, 'CryptoObject', 48, 32) == 'FlickCube'
assert pick_crypto(blender, 'CryptoMaterial', 48, 32) == 'FlickMaterial'
with OpenEXR.File(str(blender), separate_channels=True) as source:
    beauty_pixels = source.channels(0)
    beauty = decode(blender)
    for axis, channel in enumerate('RGB'):
        np.testing.assert_array_equal(beauty.pixels[..., axis], beauty_pixels[f'Image.{channel}'].pixels)
for scale in (1, 2, 4):
    frame = decode(blender, layer='CryptoObject',
                   selected=layers['CryptoObject'][2]['FlickCube'], scale=scale)
    assert frame.pixels.shape[:2] == ((64+scale-1)//scale, (96+scale-1)//scale)
    assert frame.pixels[..., 0].max() > 0

app = QApplication([])
window = Window()
window.proxy_store = ProxyStore(root / 'proxy-verify')
window.loader.cache.budget = 100*1024**2
window.open_path(root / '4k' / 'bench.0001.exr')
deadline = time.monotonic()+30
while time.monotonic() < deadline and not window.loader.ready:
    app.processEvents()
    window.tick()
    time.sleep(.01)
assert window.loader.ready, window.loader.error
assert window.effective_scale == 4, window.effective_scale
assert window.loader.loaded_count == 12, window.loader.loaded_count
assert window.loader.cache.bytes < window.loader.cache.budget
hits_before = window.proxy_store.hits
jobs_before = window.loader.submitted
window.start_preload()
deadline = time.monotonic()+10
while time.monotonic() < deadline and not window.loader.ready:
    app.processEvents()
    window.tick()
    time.sleep(.01)
assert window.loader.ready, window.loader.error
assert window.proxy_store.hits == hits_before
assert window.loader.submitted == jobs_before
window.in_frame.setValue(window.sequence.numbers[2])
window.out_frame.setValue(window.sequence.numbers[9])
window.start_preload()
assert window.loader.ready
assert window.loader.submitted == jobs_before
window.in_frame.setValue(window.sequence.numbers[3])
window.out_frame.setValue(window.sequence.numbers[10])
window.start_preload()
deadline = time.monotonic()+10
while time.monotonic() < deadline and not window.loader.ready:
    app.processEvents()
    window.tick()
    time.sleep(.01)
assert window.loader.ready, window.loader.error
assert window.loader.submitted == jobs_before+1
print('Auto resolution: Quarter; 12 frames loaded within 100 MiB')
print('RAM cache: same range reused instantly; shifted range decoded one new frame')
print('Blender 5.1 Cryptomatte: Beauty, Object, Material, matte and click-pick passed')
window.close()

window = Window()
window.open_path(blender)
deadline = time.monotonic()+10
while time.monotonic() < deadline and not window.loader.ready:
    app.processEvents()
    window.tick()
    time.sleep(.01)
assert window.loader.ready, window.loader.error
window.pass_combo.setCurrentIndex(window.pass_combo.findText('CryptoObject'))
deadline = time.monotonic()+10
while time.monotonic() < deadline and not window.loader.ready:
    app.processEvents()
    window.tick()
    time.sleep(.01)
assert window.loader.ready, window.loader.error
pick_started = time.perf_counter()
window.pick_at(48, 32)
pick_elapsed_ms = (time.perf_counter()-pick_started)*1000
assert window.pick_job is None, 'Preview click should not reopen the EXR'
deadline = time.monotonic()+10
while time.monotonic() < deadline and window.crypto_item.currentText() != 'FlickCube':
    app.processEvents()
    window.tick()
    time.sleep(.01)
assert window.crypto_item.currentText() == 'FlickCube'
print(f'UI: Cryptomatte click selects FlickCube without reopening EXR ({pick_elapsed_ms:.2f} ms); matte reload passed')
window.close()
