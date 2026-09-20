# Checkpoint 7 distribution boundary

Version **0.2.0rc1** is an unpublished Windows x64 release candidate. There is no
production person detector default. Artifact and database schemas remain unchanged.

The Windows distribution uses official CPython 3.13.15 embedded binaries, a fully
pinned/hash-locked wheel graph, a minimal Qt Widgets/SVG runtime, ordinary readable
Python package sources, two relative-path Windows launchers, and Inno Setup 6.7.3.
It supplies a per-user installer and portable ZIP. No global Python or PATH change
is required. The GUI launcher is a Windows GUI-subsystem executable, so ordinary
launch does not create a console; the console launcher preserves CLI workflows.

This was chosen over a frozen executable because the existing backend hashes its
own source files for provenance, lazily imports optional dependencies, uses real
Python subprocesses for capability checks, and serves packaged Jinja/static files.
Keeping an ordinary interpreter/package layout preserves all those mechanisms.
PyInstaller/cx_Freeze would require extra source/probe/resource hooks; a pip-only
installer would require researchers to arrange Python. A one-file self-extractor
would introduce temporary extraction and make replaceable native libraries harder
to inspect. The chosen portable payload is about a few hundred MiB unpacked; see
the actual verification report for exact byte counts.

Core+GUI includes NumPy/SciPy/PyWavelets through ImageHash's mechanical image
processing dependencies, plus HTTP/API/browser libraries and Qt Essentials.
Qt Addons, Torch/torchvision, OpenCLIP, CUDA and model weights are omitted. The
standalone runtime is fixed, without pip; ML uses a separately provisioned ordinary
Python environment and the supplied wheel. Existing project rules are unchanged.

Build scripts, input URLs/hashes, wheel lock and test harnesses are in `release/`.
The output includes notices, inventory, checksums and matching Qt/PySide sources.
`requirements-windows.in` records audited versions; the `.lock` and `inputs.json`
are authoritative for a rebuild. Updating them requires a fresh release validation.
`SOURCE_DATE_EPOCH` and portable ZIP member ordering/timestamps are fixed. No
bit-identical installer claim is made without a second same-toolchain comparison.

See the packaged USER-GUIDE for researchers; ADVANCED covers CLI/browser/ML and
rebuilds. Preserve original version strings inside historical frozen evidence.
Windows native script/visual checks are distinct from a fresh-machine VM acceptance
test. Linux/macOS remain unverified targets; no cross-platform binary is claimed.

No update service, telemetry, public upload, release tag or signing key is added.
