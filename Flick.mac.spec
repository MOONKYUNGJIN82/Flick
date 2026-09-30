# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs


a = Analysis(
    ['flick.py'],
    pathex=[],
    binaries=collect_dynamic_libs('PyOpenColorIO'),
    datas=[('FLICK_README.md', '.'), ('flick-release.json', '.'),
           ('assets/flick-icon-v2.png', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Flick',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch='arm64',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Flick-macOS-arm64',
)
app = BUNDLE(
    coll,
    name='Flick.app',
    icon='assets/flick.icns',
    bundle_identifier='media.kallos.flick',
    version='1.0.0',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '12.0',
    },
)
