# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the AI Checker desktop client.
# Build:  pyinstaller AICheckerClient.spec   ->  dist/AICheckerClient.exe

import os
import json

oauth_datas = []
for oauth_candidate in ('pw_oauth_config.json', os.path.join('dist', 'pw_oauth_config.json')):
    if not os.path.exists(oauth_candidate):
        continue
    with open(oauth_candidate, encoding='utf-8') as oauth_handle:
        oauth_config = json.load(oauth_handle)
    # A browser OAuth client id is public. Never let the desktop bundle silently
    # absorb a client_secret or any unrelated credential from a nearby config file.
    if (set(oauth_config) == {'google_client_id'}
            and str(oauth_config.get('google_client_id') or '').strip()):
        oauth_datas = [(oauth_candidate, '.')]
        break
if not oauth_datas:
    raise RuntimeError('A minimal pw_oauth_config.json containing only google_client_id is required')

a = Analysis(
    ['run_client.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('Kalam-Regular.ttf', '.')] + oauth_datas,
    hiddenimports=[
        # uvicorn resolves these dynamically at runtime, so PyInstaller can't see them.
        'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Provider calls use the requests-based pw_access client. Provider SDKs stay out
    # of the binary; credentials and direct provider clients are forbidden.
    excludes=[
        'anthropic', 'google', 'google_genai', 'grpc', 'grpc_status',
        'streamlit', 'matplotlib', 'pandas', 'numpy',
        'tkinter', 'PyQt5', 'PySide2', 'IPython', 'pytest', '_pytest',
        'mypy', 'openpyxl',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AICheckerClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
