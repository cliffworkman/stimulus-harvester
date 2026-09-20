"""Offline Windows core+GUI build from verified input archives and locked wheels.

Run --help. Output must be new: the build never deletes an existing destination.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

REPO = Path(__file__).resolve().parents[1]


def qt_runtime_member(name):
    """Ship only dynamically linked Widgets/SVG dependencies, not QML/designer/tools."""
    parts = PurePosixPath(name).parts
    if not parts or parts[0] != "PySide6":
        return True
    if len(parts) == 2:
        return parts[1] in {
            "__init__.py", "_config.py", "_git_pyside_version.py", "py.typed",
            "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd", "QtSvg.pyd", "QtTest.pyd",
            "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll", "Qt6Test.dll",
            "pyside6.abi3.dll", "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll",
            "msvcp140_2.dll", "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll",
        }
    if len(parts) >= 3 and parts[1] in {"support"}:
        return True
    return name in {
        "PySide6/plugins/platforms/qwindows.dll", "PySide6/plugins/platforms/qoffscreen.dll",
        "PySide6/plugins/styles/qmodernwindowsstyle.dll", "PySide6/plugins/iconengines/qsvgicon.dll",
        "PySide6/plugins/imageformats/qgif.dll", "PySide6/plugins/imageformats/qico.dll",
        "PySide6/plugins/imageformats/qjpeg.dll", "PySide6/plugins/imageformats/qsvg.dll",
    }


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract(archive, target, *, wheel=False):
    """Reject paths outside target and symlinks, even for pinned inputs."""
    target = target.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            name = PurePosixPath(item.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in item.orig_filename or ":" in item.filename:
                raise ValueError(f"Unsafe archive member: {item.filename}")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive symlinks are not accepted")
            if wheel and not qt_runtime_member(item.filename):
                continue
            if wheel and name.parts[0].endswith(".data"):
                if name.parts[1] not in {"purelib", "platlib"}:
                    # Wheel scripts are replaced by our explicit, relocatable launchers.
                    continue
                name = PurePosixPath(*name.parts[2:])
            output = target.joinpath(*name.parts).resolve()
            if not output.is_relative_to(target):
                raise ValueError("Archive member escapes destination")
            if item.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(bundle.read(item))


def portable_zip(root, output):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(root.name + "/" + path.relative_to(root).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())


def icon(destination):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image

    svg = REPO / "src/stimulus_harvester/resources/harvester.svg"
    canvas = QImage(256, 256, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    QSvgRenderer(str(svg)).render(painter)
    painter.end()
    # Direct raw pixel conversion avoids retaining a build-path raster file.
    rgba = canvas.convertToFormat(QImage.Format.Format_RGBA8888)
    Image.frombytes("RGBA", (256, 256), bytes(rgba.bits())).save(
        destination, sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]
    )


def licenses(wheels, payload, inputs):
    root = payload / "licenses"
    root.mkdir()
    records = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            metadata = email.message_from_bytes(archive.read(next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))))
            name, version = metadata["Name"], metadata["Version"]
            texts = []
            for member in archive.namelist():
                lower = member.lower()
                if not member.endswith("/") and (
                    "/licenses/" in lower or "/license" in lower or "/copying" in lower
                    or "/notice" in lower or "sbom" in lower
                ):
                    target = root / name / member
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
                    texts.append(target.relative_to(payload).as_posix())
            records.append({"name":name,"version":version,"wheel":wheel.name,"wheel_sha256":sha(wheel),
                "declared_license": metadata["License-Expression"] or metadata["License"],
                "license_classifiers":[x for x in metadata.get_all("Classifier",[]) if x.startswith("License")],
                "project_urls":metadata.get_all("Project-URL",[]), "home_page":metadata["Home-page"],
                "requirements":metadata.get_all("Requires-Dist",[]),"license_files":texts})
    shutil.copyfile(payload / "runtime/LICENSE.txt", root / "Python-LICENSE.txt")
    shutil.copyfile(inputs / "inno/License.txt", root / "Inno-Setup-LICENSE.txt")
    records.extend([
        {"name":"CPython embedded Windows x64", "version":"3.13.15", "license_files":["licenses/Python-LICENSE.txt"], "source":"https://www.python.org/ftp/python/3.13.15/Python-3.13.15.tar.xz"},
        {"name":"Inno Setup", "version":"6.7.3", "license_files":["licenses/Inno-Setup-LICENSE.txt"], "source":"https://github.com/jrsoftware/issrc/tree/is-6_7_3"},
    ])
    write_json(payload / "dependency-inventory.json", records)
    return records


def main():
    os.environ["SOURCE_DATE_EPOCH"] = "1767225600"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True, help="Pinned runtime ZIP, compiler installer, inno/ and wheels/")
    parser.add_argument("--output", type=Path, required=True, help="New RC destination")
    parser.add_argument("--work", type=Path, required=True, help="New disposable build directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.work.mkdir(parents=True, exist_ok=False)
    inputs = json.loads((REPO / "release/inputs.json").read_text())
    for entry in inputs["downloads"] + inputs["sources"]:
        if sha(args.inputs / entry["filename"]) != entry["sha256"]:
            raise ValueError(f"Changed build input: {entry['filename']}")
    wheel_manifest = inputs["wheels"]
    wheels = []
    for entry in wheel_manifest:
        path = args.inputs / "wheels" / entry["filename"]
        if sha(path) != entry["sha256"]:
            raise ValueError(f"Changed wheel: {path.name}")
        wheels.append(path)
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    payload = args.work / f"Stimulus-Harvester-{version}-windows-x64"
    payload.mkdir()
    runtime = payload / "runtime"
    extract(args.inputs / inputs["downloads"][0]["filename"], runtime)
    (runtime / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n", encoding="ascii")
    site = runtime / "Lib/site-packages"
    for wheel in wheels:
        extract(wheel, site, wheel=True)
    icon(REPO / "src/stimulus_harvester/resources/harvester.ico")
    # Clean source copy prevents editable metadata, build caches or private local files leaking.
    source = args.work / "source"
    source.mkdir()
    shutil.copytree(REPO / "src", source / "src", ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copyfile(REPO / name, source / name)
    subprocess.run([sys.executable,"-m","pip","--isolated","wheel","--no-deps","--no-build-isolation","--no-index","--wheel-dir",str(args.output),str(source)],check=True)
    wheel = next(args.output.glob("stimulus_harvester-*.whl"))
    extract(wheel, site, wheel=True)
    shutil.copyfile(REPO / "LICENSE", payload / "LICENSE")
    shutil.copyfile(REPO / "src/stimulus_harvester/resources/harvester.ico", payload / "harvester.ico")
    shutil.copyfile(REPO / "src/stimulus_harvester/resources/USER-GUIDE.md", payload / "USER-GUIDE.md")
    shutil.copyfile(REPO / "release/ADVANCED.md", payload / "ADVANCED.md")
    shutil.copytree(REPO / "docs", payload / "docs")
    license_records = licenses(wheels + [wheel], payload, args.inputs)
    source_output = args.output / "third-party-sources"
    source_output.mkdir()
    for entry in inputs["sources"]:
        archive_path = args.inputs / entry["filename"]
        shutil.copyfile(archive_path, source_output / archive_path.name)
        # Copy license/attribution files directly, never extract untrusted tar paths.
        with tarfile.open(archive_path) as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if member.isfile() and ("LICENSES" in name.parts or name.name.lower().startswith(("license", "copying", "notice")) or name.name == "qt_attribution.json"):
                    if name.is_absolute() or ".." in name.parts or ":" in str(name) or "\\" in str(name):
                        raise ValueError("Unsafe license source path")
                    target = payload / "licenses/Qt-sources" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
    write_json(payload / "source-archives.json", inputs["sources"])
    shutil.copyfile(REPO / "release/THIRD-PARTY.md", payload / "THIRD-PARTY.md")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    for name, cli in (("Stimulus Harvester.exe",False),("stimulus-harvester.exe",True)):
        subprocess.run([str(compiler),"/nologo","/optimize+","/platform:x64",f"/target:{'exe' if cli else 'winexe'}",
            "/reference:System.Windows.Forms.dll",f"/win32icon:{payload / 'harvester.ico'}",
            f"/out:{payload / name}"] + (["/define:CLI"] if cli else []) + [str(REPO / "release/Launcher.cs")],check=True)
    write_json(payload / "release.json", {"product":"Stimulus Harvester","version":version,"tier":"core-gui", "platform":"windows-x64", "python":"3.13.15","signed":False,"baseline_commit":"b2ac2df3b1cb751c329494cdb99487e30a6946f6", "ml_included":False,"model_weights_included":False,"dependency_count":len(license_records)})
    write_json(payload / "payload-sha256.json", {p.relative_to(payload).as_posix():sha(p) for p in sorted(payload.rglob("*")) if p.is_file()})
    portable_zip(payload, args.output / f"{payload.name}-portable.zip")
    subprocess.run([str(args.inputs / "inno/ISCC.exe"), f"/DPayload={payload}",f"/DDestination={args.output}",f"/DVersion={version}",str(REPO / "release/windows.iss")],check=True)
    for name in ("release.json","dependency-inventory.json","source-archives.json","THIRD-PARTY.md","USER-GUIDE.md","ADVANCED.md","payload-sha256.json"):
        shutil.copyfile(payload / name, args.output / name)
    shutil.copytree(payload / "licenses", args.output / "licenses")
    shutil.copytree(REPO / "release", args.output / "build-recipe", ignore=shutil.ignore_patterns("__pycache__"))
    (args.output / "SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(args.output).as_posix()}\n" for p in sorted(args.output.rglob('*')) if p.is_file() and p.name != "SHA256SUMS"),encoding="utf-8")
    print(json.dumps({"payload":str(payload),"output":str(args.output),"version":version}))


if __name__ == "__main__":
    main()
