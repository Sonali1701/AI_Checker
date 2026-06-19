# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the AI Checker desktop client.
# Build:  pyinstaller AICheckerClient.spec   ->  dist/AICheckerClient.exe

a = Analysis(
    ['run_client.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('Kalam-Regular.ttf', '.')],
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
    # The client never calls an LLM (the proxy does), so keep the heavy / fragile SDKs
    # out of the binary entirely — smaller EXE, no grpc/protobuf bundling headaches.
    excludes=[
        'anthropic', 'google', 'google_genai', 'grpc', 'grpc_status',
        'streamlit', 'matplotlib', 'pandas', 'numpy',
        'tkinter', 'PyQt5', 'PySide2', 'IPython', 'pytest',
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
