"""Integration checks using a real Qt/OpenGL window, including OCIO LUTs."""
import json
import time
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtCore import QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from .core import Frame
from .main import Window, style_app


def run():
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication([])
    style_app(app)
    window = Window()
    window.show()
    output = {}

    def wait_until(predicate, seconds=10):
        deadline = time.perf_counter()+seconds
        while time.perf_counter() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.001)
        raise AssertionError('Timed out')

    def spin(seconds):
        deadline = time.perf_counter()+seconds
        wait_until(lambda: time.perf_counter() >= deadline, seconds+2)

    def center():
        window.viewer.repaint()
        im = window.viewer.grabFramebuffer()
        return np.array(im.pixelColor(im.width()//2, im.height()//2).getRgb()[:3])

    def compare(cpu, sample):
        pixels = np.empty((64, 128, 4), np.float32)
        pixels[..., :3], pixels[..., 3] = sample, 1
        window.viewer.set_frame(Frame(pixels, True, 0))
        spin(.05)
        actual = center()
        expected = np.clip(cpu.applyRGB(list(sample)), 0, 1)*255
        assert np.max(np.abs(actual-expected)) <= 2, (actual, expected)
        return {'gpu': actual.tolist(), 'cpu': expected.round(2).tolist()}

    try:
        window.open_path('build/flick-check/demo.0001.png')
        wait_until(lambda: window.loader.ready and window.displayed >= 0)
        assert window.timeline.minimum() == 0 and window.timeline.maximum() == 47
        window.language.setCurrentIndex(1)
        assert window.load_button.text() == 'Load work area'
        assert window.loop.text() == 'Loop'
        assert window.input_space.itemText(0) == 'Auto (file type)'
        window.fps.setValue(30)
        assert window.fps.value() == 30
        window.toggle()
        spin(.85)
        at_30 = list(window.presented)
        rate_30 = (len(at_30)-1)/(at_30[-1]-at_30[0])
        assert 26 < rate_30 < 34, rate_30
        window.toggle()
        window.fps.setValue(60)
        assert window.fps.value() == 60
        window.toggle()
        spin(.85)
        at_60 = list(window.presented)
        rate_60 = (len(at_60)-1)/(at_60[-1]-at_60[0])
        assert 52 < rate_60 < 68, rate_60
        window.toggle()
        output['fps_30_60'] = [round(rate_30, 2), round(rate_60, 2)]
        window.fps.setValue(24)
        for fps in (24, 30, 60):
            assert window.fps.findData(fps) >= 0
        assert window.fps.count() == 3
        window.language.setCurrentIndex(0)
        assert window.load_button.text() == '구간 읽기'
        timeline = window.timeline
        start_x = timeline._x(timeline.work_start)
        end_x = timeline._x(9)
        for event_type, x, button in [(QEvent.Type.MouseButtonPress, start_x, Qt.MouseButton.LeftButton),
                                      (QEvent.Type.MouseMove, end_x, Qt.MouseButton.NoButton),
                                      (QEvent.Type.MouseButtonRelease, end_x, Qt.MouseButton.LeftButton)]:
            event = QMouseEvent(event_type, QPointF(x, 10), button, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            if event_type == QEvent.Type.MouseButtonPress:
                timeline.mousePressEvent(event)
            elif event_type == QEvent.Type.MouseMove:
                timeline.mouseMoveEvent(event)
            else:
                timeline.mouseReleaseEvent(event)
        assert (window.range_start, window.range_end) == (9, 47)
        assert (window.in_frame.value(), window.out_frame.value()) == (10, 48)
        assert timeline.maximum() == 47, 'Full composition timeline was clipped'
        window.in_frame.setValue(10)
        window.out_frame.setValue(19)
        window.start_preload()
        wait_until(lambda: window.loader.ready)
        wait_until(lambda: window.progress.value() == window.loader.total)
        window.grab().save('build/flick-check/flick-work-area.png')
        assert set(window.loader.cache.items) == set(range(9, 19))
        submitted = window.loader.submitted
        window.mode.setCurrentIndex(1)
        window.toggle()
        spin(2)
        assert 9 <= window.displayed <= 18
        assert window.loader.submitted == submitted, 'Replay scheduled a disk decode'
        swaps = list(window.presented)
        output['cached_replay_fps'] = round((len(swaps)-1)/(swaps[-1]-swaps[0]), 2)
        output['replay_additional_decodes'] = window.loader.submitted-submitted
        window.seek(-10)
        assert window.index == 0
        wait_until(lambda: window.displayed == 0)
        assert window.loader.preview_frame is not None
        window.seek(100)
        assert window.index == 47
        wait_until(lambda: window.displayed == 47)
        assert window.timeline.maximum() == 47
        assert set(window.loader.cache.items) == set(range(9, 19)), 'Scrubbing evicted pinned work area'
        window.seek(9)
        window.loop.setChecked(False)
        window.mode.setCurrentIndex(0)
        window.toggle()
        wait_until(lambda: not window.playing)
        assert window.index == 18 and window.displayed == 18
        output['range_and_nonloop_end'] = 'passed'
        output['full_timeline_work_area_language_fps'] = 'passed'

        window.timer.stop()  # Direct GPU color tests below do not use the timeline.
        window.input_space.setCurrentText('ACEScg')
        window.ocio_view.setCurrentText('ACES 1.0 - SDR Video')
        assert not window.color_error, window.color_error
        processor = window.color_config.getProcessor(ocio.DisplayViewTransform(
            src='ACEScg', display='sRGB - Display', view='ACES 1.0 - SDR Video')).getDefaultCPUProcessor()
        output['aces_gpu_cpu'] = compare(processor, [.18, .3, 2.0])

        one = ocio.Lut1DTransform(length=16)
        for i in range(16):
            value = (i/15)**1.5
            one.setValue(i, value, value, value)
        three = ocio.Lut3DTransform(gridSize=5)
        for r in range(5):
            for g in range(5):
                for b in range(5):
                    three.setValue(r, g, b, (r/4)**2, (g/4)**.8, b/4)
        transform = ocio.GroupTransform([one, three])
        raw = ocio.Config.CreateRaw()
        descriptions = []
        for trans, name, prefix in [(ocio.MatrixTransform(), 'flickInput', 'in_'),
                                    (transform, 'flickDisplay', 'out_')]:
            desc = ocio.GpuShaderDesc.CreateShaderDesc()
            desc.setLanguage(ocio.GPU_LANGUAGE_GLSL_1_3)
            desc.setFunctionName(name)
            desc.setResourcePrefix(prefix)
            raw.getProcessor(trans).getDefaultGPUProcessor().extractGpuShaderInfo(desc)
            descriptions.append(desc)
        window.viewer.set_ocio(descriptions)
        assert len(window.viewer.luts) == 2
        output['lut1d_lut3d_gpu_cpu'] = compare(raw.getProcessor(transform).getDefaultCPUProcessor(), [.18, .3, .7])
        packed = ocio.GpuShaderDesc.CreateShaderDesc()
        packed.setLanguage(ocio.GPU_LANGUAGE_GLSL_1_3)
        packed.setFunctionName('flickDisplay')
        packed.setResourcePrefix('packed_')
        packed.setAllowTexture1D(False)
        packed.setTextureMaxWidth(8)
        raw.getProcessor(transform).getDefaultGPUProcessor().extractGpuShaderInfo(packed)
        assert list(packed.getTextures())[0].dimensions == ocio.GpuShaderCreator.TEXTURE_2D
        window.viewer.set_ocio([descriptions[0], packed])
        output['packed_lut2d_gpu_cpu'] = compare(raw.getProcessor(transform).getDefaultCPUProcessor(), [.18, .3, .7])
        Path('build/flick-check/v2-verification.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
        print(json.dumps(output))
    finally:
        window.close()
        app.processEvents()


if __name__ == '__main__':
    run()
