# -*- mode: python ; coding: utf-8 -*-
# PyInstaller — AquaMeasure macOS (.app) avec IA (ultralytics/torch)
# Usage : pyinstaller aquameasure.spec  (depuis la racine du dépôt)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
root = Path(SPECPATH)

ia_datas: list = []
ia_binaries: list = []
ia_hidden: list = []
for pkg in ('ultralytics', 'torch', 'torchvision'):
    try:
        d, b, h = collect_all(pkg)
        ia_datas += d
        ia_binaries += b
        ia_hidden += h
    except Exception:
        pass

a = Analysis(
    [str(root / 'aquameasure.py')],
    pathex=[str(root)],
    binaries=ia_binaries,
    datas=ia_datas,
    hiddenimports=[
        'app_paths',
        'scipy.linalg',
        'cv2',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'fish_detect',
        'fishial_classify',
        'fish_annotate',
        # 'fish_registry' retiré en phase 7 : le module n'existe plus.
        'ultralytics',
        'ultralytics.nn',
        'ultralytics.nn.tasks',
        'ultralytics.utils',
        'torch',
        'torchvision',
    ] + ia_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'open3d',
        'matplotlib',
        'pandas',
        'IPython',
        'notebook',
        'tkinter',
    ],
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
    name='AquaMeasure',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AquaMeasure',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='AquaMeasure.app',
        icon=None,
        bundle_identifier='fr.ird.aquameasure',
        info_plist={
            'CFBundleName': 'AquaMeasure',
            'CFBundleDisplayName': 'AquaMeasure',
            'CFBundleVersion': '1.0.0',
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
            'NSCameraUsageDescription': 'Calibration et mesure stéréo à partir de vidéos.',
        },
    )
