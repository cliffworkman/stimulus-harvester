"""Console + per-run file logging setup."""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER_NAME = "stimulus_harvester"

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(verbosity: int = 0) -> logging.Logger:
    """Set up console logging. verbosity 0=INFO, 1+=DEBUG."""
    level = logging.DEBUG if verbosity >= 1 else logging.INFO
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.StreamHandler)]
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def attach_run_log_file(run_log_path: Path) -> None:
    """Additionally write this process's log output to a per-run log file."""
    logger = logging.getLogger(LOGGER_NAME)
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == run_log_path.resolve():
            return
    handler = logging.FileHandler(run_log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME).getChild(name)
