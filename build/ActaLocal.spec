# -*- mode: python ; coding: utf-8 -*-
"""Spec de PyInstaller para ActaLocal (onedir, ventana sin consola).

La ruta de ffmpeg.exe se pasa por la variable de entorno FFMPEG_SRC (ver
build.ps1). Se necesita el build 'full' de Gyan porque trae el filtro whisper.
ffprobe.exe NO se empaqueta (pesa ~213 MB): la duracion se mide parseando la
salida de FFmpeg."""

import os

block_cipher = None

binaries = []
ffmpeg_src = os.environ.get("FFMPEG_SRC", "")
if ffmpeg_src and os.path.isfile(ffmpeg_src):
    binaries.append((ffmpeg_src, "."))

icon_path = os.environ.get("APP_ICON", "")
icon_arg = icon_path if (icon_path and os.path.isfile(icon_path)) else None

a = Analysis(
    ['..\\ActaLocal.py'],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=['soundcard', 'soundcard.mediafoundation', '_cffi_backend',
                   'numpy', 'PIL', 'octonove_core.dshow'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['scipy', 'pandas', 'matplotlib', 'PyQt5', 'PyQt6', 'PySide6',
              'pypdf', 'fitz', 'pymupdf'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ActaLocal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ActaLocal',
)
