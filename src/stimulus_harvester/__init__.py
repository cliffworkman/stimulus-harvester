"""Local-first stimulus image harvesting, filtering, scoring, and review pipeline.

This top-level module intentionally imports nothing else from the package, so that
`import stimulus_harvester` never requires heavy optional dependencies (torch,
open_clip, fastapi) to be installed. Submodules that need them import lazily.
"""

__version__ = "0.2.0rc1"
