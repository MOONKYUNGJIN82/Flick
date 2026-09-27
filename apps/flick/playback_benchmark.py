"""Measure preloaded playback using actual GPU frameSwapped notifications."""
import argparse
import hashlib
import json
import time
import os
import statistics

import OpenEXR

from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication
from OpenGL import GL as gl
from .main import Window, style_app


def main():
    OpenEXR.set_global_thread_count(min(8, max(2, os.cpu_count() or 2)))
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--fps', type=float, default=24)
    parser.add_argument('--mode', choices=['all', 'realtime'], default='all')
    parser.add_argument('--resolution', choices=['auto', 'full', 'half', 'quarter', 'eighth'], default='auto')
    parser.add_argument('--no-proxy', action='store_true')
    parser.add_argument('--profile-gpu', action='store_true',
                        help='Measure CPU time spent submitting texture uploads and draw calls')
    parser.add_argument('--framebuffer-hash', action='store_true')
    args = parser.parse_args()
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication([])
    style_app(app)
    window = Window()
    if args.no_proxy:
        window.proxy_enabled.setChecked(False)
    scale = {'auto': 0, 'full': 1, 'half': 2, 'quarter': 4, 'eighth': 8}[args.resolution]
    window.resolution.setCurrentIndex(window.resolution.findData(scale))
    window.show()
    started = time.perf_counter()
    window.open_path(args.image)
    try:
        while not window.loader.ready:
            app.processEvents()
            if window.loader.state == 'error' or time.perf_counter()-started > 120:
                raise RuntimeError(window.loader.error or 'Preload timeout')
            time.sleep(.001)
        preload = time.perf_counter()-started
        submitted = window.loader.submitted
        framebuffer_hash = None
        if args.framebuffer_hash:
            captured = window.viewer.grabFramebuffer()
            framebuffer_hash = hashlib.sha256(bytes(captured.bits())).hexdigest()
        timings = {'texture_upload': [], 'draw_submit': []}
        full_uploads = []
        originals = {}
        if args.profile_gpu:
            for name, bucket in [('glTexImage2D', 'texture_upload'),
                                 ('glTexSubImage2D', 'texture_upload'),
                                 ('glDrawArrays', 'draw_submit')]:
                original = getattr(gl, name)
                originals[name] = original
                def timed(*call_args, _original=original, _bucket=bucket, **call_kwargs):
                    began = time.perf_counter()
                    result = _original(*call_args, **call_kwargs)
                    timings[_bucket].append((time.perf_counter()-began)*1000)
                    return result
                setattr(gl, name, timed)
        window.fps.setValue(args.fps)
        window.mode.setCurrentIndex(0 if args.mode == 'all' else 1)
        window.toggle()
        if args.profile_gpu:
            window.viewer.presented.connect(lambda _serial, _when: full_uploads.append(window.viewer.upload_ms))
        until = time.perf_counter()+max(1, args.seconds)
        while time.perf_counter() < until:
            app.processEvents()
            time.sleep(.001)
        swaps = list(window.presented)
        report = {'frames': window.loader.total, 'preload_seconds': round(preload, 3),
                  'cache_MiB': round(window.loader.cache.bytes/1024**2, 2),
                  'resolution': args.resolution, 'scale': window.effective_scale,
                  'proxy_hits': window.proxy_store.hits, 'proxy_misses': window.proxy_store.misses,
                  'sum_frame_load_ms': round(sum(frame.milliseconds for frame in window.loader.cache.items.values()), 1),
                  'target_fps': args.fps, 'mode': args.mode,
                  'frame_swapped_fps': round((len(swaps)-1)/(swaps[-1]-swaps[0]), 2) if len(swaps)>1 else 0,
                  'decode_jobs_during_replay': window.loader.submitted-submitted,
                  'skip': window.skipped,
                  'note': 'GPU frameSwapped callbacks, not physical monitor measurements. Synthetic/real input as supplied.'}
        if args.profile_gpu:
            report['cpu_submit_ms'] = {
                name: {'median': round(statistics.median(values), 2),
                       'p95': round(sorted(values)[int(.95*(len(values)-1))], 2),
                       'samples': len(values)}
                for name, values in timings.items() if values}
            if full_uploads:
                report['cpu_submit_ms']['complete_upload'] = {
                    'median': round(statistics.median(full_uploads), 2),
                    'p95': round(sorted(full_uploads)[int(.95*(len(full_uploads)-1))], 2),
                    'samples': len(full_uploads)}
        if framebuffer_hash is not None:
            report['framebuffer_sha256'] = framebuffer_hash
        print(json.dumps(report, indent=2))
    finally:
        for name, original in locals().get('originals', {}).items():
            setattr(gl, name, original)
        window.close()
        app.processEvents()


if __name__ == '__main__':
    main()
