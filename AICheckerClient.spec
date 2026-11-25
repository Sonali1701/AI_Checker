# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the AI Checker desktop client.
# Build:  pyinstaller AICheckerClient.spec   ->  dist/AICheckerClient.exe
#
# The same spec also builds the integrated-workflow executable, which is the same
# server opened on /workflow.html (entry run_workflow.py):
#   set AICHECKER_EXE_ENTRY=run_workflow.py
#   set AICHECKER_EXE_NAME=AICheckerWorkflow
#   python -m PyInstaller --noconfirm AICheckerClient.spec   ->  dist/AICheckerWorkflow.exe
# and the PW Skills tool for the PW Skills team, the same server showing only /skills.html
# (entry run_skills.py; server.APP_MODE "skills"):
#   set AICHECKER_EXE_ENTRY=run_skills.py
#   set AICHECKER_EXE_NAME=PWSkillsChecker
# One spec, so the bundled modules, data files and excludes can never drift apart.

import os
import json
from PyInstaller.utils.hooks import collect_all

ENTRY = os.environ.get('AICHECKER_EXE_ENTRY', 'run_client.py')
EXE_NAME = os.environ.get('AICHECKER_EXE_NAME', 'AICheckerClient')
if (ENTRY, EXE_NAME) not in (('run_client.py', 'AICheckerClient'), ('run_workflow.py', 'AICheckerWorkflow'),
                             ('run_skills.py', 'PWSkillsChecker')):
    raise RuntimeError(f'Unknown executable {EXE_NAME!r} with entry {ENTRY!r}; see the comment at the top of this spec')

# The portal workflow drives Chrome/Edge through Playwright, whose driver (node.exe
# and its JavaScript package, ~105 MB) is package data that PyInstaller has no hook
# for. Without it the frozen app cannot open the portal window at all.
playwright_datas, playwright_binaries, playwright_hidden = collect_all('playwright')

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
    [ENTRY],
    pathex=[],
    binaries=playwright_binaries,
    datas=[('static', 'static'), ('Kalam-Regular.ttf', '.'), ('assets', 'assets')] + oauth_datas + playwright_datas,
    hiddenimports=[
        # uvicorn resolves these dynamically at runtime, so PyInstaller can't see them.
        'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        # Imported lazily by pdf_renderer.build_evaluated_pdf.
        'teacher_layout', 'wording', 'evidence_repair',
        # Imported lazily by server.py for design and drawing papers.
        'design_grading', 'design_renderer',
        # Imported lazily by pipeline.py (second look at quoted values) and server.py.
        'value_recheck', 'commerce_feedback', 'bulk', 'integrated_workflow', 'portal_vendor.data_filler',
        'portal_vendor.rejection_browser', 'portal_vendor.rejection_comments', 'portal_vendor.match_paper',
        'pdf_page_ranges', 'objective_locations',
        # Teaching keys and the PW Vidyapeeth intake, settings and results sheet.
        'teaching_key', 'vidyapeeth_intake', 'marking_settings', 'result_export',
        # School of Innovation: linked work and other formats, imported lazily by
        # pipeline.grade_sheet; Word files and Power BI models (XPress9) are read there.
        'linked_work', 'work_document', 'docx', 'xpress9',
        # Workflow alerts: silent Windows notification and email (no sound since build 137).
        'workflow_alerts', 'workflow_autopilot',
        # The notice page for a copy whose file cannot be read, and the HEIC reader the
        # download uses for iPhone photos. Both are imported lazily (23 Nov 2026).
        'unreadable_notice', 'pillow_heif',
        'playwright.sync_api', 'pandas', 'openpyxl',
    ] + playwright_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Provider calls use the requests-based pw_access client. Provider SDKs stay out
    # of the binary; credentials and direct provider clients are forbidden.
    excludes=[
        'anthropic', 'google', 'google_genai', 'grpc', 'grpc_status',
        # NumPy is required by the page-ink and collision checks. Excluding it
        # silently disabled those checks in the EXE while source tests passed.
        'streamlit', 'matplotlib',
        'tkinter', 'PyQt5', 'PySide2', 'IPython', 'pytest', '_pytest',
        'mypy',
        # Spreadsheet packages expose optional scientific/ML integrations. The
        # checker uses CSV/Excel I/O and NumPy, without these extra runtimes.
        'torch', 'tensorflow', 'jax', 'jaxlib', 'scipy', 'sklearn',
        'sympy', 'numba',
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
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    # Not Temp: a cleaner emptied it under a running copy on 31 Oct 2026, taking the certificates
    # and fonts, and 111 tests were skipped. run_client removes what killed copies leave here.
    runtime_tmpdir=r'%LOCALAPPDATA%\AIChecker\runtime' if os.name == 'nt' else None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
