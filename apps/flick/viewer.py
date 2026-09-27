from __future__ import annotations

import ctypes
import os
import numpy as np
import time
import PyOpenColorIO as ocio
from OpenGL import GL as gl
from OpenGL.GL.shaders import compileProgram, compileShader
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from .i18n import translate

VERTEX = '''#version 330 core
out vec2 uv;
uniform vec2 scale;
uniform vec2 pan;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    uv = vec2(p.x, 1.0-p.y);
    gl_Position = vec4((p*2.0-1.0)*scale+pan, 0, 1);
}'''
FRAGMENT = '''#version 330 core
in vec2 uv;
out vec4 color;
uniform sampler2D picture;
uniform float exposure;
uniform int linearInput;
uniform int channel;
uniform int displaySRGB;
uniform int rawInput;
vec3 toLinear(vec3 c) { return mix(c/12.92, pow((c+0.055)/1.055,vec3(2.4)),step(vec3(0.04045),c)); }
vec3 toSRGB(vec3 c) { return mix(c*12.92,1.055*pow(max(c,vec3(0)),vec3(1.0/2.4))-0.055,step(vec3(0.0031308),c)); }
void main() {
    if(any(lessThan(uv,vec2(0))) || any(greaterThan(uv,vec2(1)))) discard;
    vec4 p=texture(picture,uv);
    if(rawInput==1) { color=vec4(p.rgb,1); return; }
    vec3 rgb=linearInput==1 ? p.rgb : toLinear(p.rgb);
    rgb *= exp2(exposure);
    if(channel==1) rgb=vec3(rgb.r);
    if(channel==2) rgb=vec3(rgb.g);
    if(channel==3) rgb=vec3(rgb.b);
    if(displaySRGB==1) rgb=toSRGB(rgb);
    if(channel==4) rgb=vec3(p.a);
    color=vec4(rgb,1);
}'''

OCIO_FRAGMENT = '''#version 330 core
in vec2 uv;
out vec4 color;
uniform sampler2D picture;
uniform float exposure;
uniform int channel;
uniform int rawInput;
// OCIO_CODE
void main() {
    if(any(lessThan(uv,vec2(0))) || any(greaterThan(uv,vec2(1)))) discard;
    vec4 p=texture(picture,uv);
    if(rawInput==1) { color=vec4(p.rgb,1); return; }
    vec4 scene=flickInput(p);
    scene.rgb *= exp2(exposure);
    if(channel==1) scene.rgb=vec3(scene.r);
    if(channel==2) scene.rgb=vec3(scene.g);
    if(channel==3) scene.rgb=vec3(scene.b);
    vec3 rgb=flickDisplay(scene).rgb;
    if(channel==4) rgb=vec3(p.a);
    color=vec4(rgb,1);
}'''


class Viewer(QOpenGLWidget):
    failed = Signal(str)
    presented = Signal(int, float)
    cryptoPicked = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.frame = None
        self.dirty = False
        self.exposure = 0.0
        self.channel = 0
        self.srgb = True
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self.drag = None
        self.program = None
        self.descriptions = None
        self.luts = []
        self.locations = {}
        self.pending = False
        self.serial = 0
        self.painted_serial = 0
        self.swapped_serial = 0
        self.frameSwapped.connect(self.on_swap)
        self.texture_shape = None
        self.pbo_enabled = os.environ.get('FLICK_DISABLE_PBO') != '1'
        self.pbo_handles = []
        self.pbo_cursor = 0
        self.upload_ms = 0.0
        self.error = ''
        self.language = 'ko'
        self.setMinimumSize(400, 260)

    def initializeGL(self):
        try:
            self.build_program(self.descriptions)
            self.vao = gl.glGenVertexArrays(1)
            self.texture = gl.glGenTextures(1)
            if self.pbo_enabled:
                self.pbo_handles = list(gl.glGenBuffers(2))
            gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture)
            for key in (gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER):
                gl.glTexParameteri(gl.GL_TEXTURE_2D, key, gl.GL_LINEAR)
            for key in (gl.GL_TEXTURE_WRAP_S, gl.GL_TEXTURE_WRAP_T):
                gl.glTexParameteri(gl.GL_TEXTURE_2D, key, gl.GL_CLAMP_TO_EDGE)
            self.context().aboutToBeDestroyed.connect(self.cleanup)
        except Exception as exc:
            self.error = str(exc)
            self.failed.emit(self.error)

    def cleanup(self):
        if self.program:
            self.makeCurrent()
            gl.glDeleteTextures([self.texture])
            if self.pbo_handles:
                gl.glDeleteBuffers(len(self.pbo_handles), self.pbo_handles)
                self.pbo_handles = []
            gl.glDeleteVertexArrays(1, [self.vao])
            gl.glDeleteProgram(self.program)
            if self.luts:
                gl.glDeleteTextures([item[0] for item in self.luts])
                self.luts = []
            self.program = None
            self.doneCurrent()

    def on_swap(self):
        if self.painted_serial > self.swapped_serial:
            self.swapped_serial = self.painted_serial
            self.pending = self.painted_serial != self.serial
            self.presented.emit(self.swapped_serial, time.perf_counter())

    def set_frame(self, frame):
        self.frame = frame
        self.dirty = True
        self.serial += 1
        self.pending = True
        self.update()

    def set_ocio(self, descriptions):
        if self.isValid():
            self.makeCurrent()
            try:
                self.build_program(descriptions)
            finally:
                self.doneCurrent()
        self.descriptions = descriptions
        self.update()

    def build_program(self, descriptions):
        fragment = FRAGMENT if descriptions is None else OCIO_FRAGMENT.replace(
            '// OCIO_CODE', '\n'.join(desc.getShaderText() for desc in descriptions))
        # Samplers of differing types are assigned after linking, before validation.
        program = compileProgram(compileShader(VERTEX, gl.GL_VERTEX_SHADER),
                                 compileShader(fragment, gl.GL_FRAGMENT_SHADER), validate=False)
        luts = []
        try:
            resources = []
            for desc in descriptions or []:
                resources.extend((texture, False) for texture in desc.getTextures())
                resources.extend((texture, True) for texture in desc.get3DTextures())
            if len(resources)+1 > gl.glGetIntegerv(gl.GL_MAX_TEXTURE_IMAGE_UNITS):
                raise ValueError('OCIO LUT 수가 GPU 텍스처 슬롯 한도를 넘습니다.')
            gl.glUseProgram(program)
            for unit, (texture, is3d) in enumerate(resources, 1):
                if is3d:
                    target = gl.GL_TEXTURE_3D
                else:
                    target = gl.GL_TEXTURE_1D if texture.dimensions == ocio.GpuShaderCreator.TEXTURE_1D else gl.GL_TEXTURE_2D
                handle = gl.glGenTextures(1)
                luts.append((handle, target, unit))
                gl.glActiveTexture(gl.GL_TEXTURE0+unit)
                gl.glBindTexture(target, handle)
                interpolation = gl.GL_NEAREST if texture.interpolation == ocio.INTERP_NEAREST else gl.GL_LINEAR
                for key in [gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER]:
                    gl.glTexParameteri(target, key, interpolation)
                for key in [gl.GL_TEXTURE_WRAP_S, gl.GL_TEXTURE_WRAP_T, gl.GL_TEXTURE_WRAP_R]:
                    gl.glTexParameteri(target, key, gl.GL_CLAMP_TO_EDGE)
                data = np.ascontiguousarray(texture.getValues(), dtype=np.float32)
                gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
                if is3d:
                    edge = texture.edgeLen
                    gl.glTexImage3D(target, 0, gl.GL_RGB32F, edge, edge, edge, 0, gl.GL_RGB, gl.GL_FLOAT, data)
                else:
                    red = texture.channel == ocio.GpuShaderCreator.TEXTURE_RED_CHANNEL
                    internal, channels = (gl.GL_R32F, gl.GL_RED) if red else (gl.GL_RGB32F, gl.GL_RGB)
                    if target == gl.GL_TEXTURE_1D:
                        gl.glTexImage1D(target, 0, internal, texture.width, 0, channels, gl.GL_FLOAT, data)
                    else:
                        gl.glTexImage2D(target, 0, internal, texture.width, texture.height, 0, channels, gl.GL_FLOAT, data)
                gl.glUniform1i(gl.glGetUniformLocation(program, texture.samplerName), unit)
            gl.glUniform1i(gl.glGetUniformLocation(program, 'picture'), 0)
        except Exception:
            gl.glDeleteProgram(program)
            if luts:
                gl.glDeleteTextures([item[0] for item in luts])
            raise
        finally:
            gl.glUseProgram(0)
            gl.glActiveTexture(gl.GL_TEXTURE0)
        if self.program:
            gl.glDeleteProgram(self.program)
        if self.luts:
            gl.glDeleteTextures([item[0] for item in self.luts])
        self.program, self.luts = program, luts
        self.locations = {name: gl.glGetUniformLocation(program, name) for name in
                          ['scale', 'pan', 'exposure', 'picture', 'linearInput', 'channel', 'displaySRGB', 'rawInput']}

    def fit(self):
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self.update()

    def paintGL(self):
        gl.glClearColor(0.035, 0.044, 0.055, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        if not self.frame or not self.program:
            painter = QPainter(self)
            painter.setPen(QColor('#8d9baa'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             self.error or ('FLICK\n\n'+translate(self.language, '이미지 한 장을 열면 시퀀스를 함께 불러옵니다')+
                                            '\nEXR  ·  JPG  ·  PNG  ·  TGA\n\n'+translate(self.language, 'Ctrl+O  열기     Space  재생')))
            painter.end()
            return
        pixels = self.frame.pixels
        h, w = pixels.shape[:2]
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture)
        if self.dirty:
            upload_started = time.perf_counter()
            components = pixels.shape[2]
            format = gl.GL_RGB if components == 3 else gl.GL_RGBA
            kind = (gl.GL_UNSIGNED_BYTE if pixels.dtype == np.uint8 else
                    gl.GL_HALF_FLOAT if pixels.dtype == np.float16 else gl.GL_FLOAT)
            internal = {np.uint8: gl.GL_RGBA8, np.float16: gl.GL_RGBA16F,
                        np.float32: gl.GL_RGBA32F}[pixels.dtype.type]
            shape = (w, h, internal)
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
            resized = self.texture_shape != shape
            use_pbo = self.pbo_enabled and self.pbo_handles and 8*1024**2 <= pixels.nbytes <= 64*1024**2
            if resized:
                gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, internal, w, h, 0, format, kind,
                                None if use_pbo else pixels)
                self.texture_shape = shape
            if use_pbo:
                handle = self.pbo_handles[self.pbo_cursor]
                self.pbo_cursor = (self.pbo_cursor+1) % len(self.pbo_handles)
                gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, handle)
                try:
                    gl.glBufferData(gl.GL_PIXEL_UNPACK_BUFFER, pixels.nbytes, None, gl.GL_STREAM_DRAW)
                    mapped = gl.glMapBufferRange(gl.GL_PIXEL_UNPACK_BUFFER, 0, pixels.nbytes,
                                                 gl.GL_MAP_WRITE_BIT | gl.GL_MAP_INVALIDATE_BUFFER_BIT)
                    address = ctypes.cast(mapped, ctypes.c_void_p).value
                    if not address:
                        raise RuntimeError('GPU upload buffer mapping failed')
                    ctypes.memmove(address, pixels.ctypes.data, pixels.nbytes)
                    if not gl.glUnmapBuffer(gl.GL_PIXEL_UNPACK_BUFFER):
                        raise RuntimeError('GPU upload buffer became invalid')
                    gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, w, h, format, kind,
                                       ctypes.c_void_p(0))
                except Exception:
                    self.pbo_enabled = False
                    gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, 0)
                    gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, w, h, format, kind, pixels)
                finally:
                    gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, 0)
            elif not resized:
                gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, w, h, format, kind, pixels)
            self.upload_ms = (time.perf_counter()-upload_started)*1000
            self.dirty = False
        ratio = min(self.width()/w, self.height()/h) * self.zoom
        gl.glUseProgram(self.program)
        gl.glUniform2f(self.locations['scale'], w*ratio/self.width(), h*ratio/self.height())
        gl.glUniform2f(self.locations['pan'], *self.pan)
        gl.glUniform1f(self.locations['exposure'], self.exposure)
        gl.glUniform1i(self.locations['rawInput'], int(self.frame.note.startswith('Cryptomatte')))
        for name, value in [('picture', 0), ('linearInput', int(self.frame.linear)),
                            ('channel', self.channel), ('displaySRGB', int(self.srgb))]:
            gl.glUniform1i(self.locations[name], value)
        for handle, target, unit in self.luts:
            gl.glActiveTexture(gl.GL_TEXTURE0+unit)
            gl.glBindTexture(target, handle)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindVertexArray(self.vao)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        gl.glBindVertexArray(0)
        gl.glUseProgram(0)
        self.painted_serial = self.serial

    def wheelEvent(self, event):
        self.zoom = min(32, max(.05, self.zoom * 1.15 ** (event.angleDelta().y()/120)))
        self.update()

    def mousePressEvent(self, event):
        self.drag = event.position()
        self.drag_start = event.position()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            delta = event.position() - self.drag
            self.pan[0] += 2*delta.x()/self.width()
            self.pan[1] -= 2*delta.y()/self.height()
            self.drag = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        if (self.frame and self.frame.note.startswith('Cryptomatte') and
                self.frame.note.endswith('Preview') and
                (event.position()-self.drag_start).manhattanLength() < 4):
            h, w = self.frame.pixels.shape[:2]
            ratio = min(self.width()/w, self.height()/h)*self.zoom
            left = (self.width()-w*ratio)/2 + self.pan[0]*self.width()/2
            top = (self.height()-h*ratio)/2 - self.pan[1]*self.height()/2
            x, y = int((event.position().x()-left)/ratio), int((event.position().y()-top)/ratio)
            if 0 <= x < w and 0 <= y < h:
                self.cryptoPicked.emit(x, y)
        self.drag = None

    def mouseDoubleClickEvent(self, event):
        self.fit()
