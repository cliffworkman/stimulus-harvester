"""Search backend abstraction.

Every adapter (Openverse, SearXNG, and any future one -- e.g. a Crawl4AI page-
extraction fallback, not implemented in this pass) normalizes its results into the
shared `SearchResult` schema and is responsible for persisting its own raw API
responses under `raw_api_dir` for reproducibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from stimulus_harvester.models import SearchResult


class SearchAdapter(ABC):
    name: str

    @abstractmethod
    def search(self, query: str, seed_query: str, *, raw_api_dir: Path) -> list[SearchResult]:
        """Run one query against this backend and return normalized results."""
        raise NotImplementedError
