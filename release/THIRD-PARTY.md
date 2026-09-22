# Third-party attribution and redistribution review

The machine-readable `dependency-inventory.json` names the exact redistributed
wheel versions, their SHA-256 values, declared terms, requirements, upstream URLs,
and copied license files. The **license texts**, not a guessed package-name mapping,
are in `licenses/` and alongside original wheel metadata in the runtime.
Harvester and the two small launcher sources are MIT. The original editable SVG
icon was created for this project; no external art or institutional logo is used.

## Runtime and packaging

CPython 3.13.15 is the official Windows x64 embedded distribution. Its LICENSE.txt
includes PSF/history and bundled-component notices, including OpenSSL, expat,
libffi and zlib. The embedded ZIP also retains its original runtime DLLs.
Inno Setup 6.7.3 (Jordan Russell / Martijn Laan) builds the installer and uninstaller;
its license text is supplied. The compiler is a build tool, not shipped as an app.
The .NET Framework launcher uses the Windows .NET Framework 4.x already present
on the target Windows system; the .NET runtime/compiler is not redistributed.
Setuptools, pip and wheel are build tools and are absent from the core runtime.

## Qt/PySide

PySide6 Essentials and Shiboken6 6.11.2 declare LGPL-3.0-only OR GPL-2.0-only OR
GPL-3.0-only. The wheel's commercial-license notice is retained but **does not
grant a commercial license**. Open-source license texts and upstream attributions
are included from the exact Qt/PySide 6.11.2 source archives.

The distribution selects the dynamically loaded Qt Core, Gui, Widgets, Svg and
Test modules, their Python bindings, Windows/offscreen platform plugins, modern
Windows style, SVG icon engine and GIF/ICO/JPEG/SVG image plugins. Other Essentials
files are deliberately omitted: no QML, Qt Quick, Designer, WebEngine, Qt tools,
software OpenGL or Addons. `qt_runtime_member` in the build recipe is the complete
allowlist; `payload-sha256.json` records the actual files. The original wheel
metadata/RECORD is retained for attribution, but is not a claim that every wheel
member is installed; payload-sha256 is authoritative for this distribution.

Qt libraries remain ordinary replaceable DLLs/PYD files; there is no signature or
hash gate preventing a user from replacing them with compatible modified builds.
Harvester permits modification and reverse engineering for debugging such changes.
Its Python and launcher sources/build recipe are supplied. The RC directory also
provides exact-version upstream QtBase, QtSvg and PySide/Shiboken source archives
with SHA-256/source URLs. Preserve access to these when conveying binaries.
Those archives contain upstream build instructions and third-party notices.

QtBase attribution files cover included or potentially compiled native components
such as FreeType, HarfBuzz, libjpeg-turbo, libpng, zlib, PCRE2 and double-conversion.
The source-wide notice set is intentionally conservative, and includes components
not necessarily active in this Windows build. It is **not** a binary-level SBOM or
proof of the vendor's exact configure flags. Microsoft Visual C++ runtime DLLs
supplied in official Python/Qt wheels are retained as runtime dependencies;
their applicable Microsoft redistributable terms must be checked again against
the chosen compiler/runtime versions before public redistribution.

## Other Python and native components

Core HTTP/UI dependencies retain their MIT/BSD/MPL/PSF notices as stated in the
inventory. Certifi uses MPL-2.0; its unmodified CA data/source are supplied.
Pillow 10.4.0's complete bundled LICENSE contains HPND and native image-codec
notices (including brotli, FreeType, JPEG, LCMS, TIFF, WebP, OpenJPEG, zlib).
NumPy/SciPy wheels retain their complete notices including BLAS/LAPACK and
compiler-runtime notices/exception texts where supplied. PyWavelets retains its
MIT/BSD notices. Do not reduce these aggregates to one package-level license.

No Torch, torchvision, OpenCLIP, Ollama, CUDA stack or model weights are
redistributed in the core artifacts. Optional provisioning retrieves components
directly from their maintainers and requires the user's separate terms review.

## Publication boundary

This is a bounded engineering inventory and source/notice preservation exercise,
not a legal certification. Before public distribution, finish binary-level matching
of vendor native runtime notices/terms and source/build correspondence, check the
final distribution artifact again, and make the relevant source archives available with
the binaries. Do not infer that the application's MIT license replaces library
licenses. No public release is made by this checkpoint.
