"""Scan actual portable contents, verify hash inventory and executable subsystem."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
import zipfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    archive=next(args.release.glob('*-portable.zip'))
    forbidden=[b'c:\\users\\cliff',b'c:/users/cliff',b'checkpoint2-venv',b'codex-runtimes',b'01_work',b'cp7-package-final']
    findings=[]; generic=[]; count=0; total=0
    with zipfile.ZipFile(archive) as z:
        prefix=z.namelist()[0].split('/')[0]+'/'
        manifest=json.loads(z.read(prefix+'payload-sha256.json'))
        for name,expected in manifest.items():
            content=z.read(prefix+name); count+=1;total+=len(content)
            assert hashlib.sha256(content).hexdigest()==expected,name
            lower=content.lower()
            hits=[p.decode() for p in forbidden if p in lower or p.decode().encode('utf-16-le') in lower]
            if hits:findings.append({'file':name,'patterns':hits})
            at=lower.find(b'juno')
            if at>=0:generic.append({'file':name,'context':repr(content[max(0,at-35):at+50])})
            if name.endswith('.zip'):
                import io
                with zipfile.ZipFile(io.BytesIO(content)) as nested:
                    for inner in nested.namelist():
                        data=nested.read(inner).lower()
                        hits=[p.decode() for p in forbidden if p in data]
                        if hits:findings.append({'file':name+'!'+inner,'patterns':hits})
        def subsystem(name):
            data=z.read(prefix+name); offset=struct.unpack_from('<I',data,0x3c)[0]
            return struct.unpack_from('<H',data,offset+24+68)[0]
        assert subsystem('Stimulus Harvester.exe')==2
        assert subsystem('stimulus-harvester.exe')==3
    assert not findings,findings
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.release.iterdir() if p.suffix in {'.exe','.zip','.whl'}}
    result={'payload_files_verified':count,'payload_bytes':total,'forbidden_path_matches':findings,'broad_keyword_matches_for_review':generic,'GUI_PE_subsystem':2,'CLI_PE_subsystem':3,'artifacts':hashes}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
