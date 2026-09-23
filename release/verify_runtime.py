"""Run using the shipped Python, outside the checkout. Synthetic data only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-project", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = {"python":sys.version, "executable":sys.executable, "sys_path":sys.path,
               "cwd":os.getcwd(), "checks":[], "limitations":["Same Windows host, isolated embedded runtime; not a clean VM.", "Scripted Qt interactions and screenshots; not human or assistive-technology certification."]}

    def passed(name, **evidence):
        results["checks"].append({"name":name,"passed":True,**evidence})
        (args.output / "runtime-verification.json").write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding="utf-8")
        print("PASS",name,flush=True)

    import stimulus_harvester
    from stimulus_harvester import services, workspace
    from importlib.resources import files
    root = Path(sys.executable).resolve().parent.parent
    assert Path(stimulus_harvester.__file__).is_relative_to(root)
    assert all("site-packages" not in p or Path(p).is_relative_to(root) for p in sys.path)
    assert sys.flags.isolated and sys.flags.dont_write_bytecode
    assert shutil.which("git") is None
    passed("isolated runtime without checkout, Git, PYTHONPATH or developer site packages")
    assert stimulus_harvester.__version__ == importlib.metadata.version("stimulus-harvester") == json.loads((root/'release.json').read_text())["version"] == "0.2.0rc5"
    passed("coherent release, runtime and package versions")
    for module in ("torch","torchvision","open_clip","pip"):
        assert importlib.util.find_spec(module) is None, module
    deps = workspace.dependency_states(probe=True)
    assert not any(deps[name]["available"] for name in ("torch","person_detector","OpenCLIP","CUDA","Ollama")), deps
    passed("core lacks ML, pip, CUDA and Ollama service", capabilities=deps)
    resource = files("stimulus_harvester")
    for name in ("resources/harvester.ico","resources/harvester.svg","resources/USER-GUIDE.md","resources/minimal.yaml","review/templates/grid.html","review/static/style.css","review/static/review.js"):
        assert resource.joinpath(name).is_file(), name
    assert (root/'licenses/Python-LICENSE.txt').is_file()
    import imagehash
    from PIL import Image
    assert len(str(imagehash.phash(Image.new('RGB',(32,32),'white'))))==16
    passed("runtime icons, guide, configuration, browser resources and licenses")

    # Block every network connection for ordinary startup and synthetic workflow.
    original_connect = socket.socket.connect
    def no_network(*a, **k):
        raise AssertionError("Unexpected network call during core workflow")
    socket.socket.connect = no_network
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QIcon
    from stimulus_harvester.gui.window import Window
    from stimulus_harvester.gui.demo import exercise
    app = QApplication([])
    app.setWindowIcon(QIcon(str(resource.joinpath("resources/harvester.ico"))))
    window = Window()
    window.show()
    window.confirm = lambda *a: True

    def wait():
        deadline = time.monotonic() + 120
        loop, timer = QEventLoop(), QTimer()
        idle = []
        def check():
            if window.session.busy: idle.clear()
            else: idle.append(True)
            if len(idle) >= 4 or time.monotonic() > deadline: loop.quit()
        timer.timeout.connect(check)
        timer.start(30)
        loop.exec()
        timer.stop()
        assert not window.session.busy, window.operation.text()
    wait()
    assert window.tabs.count() == 6 and app.platformName() == "windows"
    assert not app.windowIcon().isNull()
    passed("native six-stage Qt GUI startup with networking blocked",qt_platform=app.platformName())

    blank = args.output / "empty project with spaces 研究"
    project = services.create_project(blank, workspace.default_configuration("release core"))
    assert not project.config.screening.detectors
    reopened = services.open_project(blank)
    assert reopened.project_id == project.project_id
    window.session.submit("open",path=str(blank)); wait()
    assert window.session.state.project_id == project.project_id
    passed("create/open Unicode and spaces project without detector default",project_id=project.project_id)

    demo = exercise(window,wait,args.output/'synthetic project 研究',args.output)
    assert demo["old_freeze_unchanged"] and demo["original_image_unchanged_by_overlay"]
    demo_id=window.session.state.project_id
    passed("synthetic native candidate/review/freeze/assembly/export/verify workflow",demo=demo)
    window.close(); app.processEvents()
    window = Window(); window.show(); wait()
    window.session.submit("open",path=str(args.output/'synthetic project 研究')); wait()
    assert window.session.state.project_id == demo_id
    passed("close and reopen desktop project")
    for theme in ("Dark","Light"):
        window.project.appearance.setCurrentText(theme); QTest.qWait(100)
        window.grab().save(str(args.output / (theme.lower()+'-desktop.png')))
    window.resize(850,690); QTest.qWait(100)
    window.grab().save(str(args.output/'small-desktop.png'))
    # Keyboard tab traversal has visible focus and can reach actionable controls.
    window.tabs.setCurrentIndex(0)
    focus=[]
    for _ in range(30):
        QTest.keyClick(window,Qt.Key.Key_Tab)
        widget=app.focusWidget()
        if widget: focus.append({"class":type(widget).__name__,"name":widget.accessibleName(),"text":widget.text() if hasattr(widget,'text') else '',"visible":widget.isVisible()})
    assert any(x['class']=='QPushButton' and x['visible'] for x in focus)
    from PySide6.QtWidgets import QWidget
    font=app.font(); font.setPointSizeF(font.pointSizeF()*1.5); app.setFont(font)
    window.setFont(font)
    for widget in window.findChildren(QWidget): widget.setFont(font)
    window.resize(1260,940); QTest.qWait(100)
    window.grab().save(str(args.output/'text-150-percent.png'))
    passed("keyboard focus, dark/light and resize smoke",focus_traversal=focus,
           text_scaling_note="Programmatic font changes are overridden by the 13px stylesheet; use separate QT_SCALE_FACTOR display verification.")
    window.close(); app.processEvents()
    socket.socket.connect = original_connect

    from fastapi.testclient import TestClient
    client=TestClient(services.review_application(project))
    for route in ('/healthz','/images','/static/style.css','/static/review.js'):
        assert client.get(route).status_code == 200
    assert client.get('/static/../LICENSE').status_code == 404
    passed("installed browser resources and traversal rejection")

    config=workspace.default_configuration('provider validation')
    data=config.model_dump(mode='json')
    data['search']['searxng']['enabled']=True
    data['search']['commons']['enabled']=True
    from stimulus_harvester.config import HarvesterConfig
    states=workspace.provider_states(HarvesterConfig.model_validate(data))
    assert states['searxng']['state']=='setup_error'
    assert states['commons']['state']=='setup_error'
    assert states['openverse']['state']=='configured'
    passed("provider missing-configuration diagnostics",providers=states)

    old=args.output/'copied CP6 project 研究'
    shutil.copytree(args.old_project,old)
    before={p.relative_to(old).as_posix():digest(p) for p in old.rglob('*') if p.is_file() and (set(p.parts) & {'freezes','assemblies','frozen_exports'} or p.suffix in ('.png','.jpg'))}
    old_project=services.open_project(old)
    old_state=workspace.workspace(old)
    assert old_state.project_id=='project_6c64d966a24948c78fd56d043e913316'
    artifacts=services.list_frozen_artifacts(old_project)
    for artifact in artifacts:
        services.frozen_artifact(old_project,artifact['artifact_id'])
    after={p.relative_to(old).as_posix():digest(p) for p in old.rglob('*') if p.is_file() and (set(p.parts) & {'freezes','assemblies','frozen_exports'} or p.suffix in ('.png','.jpg'))}
    assert before==after
    replay=services.run_stage(old_project,'assemble',freeze_id='freeze_c02599637f6f478e9b8c71da69c4506d')
    assert replay.status=='succeeded' and replay.result['stimulus_count']==2,replay
    assert all(digest(old/name)==expected for name,expected in before.items())
    passed("pre-packaging CP6 project and immutable artifacts preserved",project_id=old_project.project_id,artifacts=artifacts,unchanged_files=len(before))

    # Read-only install audit after all operations; no bytecode or project data in app.
    manifest=json.loads((root/'payload-sha256.json').read_text())
    for name,expected in manifest.items():
        assert digest(root/name)==expected,name
    unexpected=[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in manifest and p.name!='payload-sha256.json' and not p.name.startswith('unins')]
    assert not unexpected, unexpected[:10]
    passed("installed payload unmodified and no runtime writes into installation")
    results['passed']=len(results['checks'])
    (args.output/'runtime-verification.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')


if __name__ == '__main__':
    main()
