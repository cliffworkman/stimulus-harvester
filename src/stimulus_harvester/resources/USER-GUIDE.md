# Stimulus Harvester 0.2.0rc4

Research stimulus discovery & adjudication. Release candidate for evaluation;
no production person detector has been selected.

## Install and first launch

Windows 11 x64 has been tested on the existing host. Windows 10 x64 is the
installer-declared/expected minimum, not an empirically validated platform.
Run the approved `windows-x64-setup.exe`, or extract the entire
portable ZIP to an empty folder. Launch **Stimulus Harvester.exe**. No Python, Git,
Codex or model installation is needed. Stimulus Harvester's current Windows builds are unsigned. Windows may display an Unknown Publisher or SmartScreen warning. Code signing is intentionally deferred while distribution infrastructure is being reconciled across the Stimulus Modernization Suite. Compare the download against the separately
received SHA256SUMS before deciding whether to trust it. A checksum alone does not
authenticate its publisher. Do not bypass organizational security restrictions.

Keep the application folder intact. Put research projects in a separate writable
folder, such as Documents. Paths with spaces and `研究` are supported. Prefer a local
working copy and sync/backup only when operations are idle; never have two programs
writing to the same project at once.

## The six stages

1. **Project:** create an empty project folder or open an existing one. Reopen by
   browsing to the same folder. The synthetic demo contains invented images and
   saved mock observations; it is a safe way to learn, not detector accuracy evidence.
2. **Discover:** enter queries, choose providers, preview coverage, search and
   download originals. Openverse needs network access; service limits can apply.
   SearXNG requires your instance URL. Commons requires your real operator-contact
   user agent. Enabling either provider opens configuration immediately if its
   required setting is missing or invalid; cancelling leaves it unchecked. Settings
   are saved when you choose Start a new search. Download pending images handles
   unattempted results; Retry failed downloads retries eligible failures without
   repeating searches or previously successful downloads. Found/downloaded/failed/
   pending counts describe results; unique available images describe shared bytes.
   Openverse reaching its advertised result boundary is normal completion. A new
   search starts at page 1; search continuation is not yet available. Misconfiguration
   and remote errors are reported distinctly.
3. **Screen:** optional. Core does not contain Torch, OpenCLIP, CUDA or weights.
   Leave screening unconfigured and perform human review, or explicitly provision
   ML, select a detector, thresholds and project rule. `not_detected` never proves
   absence. Observation, rule evaluation and investigator adjudication remain separate.
4. **Review freeze:** inspect images and all source/license claims; record reviewer
   identity, Accept/Maybe/Reject and notes. Inspect validation blockers and resolve
   them before confirming a freeze. A freeze seals evidence and membership;
   subsequent live reviews do not change it.
5. **Assemble:** select the immutable freeze and make a deterministic package from
   the original bytes. Conflicting exact-asset histories require explicit handling.
6. **Export:** export an assembly/freeze with source claims, manifests and checksums.
   Verify with its expected manifest SHA-256. Hash verification detects changes;
   it does not certify image licenses or scientific suitability.

Back up/copy the **whole project folder**, including originals, database, config,
operations and frozen artifacts. Close the application before copying. Do not edit
frozen files or replace originals. Old immutable metadata keeps its original version.

## Optional ML

The standalone core runtime is fixed and has no pip. Do not inject packages into it.
Use the separately supplied wheel in a normal Python 3.12/3.13 virtual environment.
See the accompanying **ADVANCED.md** for exact CPU provisioning commands. That
environment can run the same desktop/CLI against your project after the other copy
has closed. This does not alter frozen evidence or choose a production detector.

Explicit first screening may download approximately 167 MiB (ResNet50 v2) or 74 MiB
(MobileNet320) of weights. Torch itself requires several hundred MiB; CUDA builds
can require gigabytes. Weights are not bundled. Exact weight name, URL, full SHA-256
and runtime versions are stored with observations. OpenCLIP and external Ollama
remain separate optional installations; they are not prerequisites for human review.

## Upgrade and uninstall

Close all Harvester processes and back up projects first. For this RC, uninstall the
previous RC before installing a newer one, or extract portable builds to new folders.
Do not overlay portable versions. There is no updater or background update check.
The installer removes its registered application files and shortcuts only. Removing
the portable folder removes the application only. External projects survive.

Uninstall preserves external projects, launch-error logs, and optional model caches.
No persistent recent-project list or theme preference is currently written. Runtime
help/icon/templates are read from the installation. Scientific logs live in projects.
Only failed GUI launches write `%LOCALAPPDATA%\StimulusHarvester\logs`.
Optional Torch defaults to `~/.cache/torch` (or `TORCH_HOME`); Hugging Face/OpenCLIP
can use their own user caches (`HF_HOME` where supported). Ollama manages its own
service/model storage. Remove such caches manually only when no longer needed.

Mutable schema upgrades use the existing backed-up migration path. Immutable freeze,
assembly and export manifests are never silently rewritten by installer upgrades.

## Recovery and privacy

No telemetry or launch-time model download is added. Searches send queries to the
providers you enable; downloads contact image hosts. Optional inference is local
after weight download. An explicit dependency check can contact the configured
Ollama endpoint. Browser review defaults to loopback and is unnecessary for desktop.
Do not expose its unauthenticated server to the network. Provider-returned links
remain untrusted, and source/license claims require researcher verification.

If an operation fails, read its message and expanded details. Preserve the project
and operation logs. A lock means another operation may be writing: do not blindly
delete it. Pending/unregistered artifacts require inspection, not automatic cleanup.
Malformed live configuration can be repaired in Project advanced settings; immutable
artifacts remain available. Missing originals must be restored byte-for-byte from
backup. Reinstalling the application does not repair altered research evidence.

This RC has bounded scripted keyboard, contrast and scale checks, not accessibility
certification. Consult the verification report for the measured platform and limits.

## License

Harvester: MIT, copyright 2026 Stimulus Harvester contributors. No institutional
endorsement is implied. See LICENSE and third-party attribution inventory in the
distribution. Qt/PySide is dynamically loaded and replaceable; its applicable license
texts and source references are supplied. Modification/relinking for LGPL debugging
is not restricted by Harvester. Preserve third-party notices when redistributing.
