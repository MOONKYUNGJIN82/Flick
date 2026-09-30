from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
import sys
import time
import os
import json
import subprocess

import OpenEXR
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QSurfaceFormat, QPen, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QSlider, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QProgressBar)

from .core import Loader, discover, SUPPORTED, decode, frame_bytes, inspect_exr, pick_crypto, crypto_preview_color
from .proxy import ProxyStore
from .viewer import Viewer
from .color import load_config, automatic_input, make_descriptors
from .i18n import translate, translate_error
from .update import REPOSITORY, latest_release, download_release, update_dir


class Timeline(QSlider):
    workAreaEdited = Signal(int, int)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.cached = ()
        self.work_start = 0
        self.work_end = 0
        self.drag_part = None
        self.drag_origin = 0
        self.drag_bounds = (0, 0)
        self.setMinimumHeight(43)
        self.setToolTip('Drag the green work-area handles to set the RAM/playback range.')

    def set_work_area(self, first, last):
        self.work_start, self.work_end = first, last
        self.update()

    def _x(self, index):
        span = max(1, self.maximum()-self.minimum())
        return 9 + (index-self.minimum())*(self.width()-18)/span

    def _index(self, x):
        span = max(1, self.maximum()-self.minimum())
        return max(self.minimum(), min(self.maximum(), round(self.minimum() + (x-9)*span/max(1, self.width()-18))))

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#7de2ba'))
        span = max(1, self.maximum()-self.minimum()+1)
        for index in self.cached:
            if self.minimum() <= index <= self.maximum():
                painter.drawRect(int((index-self.minimum())*self.width()/span), self.height()-3,
                                 max(1, int(self.width()/span)), 3)
        left, right = self._x(self.work_start), self._x(self.work_end)
        painter.setBrush(QColor('#183b39'))
        painter.drawRoundedRect(8, 2, max(1, self.width()-16), 13, 3, 3)
        painter.setBrush(QColor('#52d7ac'))
        painter.drawRoundedRect(int(left), 2, max(2, int(right-left)), 13, 3, 3)
        painter.setPen(QPen(QColor('#b8ffe1'), 2))
        for x in (left, right):
            painter.drawLine(int(x), 1, int(x), 20)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 20:
            x = event.position().x()
            left, right = self._x(self.work_start), self._x(self.work_end)
            near_left, near_right = abs(x-left) <= 11, abs(x-right) <= 11
            if near_left or near_right:
                self.drag_part = 'start' if near_left and (not near_right or x <= (left+right)/2) else 'end'
            elif left < x < right:
                self.drag_part = 'body'
            else:
                self.drag_part = None
            if self.drag_part:
                self.drag_origin = self._index(x)
                self.drag_bounds = (self.work_start, self.work_end)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_part:
            value = self._index(event.position().x())
            first, last = self.drag_bounds
            if self.drag_part == 'start':
                self.work_start = min(value, self.work_end)
            elif self.drag_part == 'end':
                self.work_end = max(value, self.work_start)
            else:
                shift = max(self.minimum()-first, min(self.maximum()-last, value-self.drag_origin))
                self.work_start, self.work_end = first+shift, last+shift
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_part:
            self.mouseMoveEvent(event)
            self.drag_part = None
            if (self.work_start, self.work_end) != self.drag_bounds:
                self.workAreaEdited.emit(self.work_start, self.work_end)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FPSCombo(QComboBox):
    def __init__(self):
        super().__init__()
        for fps in (24, 30, 60):
            self.addItem(f'{fps} fps', fps)

    def value(self):
        return self.currentData()

    def setValue(self, value):
        index = self.findData(value)
        if index < 0:
            raise ValueError('FPS must be 24, 30, or 60')
        self.setCurrentIndex(index)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Flick 1.0.0')
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self.lang = 'ko'
        self.translatables = []
        self.loader = Loader()
        self.proxy_store = ProxyStore()
        self.scanner = ThreadPoolExecutor(1, thread_name_prefix='flick-scan')
        self.updater = ThreadPoolExecutor(1, thread_name_prefix='flick-update')
        self.update_job = None
        self.update_ready = None
        self.update_manual = False
        self.scan_job = None
        self.pick_job = None
        self.pick_context = None
        self.scan_path = None
        self.sequence = None
        self.crypto_layers = {}
        self.crypto_color_lookup = {}
        self.effective_scale = 1
        self.decode_profile = None
        self.loaded_profile = None
        self.index = 0
        self.displayed = -1
        self.playing = False
        self.play_when_ready = False
        self.range_start, self.range_end = 0, 0
        self.color_config = load_config()
        self.color_error = ''
        self.color_key = None
        self.anchor = time.perf_counter()
        self.anchor_index = 0
        self.next_frame = 0
        self.presented = deque(maxlen=120)
        self.skipped = 0
        self.last_stats = 0
        self.viewer = Viewer()
        self.viewer.presented.connect(self.frame_presented)
        self.viewer.cryptoPicked.connect(self.pick_at)
        self.viewer.failed.connect(lambda message: self.statusBar().showMessage(self.tr('GPU 오류: {error}', error=message)))
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 10)
        top = QHBoxLayout()
        brand = QLabel('FLICK')
        brand.setStyleSheet('font-size: 23px; font-weight: 800; color: #b1f3d8;')
        top.addWidget(brand)
        top.addSpacing(22)
        self.title = self.label('이미지 시퀀스 플레이어')
        top.addWidget(self.title, 1)
        self.language = QComboBox()
        self.language.addItem('한국어', 'ko')
        self.language.addItem('English', 'en')
        self.language.setToolTip('UI language / 화면 언어')
        top.addWidget(self.language)
        self.language.currentIndexChanged.connect(self.change_language)
        self.update_button = self.button('업데이트 확인')
        self.update_button.clicked.connect(lambda: self.check_update(manual=True))
        top.addWidget(self.update_button)
        open_button = self.button('시퀀스 열기')
        open_button.clicked.connect(self.choose)
        top.addWidget(open_button)
        layout.addLayout(top)
        layout.addWidget(self.viewer, 1)
        settings = QHBoxLayout()
        self.exposure = QDoubleSpinBox()
        self.exposure.setRange(-12, 12)
        self.exposure.setSingleStep(.25)
        self.exposure.setSuffix(' EV')
        self.exposure.valueChanged.connect(self.change_view)
        settings.addWidget(self.label('노출'))
        settings.addWidget(self.exposure)
        self.channel = QComboBox()
        self.channel.addItems(['RGB', 'R', 'G', 'B', 'Alpha'])
        self.channel.currentIndexChanged.connect(self.change_view)
        settings.addWidget(self.channel)
        self.display = QComboBox()
        self.display.addItems([self.tr('sRGB 표시'), 'Linear / Raw'])
        self.display.currentIndexChanged.connect(self.change_view)
        settings.addWidget(self.display)
        settings.addWidget(self.label('재생 해상도'))
        self.resolution = QComboBox()
        for label, value in [('Auto', 0), ('Full', 1), ('Half', 2), ('Quarter', 4), ('Eighth', 8)]:
            self.resolution.addItem(label, value)
        self.resolution.currentIndexChanged.connect(self.change_resolution)
        settings.addWidget(self.resolution)
        fit = self.button('화면 맞춤')
        fit.clicked.connect(self.viewer.fit)
        settings.addWidget(fit)
        settings.addStretch()
        settings.addWidget(self.label('RAM 캐시'))
        self.ram = QSpinBox()
        self.ram.setRange(1, 64)
        self.ram.setValue(2)
        self.ram.setSuffix(' GiB')
        self.ram.valueChanged.connect(self.change_budget)
        settings.addWidget(self.ram)
        layout.addLayout(settings)
        crypto = QHBoxLayout()
        crypto.addWidget(self.label('패스'))
        self.pass_combo = QComboBox()
        self.pass_combo.addItem(self.tr('원본 영상'), None)
        self.pass_combo.currentIndexChanged.connect(self.change_crypto)
        crypto.addWidget(self.pass_combo)
        crypto.addWidget(self.label('Cryptomatte 항목'))
        self.crypto_item = QComboBox()
        self.crypto_item.setMinimumWidth(220)
        self.crypto_item.setEditable(True)
        self.crypto_item.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.crypto_item.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.crypto_item.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.crypto_item.currentIndexChanged.connect(self.change_crypto)
        crypto.addWidget(self.crypto_item, 1)
        self.proxy_enabled = QCheckBox(self.tr('디스크 프록시'))
        self.proxy_enabled.setChecked(True)
        self.proxy_enabled.toggled.connect(self.change_resolution)
        crypto.addWidget(self.proxy_enabled)
        layout.addLayout(crypto)
        colors = QHBoxLayout()
        self.ocio_enabled = QCheckBox('OCIO')
        self.ocio_enabled.setChecked(True)
        colors.addWidget(self.ocio_enabled)
        config_button = self.button('설정 파일…')
        config_button.clicked.connect(self.choose_config)
        colors.addWidget(config_button)
        builtin_button = self.button('내장 ACES 1.3')
        builtin_button.clicked.connect(lambda: self.use_config())
        colors.addWidget(builtin_button)
        self.input_space = QComboBox()
        self.ocio_display = QComboBox()
        self.ocio_view = QComboBox()
        for title, combo in [('Input', self.input_space), ('Display', self.ocio_display), ('View', self.ocio_view)]:
            colors.addWidget(self.label(title))
            combo.setMinimumWidth(100)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            colors.addWidget(combo, 1)
        layout.addLayout(colors)
        self.color_status = QLabel('')
        self.color_status.setWordWrap(True)
        layout.addWidget(self.color_status)
        self.populate_colors()
        self.ocio_enabled.toggled.connect(self.apply_color)
        self.input_space.currentIndexChanged.connect(self.apply_color)
        self.ocio_display.currentIndexChanged.connect(self.display_changed)
        self.ocio_view.currentIndexChanged.connect(self.apply_color)
        ranges = QHBoxLayout()
        self.in_frame, self.out_frame = QSpinBox(), QSpinBox()
        for title, field in [('시작 프레임', self.in_frame), ('끝 프레임', self.out_frame)]:
            ranges.addWidget(self.label(title))
            field.setRange(0, 2147483647)
            field.editingFinished.connect(self.apply_range)
            ranges.addWidget(field)
        for title, callback in [('현재 → 시작', self.set_in), ('현재 → 끝', self.set_out), ('전체', self.full_range)]:
            button = self.button(title)
            button.clicked.connect(callback)
            ranges.addWidget(button)
        self.load_button = self.button('구간 읽기')
        self.load_button.clicked.connect(lambda: self.start_preload())
        ranges.addWidget(self.load_button)
        self.cancel_button = self.button('취소')
        self.cancel_button.clicked.connect(self.cancel_preload)
        ranges.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat(self.tr('구간 읽기 대기'))
        ranges.addWidget(self.progress, 1)
        layout.addLayout(ranges)
        self.timeline = Timeline()
        self.timeline.setToolTip(self.tr('작업 구간 손잡이를 드래그해 읽기·재생 범위를 설정하세요.'))
        self.timeline.setRange(0, 0)
        self.timeline.valueChanged.connect(self.seek)
        self.timeline.workAreaEdited.connect(self.timeline_work_area_edited)
        layout.addWidget(self.timeline)
        transport = QHBoxLayout()
        for text, callback in [('⏮', lambda: self.seek(self.range_start)), ('◀', lambda: self.step(-1))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            transport.addWidget(button)
        self.play = QPushButton(self.tr('▶ 재생'))
        self.play.clicked.connect(self.toggle)
        transport.addWidget(self.play)
        forward = QPushButton('▶|')
        forward.clicked.connect(lambda: self.step(1))
        transport.addWidget(forward)
        self.counter = QLabel('— / —')
        transport.addWidget(self.counter, 1)
        self.fps = FPSCombo()
        self.fps.currentIndexChanged.connect(self.reanchor)
        transport.addWidget(self.fps)
        self.mode = QComboBox()
        self.mode.addItems([self.tr('모든 프레임'), self.tr('실시간 우선')])
        self.mode.currentIndexChanged.connect(self.reanchor)
        transport.addWidget(self.mode)
        self.loop = QCheckBox(self.tr('반복'))
        self.loop.setChecked(True)
        transport.addWidget(self.loop)
        layout.addLayout(transport)
        self.stats = QLabel(self.tr('Space 재생/정지   ·   ← → 프레임 이동   ·   휠 확대   ·   드래그 이동   ·   F 화면 맞춤'))
        layout.addWidget(self.stats)
        self.setCentralWidget(root)
        for key, callback in [('Ctrl+O', self.choose), ('Space', self.toggle),
                              ('Left', lambda: self.step(-1)), ('Right', lambda: self.step(1)),
                              ('F', self.viewer.fit), ('Home', lambda: self.seek(self.range_start)),
                              ('I', self.set_in), ('O', self.set_out)]:
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(callback)
            self.addAction(action)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(5)
        QTimer.singleShot(0, self.apply_color)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.poll_update)
        self.update_timer.start(500)
        if REPOSITORY and getattr(sys, 'frozen', False) and not os.environ.get('FLICK_VERIFY_REPORT'):
            QTimer.singleShot(1500, self.check_update)

    def check_update(self, manual=False):
        if self.update_job:
            return
        if not REPOSITORY:
            if manual:
                self.statusBar().showMessage(self.tr('업데이트 저장소가 설정되지 않았습니다.'))
            return
        self.update_manual = manual
        self.update_button.setEnabled(False)
        self.update_button.setText(self.tr('업데이트 확인 중…'))
        def fetch():
            release = latest_release()
            return (release, download_release(release, update_dir()) if release else None)
        self.update_job = self.updater.submit(fetch)

    def poll_update(self):
        if not self.update_job or not self.update_job.done():
            return
        job, self.update_job = self.update_job, None
        self.update_button.setEnabled(True)
        self.update_button.setText(self.tr('업데이트 확인'))
        try:
            release, installer = job.result()
            if installer:
                self.update_ready = installer
                if sys.platform == 'darwin':
                    if self.update_manual:
                        subprocess.Popen(['open', '-R', str(installer)])
                    self.statusBar().showMessage(self.tr('Flick {version} 다운로드 완료 · Finder에서 압축을 풀어 설치하세요.', version=release.version))
                else:
                    self.statusBar().showMessage(self.tr('Flick {version} 준비 완료 · 앱 종료 후 설치합니다.', version=release.version))
            elif self.update_manual:
                self.statusBar().showMessage(self.tr('이미 최신 버전입니다.'))
        except Exception as exc:
            if self.update_manual:
                self.statusBar().showMessage(self.tr('업데이트 확인 실패: {error}', error=str(exc)))

    def tr(self, key, **values):
        return translate(self.lang, key, **values)

    def label(self, key):
        widget = QLabel(self.tr(key))
        self.translatables.append((widget, key))
        return widget

    def button(self, key):
        widget = QPushButton(self.tr(key))
        self.translatables.append((widget, key))
        return widget

    def change_language(self, *_):
        self.lang = self.language.currentData()
        for widget, key in self.translatables:
            widget.setText(self.tr(key))
        self.display.setItemText(0, self.tr('sRGB 표시'))
        self.mode.setItemText(0, self.tr('모든 프레임'))
        self.mode.setItemText(1, self.tr('실시간 우선'))
        if self.pass_combo.count() and self.pass_combo.itemData(0) is None:
            self.pass_combo.setItemText(0, self.tr('원본 영상'))
        if self.crypto_item.count():
            self.crypto_item.setItemText(0, self.tr('컬러 미리보기'))
        self.loop.setText(self.tr('반복'))
        self.proxy_enabled.setText(self.tr('디스크 프록시'))
        self.input_space.setItemText(0, self.tr('자동 (파일 형식)'))
        self.timeline.setToolTip(self.tr('작업 구간 손잡이를 드래그해 읽기·재생 범위를 설정하세요.'))
        self.viewer.language = self.lang
        self.viewer.update()
        self.update_play_text()
        self.apply_color()
        if not self.sequence:
            self.stats.setText(self.tr('Space 재생/정지   ·   ← → 프레임 이동   ·   휠 확대   ·   드래그 이동   ·   F 화면 맞춤'))
            self.progress.setFormat(self.tr('구간 읽기 대기'))
        if not self.color_status.toolTip() or 'ACES 1.3' in self.color_status.toolTip():
            self.color_status.setToolTip(self.tr('내장 ACES 1.3 CG 설정'))
        self.last_stats = 0
        self.statusBar().clearMessage()

    def update_play_text(self):
        key = '로딩 후 재생 예약' if self.play_when_ready else ('Ⅱ 정지' if self.playing else '▶ 재생')
        self.play.setText(self.tr(key))

    def timeline_work_area_edited(self, first, last):
        if not self.sequence:
            return
        self.in_frame.setValue(self.sequence.numbers[first])
        self.out_frame.setValue(self.sequence.numbers[last])
        self.apply_range()

    def populate_colors(self):
        for combo in (self.input_space, self.ocio_display, self.ocio_view):
            combo.blockSignals(True)
            combo.clear()
        self.input_space.addItem(self.tr('자동 (파일 형식)'), '')
        for name in self.color_config.getColorSpaceNames():
            self.input_space.addItem(name, name)
        self.ocio_display.addItems(list(self.color_config.getDisplays()))
        self.ocio_display.setCurrentText(self.color_config.getDefaultDisplay())
        self.ocio_view.addItems(list(self.color_config.getViews(self.ocio_display.currentText())))
        default = 'Un-tone-mapped'
        if self.ocio_view.findText(default) < 0:
            default = self.color_config.getDefaultView(self.ocio_display.currentText())
        self.ocio_view.setCurrentText(default)
        for combo in (self.input_space, self.ocio_display, self.ocio_view):
            combo.blockSignals(False)

    def choose_config(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr('OCIO 설정 열기'), '', 'OCIO (*.ocio *.ocioz)')
        if path:
            self.use_config(path)

    def use_config(self, path=None):
        try:
            config = load_config(path)
        except Exception as exc:
            self.statusBar().showMessage(self.tr('설정 파일을 열지 못했습니다: {error}', error=exc))
            return
        self.color_config = config
        self.color_key = None
        self.populate_colors()
        self.color_status.setToolTip(path or self.tr('내장 ACES 1.3 CG 설정'))
        self.apply_color()

    def display_changed(self, *_):
        previous = self.ocio_view.currentText()
        self.ocio_view.blockSignals(True)
        self.ocio_view.clear()
        display = self.ocio_display.currentText()
        self.ocio_view.addItems(list(self.color_config.getViews(display)))
        self.ocio_view.setCurrentText(previous if self.ocio_view.findText(previous) >= 0 else self.color_config.getDefaultView(display))
        self.ocio_view.blockSignals(False)
        self.apply_color()

    def apply_color(self, *_):
        enabled = self.ocio_enabled.isChecked()
        self.display.setEnabled(not enabled)
        for combo in (self.input_space, self.ocio_display, self.ocio_view):
            combo.setEnabled(enabled)
        try:
            if not enabled:
                self.viewer.set_ocio(None)
                self.color_status.setText(self.tr('OCIO 꺼짐 · 기본 sRGB / Linear 표시'))
                self.color_key = None
            else:
                linear = not self.sequence or self.sequence.paths[self.index].suffix.lower() == '.exr'
                source = self.input_space.currentData() or automatic_input(self.color_config, linear)
                display, view = self.ocio_display.currentText(), self.ocio_view.currentText()
                key = (id(self.color_config), source, display, view)
                if key != self.color_key:
                    self.viewer.set_ocio(make_descriptors(self.color_config, source, display, view))
                    self.color_key = key
                self.color_status.setText(self.tr('GPU OCIO · {source} → {display} / {view}', source=source, display=display, view=view))
            self.color_error = ''
            self.color_status.setStyleSheet('color: #86bda9;')
        except Exception as exc:
            self.color_error = str(exc)
            self.stop_playback()
            self.color_status.setText(self.tr('OCIO 오류 · 이전 변환 유지: {error}',
                                              error=translate_error(self.lang, str(exc))))
            self.color_status.setStyleSheet('color: #ff9d91;')

    def frame_presented(self, serial, timestamp):
        if self.playing:
            self.presented.append(timestamp)

    def stop_playback(self):
        self.playing = self.play_when_ready = False
        if hasattr(self, 'play'):
            self.update_play_text()

    def apply_range(self):
        if not self.sequence:
            return False
        try:
            first, last = self.sequence.bounds(self.in_frame.value(), self.out_frame.value())
        except ValueError as exc:
            self.stop_playback()
            self.statusBar().showMessage(translate_error(self.lang, str(exc)))
            return False
        if (first, last) != (self.range_start, self.range_end):
            self.stop_playback()
            self.loader.cancel()
            self.range_start, self.range_end = first, last
            self.timeline.set_work_area(first, last)
            self.statusBar().showMessage(self.tr('구간 변경 · 구간 읽기 또는 재생을 누르세요.'))
        self.in_frame.setValue(self.sequence.numbers[first])
        self.out_frame.setValue(self.sequence.numbers[last])
        return True

    def set_in(self):
        if self.sequence:
            self.in_frame.setValue(self.sequence.numbers[self.index])
            self.apply_range()

    def set_out(self):
        if self.sequence:
            self.out_frame.setValue(self.sequence.numbers[self.index])
            self.apply_range()

    def full_range(self):
        if self.sequence:
            self.in_frame.setValue(self.sequence.numbers[0])
            self.out_frame.setValue(self.sequence.numbers[-1])
            self.apply_range()

    def start_preload(self, autoplay=False, preserve_position=False):
        if not self.apply_range():
            return
        current = self.index if preserve_position and self.range_start <= self.index <= self.range_end else self.range_start
        self.stop_playback()
        self.play_when_ready = autoplay
        self.index = current
        reuse = self.loaded_profile == self.decode_profile
        self.loader.preload(self.range_start, self.range_end, priority=current, reuse=reuse)
        self.loaded_profile = self.decode_profile
        cached = self.loader.cache.get(current) if reuse else None
        if cached is not None:
            if self.viewer.frame is not cached or self.displayed != current:
                self.viewer.set_frame(cached)
            self.displayed = current
        else:
            self.displayed = -1
            self.viewer.frame = None
            self.viewer.pending = False
            self.viewer.update()
        self.update_play_text()

    def cancel_preload(self):
        self.stop_playback()
        self.loader.cancel()
        self.statusBar().showMessage(self.tr('로딩 취소 · 재생하려면 구간을 다시 읽으세요.'))

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr('시퀀스의 이미지 한 장 선택'), '',
                                             'Images (*.exr *.jpg *.jpeg *.png *.tga)')
        if path:
            self.open_path(path)

    def open_path(self, path):
        self.stop_playback()
        self.loader.cancel()
        self.sequence = None
        self.loaded_profile = None
        self.pick_job = None
        self.effective_scale = self.resolution.currentData() or 1
        if self.scan_job:
            self.scan_job.cancel()
        self.scan_path = Path(path).resolve()
        self.scan_job = self.scanner.submit(discover, path)
        self.statusBar().showMessage(self.tr('시퀀스를 찾는 중…'))

    def configure_crypto(self):
        self.configuring_crypto = True
        self.crypto_layers = {}
        beauty = True
        if self.scan_path.suffix.lower() == '.exr':
            beauty, self.crypto_layers = inspect_exr(self.scan_path)
        self.crypto_color_lookup = {}
        for layer, (_, _, names) in self.crypto_layers.items():
            lookup = {}
            for name, bits in names.items():
                lookup.setdefault(crypto_preview_color(bits), []).append(name)
            self.crypto_color_lookup[layer] = lookup
        self.pass_combo.blockSignals(True)
        self.pass_combo.clear()
        if beauty:
            self.pass_combo.addItem(self.tr('원본 영상'), None)
        for layer in self.crypto_layers:
            self.pass_combo.addItem(layer, layer)
        self.pass_combo.blockSignals(False)
        self.change_crypto()
        self.configuring_crypto = False

    def change_crypto(self, *_):
        layer = self.pass_combo.currentData()
        if layer and layer in self.crypto_layers:
            names = self.crypto_layers[layer][2]
            if self.crypto_item.property('layer') != layer:
                self.crypto_item.blockSignals(True)
                self.crypto_item.clear()
                self.crypto_item.addItem(self.tr('컬러 미리보기'), None)
                for name in sorted(names):
                    self.crypto_item.addItem(name, names[name])
                self.crypto_item.setProperty('layer', layer)
                self.crypto_item.blockSignals(False)
            self.crypto_item.setEnabled(True)
        else:
            self.crypto_item.setEnabled(False)
            self.crypto_item.setProperty('layer', '')
        self.update_decoder()
        if self.sequence and not getattr(self, 'configuring_crypto', False):
            self.start_preload(preserve_position=True)

    def update_decoder(self):
        layer = self.pass_combo.currentData()
        selected = self.crypto_item.currentData() if layer else None
        self.decode_profile = (layer, selected, self.effective_scale)
        hint = self.crypto_layers[layer][:2] if layer in self.crypto_layers else None
        decoder = self.proxy_store.decode if self.proxy_enabled.isChecked() else decode
        self.loader.decoder = partial(decoder, layer=layer,
                                      selected=selected,
                                      scale=self.effective_scale, crypto_hint=hint)
        if self.proxy_enabled.isChecked():
            self.loader.estimator = partial(self.proxy_store.estimate, layer=layer,
                                            selected=selected,
                                            scale=self.effective_scale, crypto_hint=hint)
        else:
            self.loader.estimator = partial(frame_bytes, layer=layer, scale=self.effective_scale,
                                            crypto_hint=hint)

    def change_resolution(self, *_):
        self.effective_scale = self.resolution.currentData() or 1
        self.update_decoder()
        if self.sequence:
            self.start_preload(preserve_position=True)

    def pick_at(self, x, y):
        layer = self.pass_combo.currentData()
        if not self.sequence or not layer:
            return
        frame = self.viewer.frame
        if (frame is not None and self.displayed == self.index and
                frame.note.startswith('Cryptomatte') and frame.note.endswith('Preview') and
                0 <= y < frame.pixels.shape[0] and 0 <= x < frame.pixels.shape[1]):
            color = tuple(int(channel) for channel in frame.pixels[y, x, :3])
            matches = self.crypto_color_lookup.get(layer, {}).get(color, ())
            if len(matches) == 1:
                item = self.crypto_item.findText(matches[0])
                if item >= 0:
                    self.pick_job = None
                    self.crypto_item.setCurrentIndex(item)
                    return
        path = self.sequence.paths[self.index]
        self.pick_context = (path, layer)
        self.pick_job = self.scanner.submit(pick_crypto, path, layer,
                                            x*self.effective_scale, y*self.effective_scale)

    def change_view(self, *_):
        self.viewer.exposure = self.exposure.value()
        self.viewer.channel = self.channel.currentIndex()
        self.viewer.srgb = self.display.currentIndex() == 0
        self.viewer.update()

    def change_budget(self, value):
        self.stop_playback()
        self.loader.set_budget(value * 1024**3)
        self.statusBar().showMessage(self.tr('RAM 한도 변경 · 구간을 다시 읽으세요.'))

    def reanchor(self, *_):
        self.anchor = time.perf_counter()
        self.anchor_index = self.index
        self.next_frame = self.anchor + 1/self.fps.value()

    def toggle(self):
        if not self.sequence:
            return
        if not self.apply_range() or self.color_error:
            return
        if self.play_when_ready:
            self.play_when_ready = False
            self.update_play_text()
            return
        if not self.loader.ready:
            if self.loader.state in ('planning', 'loading'):
                self.play_when_ready = True
                self.update_play_text()
            else:
                self.start_preload(autoplay=True)
            return
        self.playing = not self.playing
        if self.playing and not self.range_start <= self.index < self.range_end:
            self.index = self.range_start
        self.update_play_text()
        self.presented.clear()
        self.skipped = 0
        self.reanchor()

    def step(self, amount):
        self.seek(self.index+amount)

    def seek(self, index):
        if not self.sequence:
            return
        self.stop_playback()
        self.index = max(0, min(len(self.sequence.paths)-1, index))
        self.reanchor()
        self.loader.request(self.index, self.loop.isChecked())

    def tick(self):
        now = time.perf_counter()
        if self.pick_job and self.pick_job.done():
            job, self.pick_job = self.pick_job, None
            try:
                name = job.result()
                if (name and self.sequence and self.sequence.paths[self.index] == self.pick_context[0]
                        and self.pass_combo.currentData() == self.pick_context[1]):
                    item = self.crypto_item.findText(name)
                    if item >= 0:
                        self.crypto_item.setCurrentIndex(item)
            except Exception as exc:
                self.statusBar().showMessage(str(exc))
        if self.scan_job and self.scan_job.done():
            job, self.scan_job = self.scan_job, None
            try:
                self.sequence = job.result()
                self.loader.open(self.sequence)
                self.configure_crypto()
                self.range_start, self.range_end = 0, len(self.sequence.paths)-1
                self.index = 0
                self.in_frame.setValue(self.sequence.numbers[0])
                self.out_frame.setValue(self.sequence.numbers[-1])
                self.displayed = -1
                self.viewer.frame = None
                self.viewer.fit()
                self.timeline.blockSignals(True)
                self.timeline.setRange(0, len(self.sequence.paths)-1)
                self.timeline.set_work_area(0, len(self.sequence.paths)-1)
                self.timeline.blockSignals(False)
                self.title.setText(self.scan_path.name)
                self.setWindowTitle(f'{self.scan_path.name} — Flick 1.0.0')
                self.statusBar().showMessage(self.tr('{count:,} frames · 누락 {missing:,}개 (건너뛰어 재생)',
                                                     count=len(self.sequence.paths), missing=self.sequence.missing))
                self.reanchor()
                self.apply_color()
                self.start_preload()
            except Exception as exc:
                self.statusBar().showMessage(translate_error(self.lang, str(exc)))
        if not self.sequence:
            return
        self.loader.pump()
        if self.loader.ready and self.play_when_ready:
            self.play_when_ready = False
            self.toggle()
        count = self.range_end-self.range_start+1
        if self.playing:
            target = self.index
            if self.mode.currentIndex() == 1:
                target = self.anchor_index + int((now-self.anchor)*self.fps.value())
            elif self.displayed == self.index and not self.viewer.pending and now >= self.next_frame:
                target += 1
            if target > self.range_end and not self.loop.isChecked():
                self.stop_playback()
                target = self.range_end
            self.index = self.range_start + (target-self.range_start) % count
        self.loader.request(self.index, self.loop.isChecked())
        frame = self.loader.frame(self.index)
        if frame is not None and self.index != self.displayed and not self.viewer.pending:
            if self.playing and self.displayed >= 0:
                self.skipped += max(0, (self.index-self.displayed) % count - 1)
            self.viewer.set_frame(frame)
            self.displayed = self.index
            self.next_frame = max(now, self.next_frame + 1/self.fps.value()) if self.playing else now + 1/self.fps.value()
            self.timeline.blockSignals(True)
            self.timeline.setValue(self.index)
            self.timeline.blockSignals(False)
            self.counter.setText(f'{self.sequence.numbers[self.index]}   ·   {self.index+1:,} / {len(self.sequence.paths):,}')
        error = self.loader.errors.get(self.index)
        if error:
            self.stop_playback()
            self.statusBar().showMessage(f'{self.sequence.paths[self.index].name}: {translate_error(self.lang, error)}')
        if now-self.last_stats > .25:
            self.last_stats = now
            if (self.loader.state == 'error' and self.resolution.currentData() == 0
                    and self.effective_scale < 8 and
                    ('GiB가 필요합니다' in self.loader.error or '용량이 RAM 한도를 넘었습니다' in self.loader.error)):
                self.effective_scale *= 2
                self.update_decoder()
                self.start_preload(autoplay=self.play_when_ready, preserve_position=True)
                self.statusBar().showMessage(self.tr('RAM에 맞춰 해상도를 낮췄습니다: 1/{scale}', scale=self.effective_scale))
                return
            self.timeline.cached = tuple(self.loader.cache.items)
            self.timeline.update()
            state = self.loader.state
            busy = state in ('planning', 'loading')
            self.cancel_button.setEnabled(busy)
            self.load_button.setEnabled(not busy)
            if state == 'planning':
                self.progress.setRange(0, 0)
                self.progress.setFormat(self.tr('메모리 계산 중…'))
            else:
                self.progress.setRange(0, max(1, self.loader.total))
                self.progress.setValue(self.loader.loaded_count if state in ('loading', 'ready') else 0)
                self.progress.setFormat(self.tr({'loading': '읽는 중 %v / %m', 'ready': '준비 완료 · %m frames',
                                                 'error': '로딩 실패', 'cancelled': '구간 읽기 대기'}.get(state, '대기')))
            if self.loader.error and state == 'error':
                self.stop_playback()
                self.statusBar().showMessage(translate_error(self.lang, self.loader.error))
            while self.presented and now-self.presented[0] > 2:
                self.presented.popleft()
            rate = (len(self.presented)-1)/(self.presented[-1]-self.presented[0]) if len(self.presented)>1 else 0
            detail = ''
            if self.viewer.frame:
                current = self.viewer.frame
                h, w = current.pixels.shape[:2]
                detail = f'1/{self.effective_scale} · {w} × {h} · {current.pixels.dtype} · {self.tr("디코드")} {current.milliseconds:.1f} ms · '
            waiting = ' · '+self.tr('버퍼링…') if frame is None and not error else ''
            self.stats.setText(detail+self.tr('화면 제출 {rate:.1f} fps · skip {skipped} · RAM {used:.2f}/{limit} GiB · 구간 필요 {required:.2f} GiB{waiting}',
                                                 rate=rate, skipped=self.skipped,
                                                 used=self.loader.cache.bytes/1024**3, limit=self.ram.value(),
                                                 required=self.loader.required_bytes/1024**3, waiting=waiting) +
                               (self.tr(' · 프록시 재사용 {hits}', hits=self.proxy_store.hits)
                                if self.proxy_enabled.isChecked() and self.effective_scale > 1 else ''))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(Path(url.toLocalFile()).suffix.lower() in SUPPORTED for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if Path(url.toLocalFile()).suffix.lower() in SUPPORTED:
                self.open_path(url.toLocalFile())
                break

    def closeEvent(self, event):
        self.timer.stop()
        self.update_timer.stop()
        self.poll_update()
        self.loader.close()
        self.proxy_store.close()
        self.scanner.shutdown(wait=False, cancel_futures=True)
        self.updater.shutdown(wait=False, cancel_futures=True)
        if self.update_ready and self.update_ready.is_file() and sys.platform == 'win32':
            try:
                subprocess.Popen([str(self.update_ready), '/SILENT', '/SUPPRESSMSGBOXES',
                                  '/NORESTART', '/CLOSEAPPLICATIONS'],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError:
                pass
        super().closeEvent(event)


def style_app(app):
    app.setStyle('Fusion')
    app.setStyleSheet('''
        QWidget { background: #161c24; color: #d7e0e9; font-family: "Segoe UI"; font-size: 12px; }
        QPushButton, QComboBox, QSpinBox, QDoubleSpinBox { background: #25303c; border: 1px solid #344252; border-radius: 5px; padding: 6px 10px; }
        QPushButton:hover { background: #344657; }
        QSlider::groove:horizontal { background: #303d4b; height: 6px; border-radius: 3px; }
        QSlider::handle:horizontal { background: #a5efd1; width: 12px; margin: -5px 0; border-radius: 5px; }
        QStatusBar { color: #8d9baa; }
    ''')


def main():
    # Native decompression has its own pool; file jobs are bounded by Loader.
    OpenEXR.set_global_thread_count(min(8, max(2, os.cpu_count() or 2)))
    surface = QSurfaceFormat()
    # macOS exposes Core Profile 3.2 and 4.1, but not 3.3.
    if sys.platform == 'darwin':
        surface.setVersion(4, 1)
    else:
        surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    surface.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication(sys.argv)
    app.setApplicationName('Flick')
    icon_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    app.setWindowIcon(QIcon(str(icon_root / 'assets' / 'flick-icon-v2.png')))
    style_app(app)
    window = Window()
    window.show()
    if len(sys.argv) > 1:
        window.open_path(sys.argv[1])
    # Opt-in packaged-build verification; normal launches do not write reports.
    report = os.environ.get('FLICK_VERIFY_REPORT')
    if report:
        started = time.monotonic()
        check_timer = QTimer(window)
        def verify():
            if (window.viewer.frame is None or not window.loader.ready) and time.monotonic()-started < 20:
                return
            result = {'loaded': window.viewer.frame is not None,
                      'gpu_valid': window.viewer.isValid(), 'gpu_error': window.viewer.error,
                      'preload_ready': window.loader.ready, 'cached_frames': window.loader.loaded_count,
                      'ocio_active': window.viewer.descriptions is not None, 'ocio_error': window.color_error}
            if result['loaded'] and result['gpu_valid']:
                captured = window.viewer.grabFramebuffer()
                result['framebuffer_size'] = [captured.width(), captured.height()]
            Path(report).write_text(json.dumps(result), encoding='utf-8')
            check_timer.stop()
            window.close()
            app.exit(0 if result['loaded'] and result['gpu_valid'] and result['preload_ready']
                     and result['ocio_active'] and not result['gpu_error'] and not result['ocio_error'] else 1)
        check_timer.timeout.connect(verify)
        check_timer.start(100)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
