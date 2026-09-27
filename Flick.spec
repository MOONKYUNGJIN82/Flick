# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs


a = Analysis(
    ['flick.py'],
    pathex=[],
    binaries=collect_dynamic_libs('PyOpenColorIO'),
    datas=[('FLICK_README.md', '.'), ('flick-release.json', '.'), ('assets/flick-icon-v2.png', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt uses the ICU shipped with Windows. A different ICU on PATH (e.g. Poppler)
# has incompatible exports and must not shadow the Windows DLL in this bundle.
a.binaries = [entry for entry in a.binaries if entry[0].lower() != 'icuuc.dll']
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
    upx=True,
    console=False,
    icon='assets/flick-v2.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Flick-1.0.0',
)



