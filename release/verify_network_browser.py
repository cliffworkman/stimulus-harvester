"""Bounded live Openverse and installed loopback browser smoke; explicit network test."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from stimulus_harvester import services,workspace


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    data=workspace.default_configuration('release provider smoke').model_dump(mode='json')
    data['search']['seed_queries']=['architecture interior']
    data['search']['openverse'].update(page_size=2,max_pages_per_query=1,max_retries=0,request_timeout_seconds=20)
    from stimulus_harvester.config import HarvesterConfig
    project=services.create_project(args.output/'public provider query',HarvesterConfig.model_validate(data))
    result=services.run_stage(project,'search')
    evidence={'openverse_status':result.status,'openverse_result':result.result,'openverse_error':result.error,'diagnostics':services.discovery_diagnostics(project)}
    # One real Uvicorn HTTP server, explicitly loopback only, selected free port.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    script="from pathlib import Path;import sys,uvicorn;from stimulus_harvester import services;p=services.open_project(Path(sys.argv[1]));uvicorn.run(services.review_application(p),host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')"
    with (args.output/'browser-server.log').open('w') as log:
        child=subprocess.Popen([sys.executable,'-I','-B','-c',script,str(project.root),str(port)],stdout=log,stderr=log)
        try:
            with httpx.Client(timeout=2,trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get(f'http://127.0.0.1:{port}/healthz').status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                responses={path:client.get(f'http://127.0.0.1:{port}'+path).status_code for path in ('/healthz','/images','/static/style.css','/static/review.js')}
                assert all(code==200 for code in responses.values()),responses
                evidence['browser_http']=responses
        finally:child.terminate();child.wait(timeout=15)
    (args.output/'network-browser.json').write_text(json.dumps(evidence,indent=2,default=str),encoding='utf-8')
    print(json.dumps(evidence,indent=2,default=str))


if __name__=='__main__':main()
