"""One read projection's file metadata, never a cross-operation integrity cache."""

from contextlib import contextmanager
from contextvars import ContextVar

from .storage import resolve_path

_files = ContextVar("projection_files", default=None)


@contextmanager
def file_projection():
    token = _files.set({})
    try:
        yield
    finally:
        _files.reset(token)


def file_view(root, value):
    cache = _files.get()
    key = (str(root), str(value))
    if cache is not None and key in cache:
        return cache[key]
    path = resolve_path(root, value)
    result = (path, path.stat())
    if cache is not None:
        cache[key] = result
    return result
