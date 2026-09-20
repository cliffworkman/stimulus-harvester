"""Install/portable/uninstall smoke; call from a normal test runner, not the app.

Only creates a fresh, explicit sandbox directory and removes this test installation.
Project evidence is outside the installation. Does not touch other installed apps.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile


def sha(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--sandbox',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--old-project',type=Path,required=True)
    parser.add_argument('--resume-after-runtime',action='store_true',help='Continue a preserved test installation after its 13 runtime checks passed')
    args=parser.parse_args()
    args.sandbox.mkdir(parents=True,exist_ok=args.resume_after_runtime)
    args.evidence.mkdir(parents=True,exist_ok=args.resume_after_runtime)
    install=args.sandbox/'Installed application 研究'
    evidence={'checks':[], 'scope':'Isolated runtime and sanitized process environment on existing Windows host, not a VM.'}
    if args.resume_after_runtime:
        evidence=json.loads((args.evidence/'distribution-verification.json').read_text(encoding='utf-8'))
        assert len(evidence['checks'])==3 and all(x['passed'] for x in evidence['checks'])
    def passed(name,**data):
        evidence['checks'].append({'name':name,'passed':True,**data})
        (args.evidence/'distribution-verification.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        print('PASS',name,flush=True)
    environment=os.environ.copy()
    environment['PATH']=os.environ['WINDIR']+'\\System32;'+os.environ['WINDIR']
    environment['PYTHONPATH']='C:\\nonexistent-poisoned-checkout'
    environment['PYTHONHOME']='C:\\nonexistent-python'
    for key in ('QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QT_QPA_PLATFORM','QML2_IMPORT_PATH'):
        environment.pop(key,None)
    installer=next(args.release.glob('*-setup.exe'))
    def install_app():
        subprocess.run([str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS',f'/DIR={install}',f'/LOG={args.evidence / "installer.log"}'],check=True,timeout=180,cwd=args.sandbox)
        assert (install/'Stimulus Harvester.exe').is_file()
    if not args.resume_after_runtime:
        install_app()
        passed('per-user installer into new Unicode and spaces destination')
    python=install/'runtime/python.exe'
    def run_python(arguments,name):
        with (args.evidence/(name+'.log')).open('w',encoding='utf-8') as log:
            subprocess.run([str(python),'-I','-B']+arguments,env=environment,cwd=args.sandbox,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    for command in ([str(install/'stimulus-harvester.exe'),'--help'],[str(python),'-I','-B','-m','stimulus_harvester','--help']):
        result=subprocess.run(command,cwd=args.sandbox,env=environment,capture_output=True,text=True,timeout=30)
        assert result.returncode==0 and 'freeze' in result.stdout,(result.stdout,result.stderr)
    if not args.resume_after_runtime:
        passed('installed console launcher and module entry point')
        run_python([str(Path(__file__).with_name('verify_runtime.py')),'--output',str(args.evidence/'installed-workflow'),'--old-project',str(args.old_project)],'installed-workflow')
    runtime=json.loads((args.evidence/'installed-workflow/runtime-verification.json').read_text())
    assert runtime['passed']==13
    if not args.resume_after_runtime:
        passed('installed native workflow, noML, resources and historical compatibility',runtime_checks=13)
    # Launch the actual GUI-subsystem launcher and gracefully close its own Python child.
    # Paths are passed via environment variables, never interpolated into shell source.
    ps=r'''
$launcher=Start-Process -FilePath $env:HARVESTER_TEST_EXE -ArgumentList '--project',('"'+$env:HARVESTER_TEST_PROJECT+'"') -PassThru -WindowStyle Hidden
$deadline=(Get-Date).AddSeconds(30)
$child=$null
do {
  $childInfo=Get-CimInstance Win32_Process -Filter ('ParentProcessId = '+$launcher.Id) | Where-Object Name -eq 'pythonw.exe' | Select-Object -First 1
  if ($childInfo) {$child=Get-Process -Id $childInfo.ProcessId -ErrorAction SilentlyContinue}
  if ($child -and $child.MainWindowHandle -ne 0) {break}
  Start-Sleep -Milliseconds 200
} while ((Get-Date) -lt $deadline)
if (-not $child -or $child.MainWindowHandle -eq 0) {throw 'GUI did not create its native window'}
Start-Sleep -Seconds 2
$title=$child.MainWindowTitle
$deadline=(Get-Date).AddSeconds(60)
do {
  if (-not $child.HasExited) {$null=$child.CloseMainWindow()}
  if ($launcher.WaitForExit(500)) {break}
} while ((Get-Date) -lt $deadline)
if (-not $launcher.HasExited) {throw 'GUI launcher did not exit after project loading completed'}
if ($launcher.ExitCode -ne 0) {throw 'GUI launcher failed'}
@{window_title=$title;exit_code=$launcher.ExitCode;child_name=$child.ProcessName}|ConvertTo-Json
'''
    script=args.sandbox/'launch-check.ps1';script.write_text(ps)
    launch_env=environment.copy();launch_env['HARVESTER_TEST_EXE']=str(install/'Stimulus Harvester.exe')
    launch_env['HARVESTER_TEST_PROJECT']=str(args.evidence/'installed-workflow/synthetic project 研究')
    shell=Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],env=launch_env,cwd=args.sandbox,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    passed('actual GUI launcher relaunch and graceful close',details=result.stdout)
    run_python([str(Path(__file__).with_name('verify_network_browser.py')),'--output',str(args.evidence/'network-browser')],'network-browser')
    passed('real loopback browser and bounded live provider check',details=json.loads((args.evidence/'network-browser/network-browser.json').read_text()))
    portable=next(args.release.glob('*-portable.zip'))
    unpack=args.sandbox/'Portable application 研究';unpack.mkdir()
    # Only the just-built, SHA-checked local artifact is extracted; build safety tested separately.
    with zipfile.ZipFile(portable) as archive:archive.extractall(unpack)
    portable_root=next(unpack.iterdir())
    portable_python=portable_root/'runtime/python.exe'
    result=subprocess.run([str(portable_python),'-I','-B','-c',"import stimulus_harvester;from PySide6.QtWidgets import QApplication;from stimulus_harvester.gui.window import Window;a=QApplication([]);w=Window();assert w.tabs.count()==6;assert stimulus_harvester.__version__=='0.2.0rc2';print('portable passed')"],env=environment,cwd=args.sandbox,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr
    passed('portable ZIP unpack and independent native GUI construction',sha256=sha(portable))
    project_root=args.evidence/'installed-workflow'
    before={str(p.relative_to(project_root)):sha(p) for p in project_root.rglob('*') if p.is_file()}
    def uninstall_app():
        assert install.resolve().is_relative_to(args.sandbox.resolve())
        subprocess.run([str(install/'unins000.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],check=True,timeout=120,cwd=args.sandbox)
        for _ in range(60):
            if not (install/'Stimulus Harvester.exe').exists():break
            time.sleep(.1)
        assert not (install/'Stimulus Harvester.exe').exists()
    uninstall_app()
    assert before=={str(p.relative_to(project_root)):sha(p) for p in project_root.rglob('*') if p.is_file()}
    install_app()
    run_python(['-c',"from pathlib import Path;import sys;from stimulus_harvester import services;p=services.open_project(Path(sys.argv[1]));print(p.project_id)",str(project_root/'synthetic project 研究')],'reinstalled-open')
    assert before=={str(p.relative_to(project_root)):sha(p) for p in project_root.rglob('*') if p.is_file()}
    uninstall_app()
    passed('uninstall/reinstall/reopen/uninstall preserves every external project evidence byte',preserved_files=len(before))
    # Remove ONLY this new, resolved portable test folder; never the evidence directory.
    assert unpack.resolve().is_relative_to(args.sandbox.resolve()) and unpack.resolve()!=args.sandbox.resolve()
    shutil.rmtree(unpack)
    assert before=={str(p.relative_to(project_root)):sha(p) for p in project_root.rglob('*') if p.is_file()}
    passed('portable removal preserves external project evidence')
    evidence['passed']=len(evidence['checks'])
    (args.evidence/'distribution-verification.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')


if __name__=='__main__':main()
