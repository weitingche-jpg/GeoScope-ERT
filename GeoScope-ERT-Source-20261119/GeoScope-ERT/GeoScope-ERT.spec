# -*- mode: python ; coding: utf-8 -*-
r"""GeoScope ERT standalone source build (electric-only public edition).

This spec is meant to sit at the root of the published source package, next to
the release-staged app.py, method_shell.py, method_tabs.py and
survey_workflow.py.  It analyses those sources in place; there is no source
overlay, patching or staging step.

Run from the extracted package root:
    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm GeoScope-ERT.spec

External runtime data (data/, README, licences) is copied next to the built
executable by the corresponding release instructions and is not bundled here.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve()

datas = [(str(ROOT / 'GeoScope-GS.ico'), '.')]
binaries = []
hiddenimports = [
    'method_ert',
    'method_shell',
    'method_tabs',
    'qt_lifecycle',
    'check_auto_array',
    'check_calibration',
    'check_campus',
    'check_coordinate_hints',
    'check_data_tabs',
    'check_dipole_desktop',
    'check_drag_line_acceptance',
    'check_endpoint_edit_acceptance',
    'check_ert_quality',
    'check_history',
    'check_interactions',
    'check_multiline_acceptance',
    'check_navigation',
    'check_observation_import',
    'check_observed_ert',
    'check_point_management',
    'check_project_performance',
    'check_quick_parallel',
    'check_region',
    'check_scalar_legend',
    'check_shortcuts',
    'check_sidebar_layout',
    'check_survey3d',
    'check_windows',
    'check_workflow',
]
for package in ('pygimli', 'pgcore', 'rasterio'):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

excludes = [
    'tkinter',
    'IPython',
    'pytest',
    'jupyter',
    'trame',
    'PyQt5',
    'PyQt6',
    # Gravity / magnetic workbenches, workers and acceptance checks.
    'magnetic_worker',
    'magnetic_compute',
    'magnetic_data',
    'magnetic_workbench',
    'gravity_worker',
    'gravity_workbench',
    'gravity_density_ui',
    'gravity_inversion',
    'check_magnetics',
    'check_gravity_density',
    'check_method_modules',
    # Full-version potential-field stack; the ERT build does not use it.
    'simpeg',
    'harmonica',
    'discretize',
    'geoana',
    'pymatsolver',
    'choclo',
]

a = Analysis(
    [str(ROOT / 'app.py')],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GeoScope',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'GeoScope-GS.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GeoScope-ERT',
)

