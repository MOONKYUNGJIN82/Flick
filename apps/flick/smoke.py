"""Generate real fixtures and verify an actual GPU-rendered window."""
from pathlib import Path
import json
import time

import numpy as np
import OpenEXR
from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from .main import Window, style_app


def run():
    root = Path('build/flick-check')
    root.mkdir(parents=True, exist_ok=True)
    for index in range(48):
        im = Image.new('RGB', (960, 540), (18, 32, 48))
        draw = ImageDraw.Draw(im)
        x = 30 + index*15
        draw.rounded_rectangle((x, 170, x+140, 310), radius=25, fill=(80, 225, 170))
        draw.text((40, 40), f'FLICK / FRAME {index+1:04d}', fill=(230, 240, 250), font_size=30)
        im.save(root/f'demo.{index+1:04d}.png')
    hdr = np.zeros((128, 256, 3), np.float16)
    hdr[:, :128, 0] = .18
    hdr[:, 128:, 1] = 2
    OpenEXR.File({}, {'RGB': hdr}).write(str(root/'hdr.exr'))
    surface = QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication([])
    style_app(app)
    window = Window()
    window.show()
    window.open_path(root/'demo.0001.png')
    result = {}
    start = time.perf_counter()
    phase = 0

    def check():
        nonlocal phase, start
        try:
            if time.perf_counter()-start > 20:
                raise RuntimeError('Smoke timeout')
            if phase == 0 and window.displayed == 0:
                assert window.viewer.isValid(), 'OpenGL context unavailable'
                assert not window.viewer.error, window.viewer.error
                window.toggle()
                phase = 1
                start = time.perf_counter()
            elif phase == 1 and time.perf_counter()-start > 1.5:
                assert window.displayed > 10, 'Playback did not advance'
                result['playback_frame_after_1_5s'] = window.displayed
                result['cache_bytes'] = window.loader.cache.bytes
                window.grab().save(str(root/'flick-window.png'))
                window.open_path(root/'hdr.exr')
                phase = 2
            elif phase == 2 and window.viewer.frame and window.viewer.frame.linear:
                window.viewer.repaint()
                image = window.viewer.grabFramebuffer()
                left = image.pixelColor(image.width()//4, image.height()//2)
                right = image.pixelColor(3*image.width()//4, image.height()//2)
                assert 100 < left.red() < 140, f'Linear to sRGB: {left.getRgb()}'
                assert right.green() > 245, f'HDR upload: {right.getRgb()}'
                result['hdr_left_pixel'] = left.getRgb()
                result['hdr_right_pixel'] = right.getRgb()
                result['gl_valid'] = window.viewer.isValid()
                print(json.dumps(result))
                timer.stop()
                window.close()
                app.quit()
        except Exception as exc:
            print('FAILED:', repr(exc))
            timer.stop()
            window.close()
            app.exit(1)
    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(50)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(run())
