"""Arc C native/package scripted verification; not human acceptance or accuracy validation."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def child(work, evidence):
    import time
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from stimulus_harvester import __version__, services, workspace
    from stimulus_harvester.frozen.demo import config, seed, request
    from stimulus_harvester.frozen.store import inventory
    from stimulus_harvester.screening import capabilities as cap, engine
    from stimulus_harvester.gui.window import Window
    from stimulus_harvester.gui.criteria import CriterionDialog

    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    checks=[]
    def passed(name, **data):
        checks.append(dict(name=name,passed=True,**data))
        (evidence/'screening-verification.json').write_text(json.dumps(dict(version=__version__,checks=checks,passed=len(checks),pytest_cases=0,human_acceptance=False,fresh_windows=False),indent=2),encoding='utf-8')
        print('PASS',name,flush=True)

    p=services.create_project(work/'Criteria study 研究',config())
    item=seed(p,'shared',label='accepted')
    old=services.run_stage(p,'freeze-create',request=request())
    assert old.status=='succeeded',old.error
    old_bytes=inventory(Path(old.result['path']))
    def stage(stage_name,**options):
        r=services.run_stage(p.root,stage_name,**options)
        assert r.status=='succeeded',r.error
        return r.result
    def criterion(text,manual=False):
        proposal=cap.propose(text)
        return dict(id='criterion-'+text.replace(' ','-'),text=text,mode='manual' if manual else 'automatic',targets=[] if manual else proposal['targets'],coverage='manual' if manual else 'partial' if proposal['support']=='partial' else 'exact',mapping_id=proposal['mapping_id'],registry_version=proposal['registry_version'],confirmed=True,limitations=proposal['explanation'])

    app=QApplication.instance() or QApplication([])
    w=Window();w.show();w.confirm=lambda *a:True
    def wait():
        until=time.monotonic()+90
        for _ in range(4):
            while w.session.busy:
                app.processEvents();time.sleep(.02)
                assert time.monotonic()<until,'GUI worker timeout'
            app.processEvents();time.sleep(.03)
    wait();w.submit('open',path=str(p.root));wait()
    w.review.reviewer.setText('scripted-investigator')
    w.tabs.setCurrentIndex(2)
    def fill():
        dialog=app.activeModalWidget();assert isinstance(dialog,CriterionDialog)
        dialog.text.setText('people');dialog.confirmed.setChecked(True);dialog.accept_valid()
    QTimer.singleShot(30,fill);w.screen.criteria.add();wait()
    assert w.screen.criteria.items.topLevelItemCount()==1
    assert not services.open_project(p.root).config.screening.detectors
    assert w.screen.model.currentData() is None and not w.screen.low.text()
    assert not w.screen.criteria.run.isEnabled()
    w.screen.criteria.items.setFocus();QTest.keyClick(w.screen.criteria.items,Qt.Key.Key_Tab)
    assert app.focusWidget()
    passed('Native criterion dialog, no defaults, no-ML capability and keyboard interaction')

    w.screen.model.setCurrentIndex(2);w.screen.low.setText('.1');w.screen.high.setText('.5')
    w.screen.save_policy();wait()
    cfg=services.open_project(p.root).config.screening.model_dump(mode='json')
    cfg['criteria']=[criterion('people'),criterion('non-human animals'),criterion('cars'),criterion('televisions'),criterion('fire exits',True)]
    stage('criteria-policy',configuration=cfg,actor='scripted-investigator')
    class SyntheticDetector:
        calls=0
        def __init__(self,settings):
            self.runtime={'fixture':'synthetic evidence; no model inference'}
            known=cap.registry()['models'][settings.model]
            self.identity=dict(model=settings.model,weights='COCO_V1',weights_url=known['url'],artifact_sha256=known['artifact_sha256'],adapter_version='2',torchvision='0.23.0',categories=cap.registry()['categories'])
        def predict(self,path):
            type(self).calls+=1
            return dict(width=40,height=30,detections=[dict(**{'class':name},class_id=cap.labels()[name],score=score,box_xyxy=[1,1,25,25]) for name,score in [('person',.8),('dog',.3),('car',.7),('tv',.02)]])
        def close(self): pass
    original=engine.build_detector
    try:
        engine.build_detector=SyntheticDetector
        stage('screen')
    finally:
        engine.build_detector=original
    r=services.screening_report(p)
    obs=r['observations'][-1]
    assert obs['status']=='recorded' and len(obs['raw']['detections'])==4
    assert [e['observation_status'] for e in r['candidates'][0]['evaluations']]==['detected','uncertain','detected','not_detected','manual_review_required']
    stage('criteria-policy',configuration=cfg,actor='scripted-investigator')
    assert SyntheticDetector.calls==1 and len(services.screening_report(p)['observations'])==1
    w.submit('refresh');wait()
    w.screen.views.setCurrentIndex(2)
    w.screen.browser.select_visible(item['candidate_id']);wait()
    w.inspect(w.screen.inspector,item['candidate_id']);wait()
    inspector=w.screen.inspector;inspector.tabs.setCurrentIndex(2)
    assert inspector.boxes.isVisible() and inspector.image.observation['observation_id']==obs['observation_id']
    inspector.boxes.setChecked(True)
    before=item['path'].read_bytes()
    for mode in ['Light','Dark']:
        w.set_appearance(mode)
        for width,height in [(850,690),(1260,940)]:
            w.resize(width,height);app.processEvents();time.sleep(.1)
            w.grab().save(str(evidence/f'{mode.lower()}-{width}-evidence.png'))
            assert w.width()<=width and w.height()<=height
    assert item['path'].read_bytes()==before
    passed('Explicit mechanism, five criterion states, run-once reuse and labelled display-only outlines')

    w.screen.views.setCurrentIndex(0);w.grab().save(str(evidence/'criteria.png'))
    r=services.screening_report(p)['candidates'][0]
    assert not services.freeze_validate(p,request=request())['valid']
    stage('screen-override',candidate_id=item['candidate_id'],disposition='eligible',reviewer='scripted-investigator',note='Synthetic manual inspection of all recorded criteria',expected_scope=r['scope'])
    freeze=stage('freeze-create',request=request())
    manifest=services.frozen_artifact(p,freeze['artifact_id'])['manifest']
    assert manifest['format_version']==3 and manifest['review_scope']=='project'
    before=inventory(Path(freeze['path']))
    cfg['criteria']=[criterion('televisions')]
    stage('criteria-policy',configuration=cfg,actor='scripted-investigator')
    assembly=stage('assemble',freeze_id=freeze['artifact_id'])
    export=stage('export-frozen',assembly_id=assembly['artifact_id'])
    assert services.verify_frozen_package(export['path'])['valid']
    assert inventory(Path(freeze['path']))==before and inventory(Path(old.result['path']))==old_bytes
    passed('Manual completion remains separate from review; v3 project freeze/export and unchanged v1')

    a=stage('collection',action='create',actor='scripted-investigator',name='Objects',phrases=[])['collection_id']
    stage('collection',action='add',collection_id=a,actor='scripted-investigator',candidate_ids=[item['candidate_id']])
    stage('criteria-policy',configuration=cfg,collection_id=a,actor='scripted-investigator')
    s=workspace.workspace(p.root,a);row=s.candidates[0]
    assert row['label']=='unrated' and row['screening']['evaluations'][0]['observation_id']==obs['observation_id']
    stage('collection',action='review',collection_id=a,actor='scripted-investigator',selection=[dict(candidate_id=item['candidate_id'],scope=row['collection_review']['scope'],event_id=None)],label='accepted')
    f=stage('freeze-create',request=request(scope=dict(mode='collection',collection_id=a,selection_rule='Synthetic collection')))
    m=services.frozen_artifact(p,f['artifact_id'])['manifest']
    assert m['format_version']==3 and m['review_scope']=='collection'
    ass=stage('assemble',freeze_id=f['artifact_id']);exp=stage('export-frozen',assembly_id=ass['artifact_id'])
    assert services.verify_frozen_package(exp['path'])['valid']
    passed('Collection scope reuses evidence without review inheritance and exports explicit v3 scope')
    w.close();app.processEvents()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--child',action='store_true')
    args=parser.parse_args()
    if args.child: return child(args.work,args.evidence)
    env=os.environ.copy();env.update(QT_QPA_PLATFORM='windows',QT_SCALE_FACTOR='1',PYTHONPATH='C:\\nonexistent-poisoned-checkout')
    result=subprocess.run([str(args.runtime),'-I','-B',str(Path(__file__).resolve()),'--child','--work',str(args.work),'--evidence',str(args.evidence)],env=env,capture_output=True,text=True,timeout=600)
    args.evidence.mkdir(parents=True,exist_ok=True)
    (args.evidence/'process.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    print(result.stdout)
    assert result.returncode==0,result.stderr


if __name__=='__main__': main()
