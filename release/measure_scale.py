"""Bounded synthetic scale test, run with the installed embedded interpreter."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time

from stimulus_harvester import services, workspace
from stimulus_harvester.models import SearchResult
from stimulus_harvester.review.db import init_db, upsert_image_from_search


def memory():
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    value=Counters(); value.cb=ctypes.sizeof(value)
    ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
    assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(value),value.cb)
    return {'working_set_MiB':value.WorkingSetSize/1048576,'peak_working_set_MiB':value.PeakWorkingSetSize/1048576}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    project=services.create_project(args.output/'scale project 研究',workspace.default_configuration('synthetic scale'))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop,QTimer,Qt
    from PySide6.QtTest import QTest
    from stimulus_harvester.gui.window import Window
    app=QApplication([]); window=Window(); window.show()
    def wait():
        loop,timer=QEventLoop(),QTimer(); idle=[]
        def check():
            if window.session.busy: idle.clear()
            else: idle.append(True)
            if len(idle)>=4: loop.quit()
        timer.timeout.connect(check); timer.start(30); QTimer.singleShot(180000,loop.quit)
        loop.exec();timer.stop();assert not window.session.busy
    wait(); rows=[]; previous=0
    for count in (1000,5000):
        conn=init_db(project.paths.db_path)
        for i in range(previous,count):
            upsert_image_from_search(conn,SearchResult(local_id=f'scale-{i:06}',source_backend='synthetic-scale',search_query='synthetic architecture',seed_query='synthetic architecture',image_url=f'https://example.invalid/scale/{i}.png',landing_page_url=f'https://example.invalid/scale/{i}',license='synthetic',creator='Synthetic fixture'),project.project_id,commit=False)
        conn.commit();conn.close();previous=count
        start=time.perf_counter(); state=workspace.workspace(project.root); projection=time.perf_counter()-start
        start=time.perf_counter(); window.session.submit('open',path=str(project.root));wait();opened=time.perf_counter()-start
        assert len(state.candidates)==count
        browser=window.review.browser; window.tabs.setCurrentIndex(3)
        start=time.perf_counter(); browser.filter.setText('scale-000099');app.processEvents();filter_time=time.perf_counter()-start
        assert sum(not browser.table.isRowHidden(i) for i in range(count))==1
        browser.filter.clear();browser.table.setFocus();browser.table.selectRow(count-1);wait()
        start=time.perf_counter(); QTest.keyClick(browser.table,Qt.Key.Key_Up);wait();navigate=time.perf_counter()-start
        # Shared review authority on one pending acquisition; no fabricated detector observations.
        local=f'scale-{count-2:06}'
        start=time.perf_counter(); result=services.run_stage(project,'review',local_id=local,label='rejected',reviewer_email='Synthetic scale reviewer',notes='synthetic test'); elapsed=time.perf_counter()-start
        assert result.status=='succeeded',result
        start=time.perf_counter(); history=services.list_frozen_artifacts(project);hist=time.perf_counter()-start
        rows.append({'candidates':count,'workspace_projection_seconds':projection,'gui_open_seconds':opened,'filter_seconds':filter_time,'keyboard_navigation_with_evidence_seconds':navigate,'review_operation_seconds':elapsed,'artifact_history_seconds':hist,'artifact_count':len(history),**memory()})
        window.grab().save(str(args.output/f'scale-{count}.png'))
        (args.output/'scale-results.json').write_text(json.dumps(rows,indent=2))
        print(json.dumps(rows[-1]),flush=True)
    from stimulus_harvester.frozen.demo import seed,request,run
    history_project=services.create_project(args.output/'artifact history',workspace.default_configuration('synthetic artifact history'))
    seed(history_project,'one',label='accepted')
    for _ in range(20): run(history_project,'freeze-create',request=request())
    start=time.perf_counter();artifacts=services.list_frozen_artifacts(history_project);listing=time.perf_counter()-start
    start=time.perf_counter();window.session.submit('open',path=str(history_project.root));wait();opening=time.perf_counter()-start
    assert len(artifacts)==20
    rows.append({'history_project_artifacts':20,'artifact_listing_seconds':listing,'gui_open_with_history_seconds':opening,**memory()})
    (args.output/'scale-results.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps(rows[-1]),flush=True)
    window.close();app.processEvents()


if __name__=='__main__':main()
