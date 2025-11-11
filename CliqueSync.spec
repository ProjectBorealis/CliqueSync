# -*- mode: python ; coding: utf-8 -*-

options = [
    ('X utf8', None, 'OPTION'),
    ('hash_seed=0', None, 'OPTION'),
    ('OO', None, 'OPTION')
]

a = Analysis(
    ['pbsync\\__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    options,
    name='CliqueSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir='Saved',
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version.rc',
    icon=['resources\\icon.ico'],
)
