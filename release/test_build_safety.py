"""Fast release-specific checks; deliberately outside the ordinary tests directory."""
import importlib.util
import json
from pathlib import Path
import tomllib
import zipfile

import pytest

spec=importlib.util.spec_from_file_location('build_windows',Path(__file__).with_name('build_windows.py'))
build=importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.mark.parametrize('name',['../escape','/absolute','C:/escape','folder/../../escape','folder\\escape'])
def test_rejects_unsafe_archives(tmp_path,name):
    archive=tmp_path/'untrusted.zip'
    info=zipfile.ZipInfo('placeholder')
    info.filename=name
    with zipfile.ZipFile(archive,'w') as z:z.writestr(info,b'untrusted')
    with pytest.raises(ValueError):build.extract(archive,tmp_path/'target')


def test_rejects_archive_symlink(tmp_path):
    archive=tmp_path/'untrusted.zip'
    info=zipfile.ZipInfo('link');info.external_attr=0o120777<<16
    with zipfile.ZipFile(archive,'w') as z:z.writestr(info,'../../escape')
    with pytest.raises(ValueError):build.extract(archive,tmp_path/'target')


def test_qt_allowlist_avoids_unused_modules():
    assert build.qt_runtime_member('PySide6/QtWidgets.pyd')
    assert build.qt_runtime_member('PySide6/plugins/platforms/qwindows.dll')
    assert not build.qt_runtime_member('PySide6/Qt6Quick.dll')
    assert not build.qt_runtime_member('PySide6/designer.exe')
    assert not build.qt_runtime_member('PySide6/plugins/imageformats/qpdf.dll')


def test_portable_archive_repeatable_and_relocatable(tmp_path):
    root=tmp_path/'portable';root.mkdir();(root/'研究.txt').write_text('content')
    build.portable_zip(root,tmp_path/'a.zip');build.portable_zip(root,tmp_path/'b.zip')
    assert build.sha(tmp_path/'a.zip')==build.sha(tmp_path/'b.zip')
    build.extract(tmp_path/'a.zip',tmp_path/'new location')
    assert (tmp_path/'new location/portable/研究.txt').read_text()=='content'


def test_release_metadata_and_hash_lock():
    project=tomllib.loads((build.REPO/'pyproject.toml').read_text())['project']
    from stimulus_harvester import __version__
    assert project['version']==__version__=='0.2.0rc6'
    assert 'stimulus-harvester-gui' in project['gui-scripts']
    data=json.loads((build.REPO/'release/inputs.json').read_text())
    assert len(data['wheels'])==27
    assert all(len(x['sha256'])==64 for x in data['downloads']+data['wheels']+data['sources'])
    assert not any(any(x in item['filename'].lower() for x in ('torch','open_clip','cuda','pyside6_addons')) for item in data['wheels'])
