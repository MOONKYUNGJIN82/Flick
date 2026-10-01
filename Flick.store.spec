# -*- mode: python ; coding: utf-8 -*-
"""Unsigned/ad-hoc Mac App Store preflight build; distribution signing is separate."""
from PyInstaller.utils.hooks import collect_dynamic_libs
import os


a = Analysis(
    ['flick.py'],
    pathex=[],
    binaries=collect_dynamic_libs('PyOpenColorIO'),
    datas=[('FLICK_README.md', '.'),
           ('packaging/flick-app-store.txt', '.'),
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
    codesign_identity=os.environ.get('FLICK_APPLE_DISTRIBUTION_IDENTITY') or None,
    entitlements_file='packaging/flick-app-store.entitlements',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Flick-store-arm64',
)
app = BUNDLE(
    coll,
    name='Flick.app',
    icon='assets/flick.icns',
    bundle_identifier='media.kallos.flick',
    version='1.0',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '12.0',
        'CFBundleVersion': '2',
        'LSApplicationCategoryType': 'public.app-category.video',
    },
)
