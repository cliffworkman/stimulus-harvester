# Advanced installation, CLI and optional ML

The Windows portable/installed folder includes `stimulus-harvester.exe` for CLI,
and `runtime\python.exe -I -B -m stimulus_harvester` for module invocation.
Run `stimulus-harvester.exe --help`. Browser review: use the `review` command shown
there; keep its default loopback host. Browser templates/static files are included.

For a normal Python installation (Python 3.11+; release validation identifies the
specific tested interpreter), install the supplied wheel:

```powershell
py -3.12 -m venv harvester-ml
.\harvester-ml\Scripts\python.exe -m pip install "path\stimulus_harvester-0.2.0rc2-py3-none-any.whl[gui]"
.\harvester-ml\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
.\harvester-ml\Scripts\python.exe -m pip check
.\harvester-ml\Scripts\stimulus-harvester-gui.exe --project "your project"
```

CPU wheels are explicit: do not substitute a default CUDA install. No model is
selected by these commands. Close the core desktop before opening the same project
in the ML desktop. Choose a model/thresholds/rule deliberately, then run screening.
The first explicit inference retrieves weights. Set TORCH_HOME before launch to
choose a writable model-cache directory. Optional OpenCLIP requires its extra and
independent model/weight terms review; Ollama is an external separately managed service.

The wheel also supports headless CLI/browser use without `[gui]`. Linux/macOS source
and wheel paths are architectural possibilities, not validated release targets in
this checkpoint. No macOS/Linux binaries are supplied.

## Rebuilding Windows

Build with normal Python, pip, setuptools and wheel, plus PySide6-Essentials/Pillow
for deterministic SVG-to-ICO conversion. `release/build_windows.py` takes explicit
work/output/embedded-runtime/compiler paths. `requirements-windows.lock` pins
exact Windows CPython 3.13 wheel SHA-256 values; `inputs.json` pins runtime/compiler.
Use `pip download --require-hashes --only-binary=:all: --platform win_amd64
--python-version 313 --implementation cp --abi cp313 -r release/requirements-windows.lock`.
The build consumes that wheelhouse offline, verifies each hash, installs by safe
wheel extraction, and includes package sources/resources, licenses, metadata and
per-file hashes. Do not resolve new dependency versions during a release rebuild.
The portable archive uses fixed timestamps/order. Compiler/ZIP compression versions
and generated PE timestamps may affect installer bytes; dependency/content
reproducibility is provided, not an unverified bit-identical installer claim.

Signing is a separate publication operation using an authorized Authenticode
certificate and trusted timestamp service. No certificate/private key is stored
here. Sign both launchers before packaging and the installer afterward; regenerate
checksums and repeat installed verification of the final signed artifacts.
