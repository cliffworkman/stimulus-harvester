# stimulus-harvester

A local-first, free/open-source pipeline for harvesting, filtering, scoring, and
reviewing candidate stimulus images for research use — built first for a narrow
practical problem: finding more prison-cell architectural photographs, especially
ones photographed from *inside* a cell looking outward, to supplement an existing
image set for a possible fMRI study.

## Purpose and scientific caution

This tool automates **acquisition and ranking based on observable visual properties
only**. It searches open image sources, downloads candidates, filters out obvious
junk, and scores/annotates the rest using local vision models (CLIP, and optionally a
local vision-language model).

**It does not, and must not, attempt to infer "self relevance" or any other
psychological construct.** The eventual research design crosses self-relevance and
valence in a 2x2 layout, but construct validity is a human research decision — this
pipeline only gets you a well-organized, well-documented set of *candidates* with
consistent scene metadata. Final stimulus selection, norming, counterbalancing, and
any claims about what a stimulus does to a viewer are entirely out of scope here.

CLIP relevance scores and VLM adjudication fields are **review aids, not filters**.
Nothing in the automated pipeline excludes a candidate because of a low relevance
score or an unfavorable VLM answer — the only automated exclusions are non-semantic
(unreadable file, too small, extreme aspect ratio, exact/near-duplicate). Every
candidate that survives those mechanical checks stays visible in the review app, sorted
by relevance so the most promising ones surface first, until a human explicitly
accepts, marks maybe, or rejects it.

### Explicit non-goals

This repository does **not** implement, and is not intended to grow into:

- automated estimation of "self relevance" or any other psychological construct
- automated neuroscience inference of any kind
- final stimulus norming
- experimental randomization or counterbalancing
- fMRI presentation scripts

Those are downstream research steps that belong in a separate, human-supervised
project.

## System requirements

- Python 3.11+
- Debian (or any Linux) recommended for the full pipeline including CLIP; also runs
  without CLIP/VLM on any OS for search/download/filter/review/export.
- An NVIDIA GPU is optional but strongly recommended for CLIP scoring at scale (an
  8 GB card such as an RTX 3050 comfortably runs `ViT-B-32`). CPU-only CLIP works, just
  slower.
- No cloud services required. Openverse's API is free and keyless. SearXNG and Ollama,
  if used, are expected to run locally or on your own LAN.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1` and use `python`
instead of `python3`. Installing `requirements.txt` alone does **not** install this
application. For development use `python -m pip install -e ".[dev]"`.

This installs everything needed for search, download, filtering, the review app, and
exports. CLIP scoring needs one more step (kept separate because you should install a
CUDA-matched `torch` wheel for your GPU/driver first — installing `open_clip_torch`
before `torch` risks pip pulling a CPU-only or CUDA-mismatched wheel):

```bash
python3 -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu118 && python3 -m pip install -e ".[clip]"
```

**Pin an exact torch/torchvision version rather than installing unpinned "latest"
from a CUDA-tagged index.** Confirmed during testing: PyTorch's CUDA-tagged indexes
(e.g. `.../whl/cu118`) do not necessarily keep serving a cu118 build of the *newest*
torch release — an unpinned `pip install torch --index-url .../cu118` on this project's
own test box resolved to `torch==2.14.0+cu130`, which requires a CUDA 13 driver and
silently fails (`torch.cuda.is_available()` returns `False`) on a driver that only
supports up to CUDA 12.2. `torch==2.7.1`/`torchvision==0.22.1` are the versions
verified working end-to-end (including real CLIP inference) on an RTX 3050 with driver
535.309.01 — adjust only if your driver/CUDA situation genuinely differs, and always
verify with `python3 -c "import torch; print(torch.cuda.is_available())"` before
trusting a new pin. Also uninstall (or use a fresh venv) rather than upgrading torch
in place if you've previously installed a different CUDA-tagged build: pip does not
clean up now-mismatched `nvidia-*-cuXX` dependency packages left behind by the old
build, and their presence alongside a new build can itself cause
`CUDNN_STATUS_NOT_INITIALIZED` errors.

(Use whichever CUDA index URL and torch version matches your driver; for CPU-only,
just `python3 -m pip install -e ".[clip]"` without a special index.)

The optional VLM adjudication step needs no extra Python packages — it talks to a
local [Ollama](https://ollama.com) server over HTTP:

```bash
ollama pull llava:7b
```

**Model capability matters for this step, confirmed during testing:** a very small
vision model (`moondream`, ~1.8B) reliably returned *incomplete* JSON for this task's
17-field schema (consistently omitting one or more required keys, even after the
built-in retry-with-corrective-prompt), which the strict field validation correctly
rejected as `status: error` rather than silently accepting partial data. `llava:7b`
completed the same schema correctly on every test image, including correctly
identifying an off-target search result as `likely_prison_environment: false` with
`obvious_non_prison_confounds: ["unrelated outdoor scene"]`. If you need a smaller
model for VRAM reasons, expect to see more `status: error` rows and treat that as a
signal to either use a larger model or shorten `vlm.fields` in your config, not as a
bug to work around.

Optionally, for development:

```bash
python3 -m pip install -e ".[dev]"
```

## Configure

Set `REVIEWER_EMAIL` in your shell, or pass `review --reviewer`, for review attribution.
Configure SearXNG and Ollama endpoints in YAML. The application does not load `.env`
files; `.env.example` is a historical reference, not an active configuration loader.

The shipped config, `configs/prison_inside_cell.yaml`, is fully commented and is the
only place task-specific vocabulary lives: search queries, CLIP prompts, and the VLM's
observable-property question list. The pipeline code itself has no built-in knowledge
of "prison cells" — copy this file and edit it to point the same machinery at a
different visual target.

Key sections:

- `search.seed_queries` / `search.query_expansion` — what to search for. Expansion is
  deterministic synonym-group x phrase-template combination, no LLM involved by
  default.
- `search.openverse` — the default, keyless search backend.
- `search.searxng` — optional second backend; only runs if `enabled: true` and
  `endpoint_url` is set. Many public SearXNG instances disable `format=json` for abuse
  prevention, so you likely need a self-hosted instance.
- `search.commons` — optional Wikimedia Commons MediaWiki API discovery; requires
  an operator email or project URL in `contact`. Extended metadata is preserved as
  source claims, including HTML and uncertain licenses. See [discovery](docs/discovery.md).
- `filter` — the **only** thresholds that can hard-exclude a candidate, and they are
  all mechanical (dimensions, aspect ratio, icon-sized, exact/near duplicate).
- `clip` — model choice and the positive/negative prompt lists used for ranking.
- `vlm` — off by default (`backend: noop`). Set `backend: ollama` and fill in
  `ollama_model` to enable structured, observable-property-only adjudication. The
  `fields` list is exactly what gets asked about and exactly what gets stored — add,
  remove, or reword fields here, not in code.
- `export.open_license_allowlist` — which license strings count as "open" for
  `--license-filter open_only`.

## Durable projects (Checkpoint 2)

Use explicit project paths to reopen from any working directory:

```bash
stimulus-harvester project create --project "my research project" --config configs/prison_inside_cell.yaml
stimulus-harvester project status --project "my research project"
stimulus-harvester search --project "my research project"
stimulus-harvester download --project "my research project"
stimulus-harvester filter --project "my research project"
stimulus-harvester score --project "my research project"
stimulus-harvester review --project "my research project"
stimulus-harvester export --project "my research project"
```

Open/migrate an existing run with `stimulus-harvester project open --project PATH`.
No discovery rerun is required. See [project identity, migration, services, and
limitations](docs/projects.md). There is no freeze/assembly or desktop GUI yet.
`images/accepted/` is a **legacy, non-authoritative convenience folder** that can
retain copies from previous decisions; it is not an approved frozen stimulus set.

Installed wheels include browser resources and a minimal config you can extract:

```bash
python -c "from importlib.resources import files; from pathlib import Path; Path('harvester.yaml').write_text(files('stimulus_harvester').joinpath('resources/minimal.yaml').read_text(encoding='utf-8'), encoding='utf-8')"
```

## Legacy run-ID commands

Each stage reads/writes `runs/RUN_ID/`. Run commands from the same working directory
each time (the `RUN_ID` is looked up under `./runs` by default).

```bash
python3 -m stimulus_harvester search --config configs/prison_inside_cell.yaml
```

This prints a `run-id`. Use it for every subsequent stage:

```bash
python3 -m stimulus_harvester download --run-id RUN_ID
python3 -m stimulus_harvester filter --run-id RUN_ID
python3 -m stimulus_harvester score --run-id RUN_ID
python3 -m stimulus_harvester review --run-id RUN_ID
python3 -m stimulus_harvester export --run-id RUN_ID --label accepted
```

Rerunning `download`/`filter`/`score` processes rows that have not been through that
stage yet. Failed downloads are also considered attempted; retry-failed behavior is
not implemented. `search --config` creates a new project; use `search --project PATH`
to add discoveries to an existing project using its stored configuration.

- `search` — expands queries, calls enabled backends, writes raw API responses to
  `runs/RUN_ID/raw_api/`, and creates the run's SQLite DB.
- `download` — threaded downloader; every attempt (success or failure) is recorded,
  nothing is silently dropped.
- `filter` — non-semantic-only exclusion (see above). Passing images are copied to
  `runs/RUN_ID/images/filtered/`.
- `score` — always runs CLIP (requires the `[clip]` extra); runs the VLM stage too if
  `vlm.backend != noop`, or force it either way with `--vlm`/`--no-vlm`.
- `review` — launches the local review app (see below).
- `export` — writes CSV/JSONL manifests, an HTML contact sheet of accepted images, and
  a JSON summary to `runs/RUN_ID/exports/`.

## The review app

```bash
python3 -m stimulus_harvester review --run-id RUN_ID --reviewer you@example.com
```

Then open `http://127.0.0.1:8000/images` (host/port configurable via `review:` in the
config, or `--host`/`--port`).

- Grid view: filter by query/source/license/status, set a minimum score, sort by
  relevance or recency, paginated.
- Click a tile for the detail view: full provenance, every CLIP prompt's individual
  score (not just the composite), all VLM fields (if run), duplicate-cluster
  thumbnails, and file location.
- Accept / maybe / reject buttons, a free-text notes field, and a `scene_cluster_id`
  field you can use to tag images you notice depict the same physical scene or came
  from the same shoot/gallery (see "Stimulus independence" below). Keyboard shortcuts
  `a`/`m`/`r` on the detail page.
- Every label/notes change is appended to an audit log (`review_events`), never
  overwritten — you can always see the full history of a decision.

## What the model scores mean

- **`clip_relevance_score`** (0–1): `sigmoid(temperature * (mean(positive prompt
  similarities) - mean(negative prompt similarities)))`. 0.5 means "no net signal
  either way"; higher means the image looks more like the positive prompts than the
  negative ones, relative to the other candidates in this run. It is **a ranking aid,
  not a probability and not a pass/fail gate** — always inspect the individual prompt
  scores (shown in the detail view) rather than trusting the composite alone, and
  expect to re-tune `clip.squash_temperature` after looking at real score
  distributions from your first run.
- **VLM fields**: structured, observable-only answers from a local vision-language
  model (e.g. "are bars visible," "is a person present"). These are also review aids —
  useful facets to filter/sort by in the review app, never an automated accept/reject
  signal. `status: skipped` means the VLM stage didn't run for that image (default);
  `status: error` means the backend failed or returned unparseable output for that one
  image, which is recorded but never crashes the batch.
- **`final_relevance_score`** in exports always equals `clip_relevance_score`. VLM
  fields are exported as separate columns/keys, never blended into this number — this
  keeps ranking interpretable instead of an opaque combined score.

## Stimulus independence (beyond pHash)

Exact (`sha256`) and near-duplicate (`phash`) detection catch literal re-encodes/crops
of the same file. They cannot notice that two *different* photographs depict the same
physical cell, or came from the same photo session/gallery — that is a human-judgment
problem. To support it without pretending to solve it automatically:

- `source_group_id` is a **system-derived, best-effort hint** (typically
  domain + creator, or domain + search engine), populated automatically at search
  time. Treat it as "these might be related," never as ground truth.
- `scene_cluster_id` is **reviewer-assigned only** (via the review app or by editing
  the DB/export directly) and starts empty. Use it to mark "these five images are the
  same physical cell/session" so they are not later mistaken for independent
  exemplars when building a stimulus set.
- Provenance needed to notice this in the first place — `landing_page_url`,
  `source_domain`, `external_id` — is preserved for every candidate.
- The export summary reports `scene_clusters` sizes so a reviewer who assigned cluster
  ids can see the effect at a glance.

## Reproducibility

Every run writes `runs/RUN_ID/manifests/reproducibility.json`, updated as stages
complete: git commit, Python version, installed package versions, torch/CUDA/GPU info
(when CLIP runs), the CLIP model/checkpoint and a hash of the exact prompt list used, the
VLM backend/model identifier and a hash of the exact field schema used, a hash of the
resolved config, and per-stage completion timestamps. `runs/RUN_ID/manifests/` also
keeps the raw config snapshot (`config_snapshot.yaml`) that every stage after `search`
actually loads, so a run's exact settings are never ambiguous after the fact.

## Known limitations

- `clip.squash_temperature` is a starting heuristic; tune it after inspecting real
  score distributions.
- Openverse/SearXNG response field names may drift from what this code expects if
  those APIs change; the adapters were written against their documented shapes at
  implementation time.
- Public SearXNG instances often disable `format=json`; the optional backend may
  require a self-hosted instance.
- The default `vlm.ollama_model: llava:7b` was confirmed working (both for VRAM
  headroom and for reliably completing the full field schema) alongside CLIP on an
  8 GB RTX 3050. A much smaller model (`moondream`) ran without VRAM issues but
  consistently failed to return the complete 17-field schema — see the VLM section of
  "Install" above.
- The default `export.open_license_allowlist` excludes ND (no-derivatives) licenses,
  on the assumption that stimuli may need cropping/editing. Confirm this matches your
  actual reuse plans.
- Exact/near-duplicate detection is per-run only, not across multiple runs.
- No HEIC/AVIF decoding; such files are recorded as `unreadable_file` (not silently
  lost, just not usable without adding `pillow-heif`).
- No per-domain download rate limiting — a single slow or rate-limiting host could be
  hit by many concurrent requests if many results happen to share a domain.
- **With the default `vlm.backend: noop`, there is no automated screening for people
  or faces in frame** — only human reviewers catch this in the review app. Treat an
  unreviewed "accepted" export accordingly.
- The review app has no authentication. Fine for local, single-machine use; revisit if
  multiple people will review concurrently over a network.
- A Crawl4AI page-extraction fallback is deliberately not implemented in this pass
  (it needs a heavy Playwright dependency) — only the `SearchAdapter` interface exists
  so one could be added later without touching anything else.
- `img2dataset` is deliberately not used; see `src/stimulus_harvester/download/fetch.py`
  for why a small custom downloader was a better fit here.

## Legal and licensing notes

- License metadata (raw string + a computed `license_flag` of `open` / `unknown` /
  `restricted`) is preserved for **every** candidate at every stage. Nothing before
  `export` ever drops or hides a candidate because of its license.
- `export --license-filter open_only` is the only point where license restricts what
  leaves the pipeline; `--license-filter all` (the default) exports everything,
  clearly flagged.
- A missing/unrecognized license is marked `unknown`, never silently treated as either
  open or restricted. Images with unknown licenses stay available for internal
  screening in the review app.
- This tool does not verify licenses beyond reading the metadata a source API
  reports. It does not perform any legal review. Treat license fields as a starting
  point for a human to verify before publishing or redistributing any image.
- Only sources whose retrieval terms permit automated, API-based search (Openverse's
  public API; a SearXNG instance you're authorized to query) are used by default. This
  tool does not attempt to bypass paywalls, robots.txt, or site-specific scraping
  restrictions.

## Adding a new search backend

1. Add a config model in `config.py` inheriting `ProviderRequestConfig` and
   wire it into `SearchConfig`.
2. Implement `search/your_backend.py` with a class extending
   `search.http.PagedAdapter`: implement `iter_pages(query, seed_query, *, raw_api_dir)`
   yielding `SearchPage` batches. Use `request_page` for durable request evidence
   and `normalize_items` to retain source ordering and isolate malformed items.
   Preserve the full provider record in `SearchResult.provider_metadata`.
3. Register it in `search/__init__.py::PROVIDERS` behind its own `enabled` flag.
4. Add mocked paging, partial-failure, metadata, and overlap tests mirroring
   `tests/test_discovery_reliability.py`. The inherited `search()` remains available
   for callers that need a collected list, but project ingestion uses pages.

No other module needs to change — `download`, `filter`, `score`, `review`, and
`export` all operate on the common `SearchResult`/DB schema, not on any
backend-specific shape.

## Project layout

```
configs/prison_inside_cell.yaml   task-specific queries/prompts/VLM schema
src/stimulus_harvester/
  cli.py, config.py, models.py, paths.py, logging_utils.py, provenance.py
  search/    Openverse, optional SearXNG/Commons, request evidence, overlap diagnostics
  download/  threaded downloader with full provenance
  filter/    non-semantic quality + duplicate detection
  score/     CLIP ranking (rubric.py is pure math, no torch import) + pluggable VLM
  review/    FastAPI + Jinja2 app, SQLite state
  export/    CSV/JSONL manifests, contact sheet, summary
tests/                             mocked HTTP, generated images, no live network
```

## Testing

```bash
python3 -m pip install -e ".[dev]" && python3 -m pytest -q
```

The full suite runs without the `[clip]` extra installed (no torch/open_clip
required) — all torch/open_clip imports in `score/clip_ranker.py` are lazy, and
`score/rubric.py` (the actual scoring math) has no ML dependency at all. All HTTP is
mocked with `respx`; no test touches the network or loads a real model.

## Development provenance

stimulus-upscaler was conceived and scientifically directed by Cliff Workman as part of a broader effort to make experimental stimulus development more reproducible, auditable, and researcher-controlled.

The core product concept, scientific requirements, workflow, provenance commitments, and acceptance criteria were defined by Workman. Substantial software implementation was carried out with AI coding agents, principally Anthropic Claude Code and OpenAI Codex, under human direction and review.

Claude Code and Codex contributed materially to code generation and refactoring, tests, validation, documentation, and implementation of the application's benchmarking, remote-compute, provenance, and review infrastructure. Their involvement should not be interpreted as independent scientific authorship: the research problem, methodological decisions, and product direction were human-defined, while the coding agents served as implementation collaborators.

The repository is intentionally explicit about this lineage. The goal is neither to obscure AI assistance nor to attribute scientific conception to the tools that implemented it.
