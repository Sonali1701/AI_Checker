"""Headless, isolated local EXE smoke check; never performs paid grading."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from PyInstaller.archive.readers import CArchiveReader


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    exe=args.exe.resolve(strict=True)
    assert exe.is_relative_to(root/'build'), 'Smoke only staged builds, not a user running copy'
    bundle=CArchiveReader(str(exe))
    # A cleaner emptied the Temp folder under a running copy on 31 Oct 2026.
    assert any(option.startswith('pyi-runtime-tmpdir ') and 'AIChecker' in option for option in bundle.options), 'EXE would unpack into Temp'
    archive=bundle.open_embedded_archive('PYZ.pyz')
    modules=['grader','grading_quality','paper_match','pdf_renderer','design_grading','design_renderer',
             'presentation_layout','pipeline','server','strict_grading','teacher_layout','wording',
             'answer_inventory','answer_map','evidence_repair','objective_locations','key_answers','mathpix',
             'selective_verification','exam_package','costs','language_marks','grading_engine',
             'pdf_page_ranges','integrated_workflow','skills_workflow','skills_portal','pdf_thread_guard','pdf_safety','commerce_feedback','rubrics','remark_quality',
             'preflight','value_recheck','objective_confirm','key_rubric','vidyapeeth_intake','vidyapeeth_fields',
             'result_export','bulk','marking_settings','repeated_pages',
             'portal_vendor.data_filler','portal_vendor.rejection_browser',
             'portal_vendor.rejection_comments','portal_vendor.match_paper',
             'categories','categories.subjects','categories.ca','categories.commerce',
             'categories.school','categories.vidyapeeth','categories.design','categories.base',
             'categories.teaching','categories.innovation','categories.gurukul','teaching_key','typed_sheets',
             'linked_work','work_document','workflow_alerts','workflow_autopilot',
             'workflow_sheet_status','workflow_cleanup','portal_recovery','unreadable_notice']
    # The id the source declares, not a second copy of it that has to be kept in step.
    import re
    build_id=re.search(r'^BUILD_ID\s*=\s*"([^"]+)"',(root/'server.py').read_text(encoding='utf-8-sig'),
                       re.MULTILINE).group(1)
    for name in modules:
        packed=archive.extract(name)
        source=root/(name.replace('.','/')+'.py')
        if not source.exists():
            source=root/name.replace('.','/')/'__init__.py'
        current=compile(source.read_text(encoding='utf-8-sig'),packed.co_filename,'exec',optimize=0)
        assert packed==current, f'Stale bundled module: {name}'
    assert any(name.startswith('numpy') for name in archive.toc)
    assert not any('pdf_diagnostics' in name or 'review_accounting_sample' in name for name in archive.toc)
    # Check the feature's browser assets as well as its Python modules. A fresh
    # backend bundled beside an old UI is otherwise invisible to a health probe.
    static_files=['index.html','app.js','styles.css','workflow.html','workflow.js',
                  'workflow-auth.js','workflow-sheets.js','skills.html','skills.js']
    # PWSkillsChecker.exe is the same server in its PW Skills mode: its own build id, and
    # the checker home and copy workflow pages lead to the PW Skills page.
    skills_exe=exe.stem.lower().startswith('pwskillschecker')
    expected_build=build_id+'-skills' if skills_exe else build_id
    redirected=('index.html','workflow.html') if skills_exe else ()
    for filename in static_files:
        candidates=[name for name in bundle.toc
                    if name.replace('\\','/')=='static/'+filename]
        assert len(candidates)==1, f'Missing bundled UI asset: {filename}'
        assert bundle.extract(candidates[0])==(root/'static'/filename).read_bytes(), \
            f'Stale bundled UI asset: {filename}'
    # Every file under assets/ (cover templates, fonts) must be bundled byte for byte;
    # a template added while a build was running once slipped past the module check.
    bundled={name.replace('\\','/'):name for name in bundle.toc}
    for asset in sorted(p for p in (root/'assets').rglob('*') if p.is_file()):
        key=asset.relative_to(root).as_posix()
        assert key in bundled, f'Missing bundled asset: {key}'
        assert bundle.extract(bundled[key])==asset.read_bytes(), f'Stale bundled asset: {key}'
    oauth_names=[name for name in bundle.toc
                 if name.replace('\\','/')=='pw_oauth_config.json']
    assert len(oauth_names)==1, 'Missing minimal bundled OAuth configuration'
    oauth=json.loads(bundle.extract(oauth_names[0]))
    assert set(oauth)=={'google_client_id'} and str(oauth['google_client_id']).strip(), \
        'Bundled OAuth configuration must contain only its public client ID'
    data=Path(tempfile.mkdtemp(prefix='smoke-',dir=root/'build'))
    isolated_env={**os.environ,'AICHECKER_NO_BROWSER':'1','AICHECKER_DATA_DIR':str(data),
                  'AI_CHECKER_WORKFLOW_DIR':str(data/'workflow_data')}
    # Folder mode grades in worker processes, which are this EXE started again. A
    # missing freeze_support() or an unbundled module only shows up in the EXE.
    engine_run=subprocess.run([str(exe)],cwd=root/'build',capture_output=True,text=True,timeout=300,
                              env={**isolated_env,'AICHECKER_ENGINE_SELFTEST':'1'},
                              creationflags=subprocess.CREATE_NO_WINDOW)
    assert engine_run.returncode==0, f'Frozen grading-engine smoke exited with {engine_run.returncode}'
    engine=json.loads([line for line in engine_run.stdout.splitlines() if line.startswith('{')][-1])
    assert engine['ready']==engine['processes']==2 and engine['strict_code']=='NOT_AN_ANSWER_SHEET' and all(
        engine[k] for k in ('result_ok','worker_pid_differs','stage_forwarded','log_forwarded','package_ok')),engine
    # The portal workflow runs inside the EXE too: the vendored engine, the bundled
    # Playwright driver and a headless browser launch (Chrome, else Edge), offline.
    workflow_run=subprocess.run([str(exe)],cwd=root/'build',capture_output=True,text=True,timeout=300,
                                env={**isolated_env,'AICHECKER_WORKFLOW_SELFTEST':'1'},
                                creationflags=subprocess.CREATE_NO_WINDOW)
    assert workflow_run.returncode==0, f'Frozen workflow smoke exited with {workflow_run.returncode}'
    workflow=json.loads([line for line in workflow_run.stdout.splitlines() if line.startswith('{')][-1])
    assert workflow['modules_ok'] and workflow['driver_ok'] and workflow['browser_launch_ok'], workflow
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
    env={**isolated_env,'AICHECKER_PORT':str(port),'AICHECKER_ENGINE_SELFTEST':'0'}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url=f'http://127.0.0.1:{port}'
    with (data/'process.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(exe)],cwd=data,env=env,stdout=log,stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        (data/'launched-pid.json').write_text(json.dumps({'pid':proc.pid,'exe':str(exe)}),encoding='utf-8')
        try:
            end=time.monotonic()+90
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f'EXE exited early. Log: {data / "process.log"}')
                try:
                    with opener.open(url+'/api/health',timeout=2) as response:
                        health=json.load(response)
                    break
                except OSError:
                    if time.monotonic()>end:
                        raise TimeoutError(f'EXE health timed out. Log: {data / "process.log"}')
                    time.sleep(.5)
            assert health['ok'] and health['build_id']==expected_build, health
            assert health.get('pdf_threads_guarded') is True, 'PDF thread guard is not active in this EXE'
            # AICheckerWorkflow.exe announces (and opens) the workflow page; the
            # checker EXE keeps the home page.
            log_text=(data/'process.log').read_text(encoding='utf-8',errors='replace')
            workflow_exe=exe.stem.lower().startswith('aicheckerworkflow')
            start_page='/workflow.html' if workflow_exe else '/skills.html' if skills_exe else '/'
            if workflow_exe:
                assert f'{url}/workflow.html' in log_text, f'Workflow EXE must open the workflow page. Log: {data / "process.log"}'
            elif skills_exe:
                assert f'{url}/skills.html' in log_text, f'PW Skills EXE must open the PW Skills page. Log: {data / "process.log"}'
            else:
                assert f'{url}/workflow.html' not in log_text, f'Checker EXE must open the home page. Log: {data / "process.log"}'
            for filename in static_files:
                route='/'+filename
                shown=root/'static'/('skills.html' if filename in redirected else filename)
                with opener.open(url+route,timeout=5) as response:
                    assert response.status==200 and response.read()==shown.read_bytes(),route
                    assert 'no-store' in response.headers.get('Cache-Control',''),route
            with opener.open(url+'/',timeout=5) as response:
                assert response.status==200 and response.read()==(root/'static'/('skills.html' if skills_exe else 'index.html')).read_bytes()
            with opener.open(url+'/api/config',timeout=5) as response:
                config=json.load(response)
                assert config['build_id']==expected_build and config['google_client_id']
                assert config.get('app_mode')==('skills' if skills_exe else 'full'), config.get('app_mode')
            with opener.open(url+'/openapi.json',timeout=5) as response:
                schema=json.load(response)
            question_only=['/api/detect-marks','/api/generate-rubric']
            exam_routes=['/api/evaluate','/api/bulk/start','/api/design/read-paper',
                         '/api/evaluate-design','/api/workflow/start']
            checked_forms={}
            for route in question_only+exam_routes:
                body=schema['paths'][route]['post']['requestBody']['content']['multipart/form-data']['schema']
                if '$ref' in body:
                    body=schema['components']['schemas'][body['$ref'].rsplit('/',1)[1]]
                required=set(body.get('required',[]))
                names=['question_page_range'] if route in question_only else [
                    'question_page_range','answer_page_range','use_question_paper_as_key']
                for name in names:
                    field=body['properties'][name]
                    assert name not in required, f'{route}: new field {name} must remain optional'
                    assert field['default']==(False if name=='use_question_paper_as_key' else ''), (route,name,field)
                    assert field['type']==('boolean' if name=='use_question_paper_as_key' else 'string'), (route,name,field)
                checked_forms[route]=names
            result={'health':health,'ui_routes_ok':True,'bundled_modules_match_source':modules,
                    'numpy_bundled':True,'sample_fixture_not_bundled':True,
                    'bundled_static_matches_source':static_files,'minimal_oauth_config_only':True,
                    'optional_page_range_forms':checked_forms,
                    'grading_engine_selftest':engine,
                    'workflow_selftest':workflow,
                    'start_page':start_page,
                    'fresh_provider_evaluation':False,'diagnostics':str(data)}
            (exe.parent/'smoke-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result,indent=2))
        finally:
            if proc.poll() is None:
                # Only the process tree launched above; never kill by image name.
                stopped=subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],check=False,capture_output=True,
                                       text=True,creationflags=subprocess.CREATE_NO_WINDOW)
                if stopped.returncode:
                    raise RuntimeError(f'Test-process cleanup failed for PID {proc.pid}: {stopped.stderr}')
                proc.wait(timeout=15)


if __name__=='__main__':
    main()
