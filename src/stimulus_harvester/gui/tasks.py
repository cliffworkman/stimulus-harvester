"""Single-task session controller with queued Qt signals and truthful cancellation."""

import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot
from .adapter import Adapter


class Worker(QThread):
    progress = Signal(object)
    reply = Signal(object)

    def __init__(self, adapter, action, path, payload, parent=None):
        super().__init__(parent)
        self.adapter, self.action, self.path, self.payload = adapter, action, path, payload
        self.cancel_requested = threading.Event()

    def run(self):
        self.reply.emit(
            self.adapter.execute(
                self.action,
                self.path,
                self.payload,
                self.progress.emit,
                self.cancel_requested.is_set,
            )
        )


class Session(QObject):
    changed = Signal(object)
    completed = Signal(object)
    progress = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, parent=None, adapter=None):
        super().__init__(parent)
        self.adapter = adapter or Adapter()
        self.state = None
        self.worker = None
        self.pending = None

    @property
    def busy(self):
        return self.worker is not None

    def submit(self, action, payload=None, *, path=None):
        if self.busy:
            return False
        selected = path if path is not None else self.state.path if self.state else None
        self.worker = Worker(self.adapter, action, selected, payload or {}, self)
        self.worker.progress.connect(self.progress)
        self.worker.reply.connect(self._receive)
        self.worker.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self.worker.start()
        return True

    @Slot(object)
    def _receive(self, reply):
        self.pending = reply

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        reply, self.pending = self.pending, None
        if reply and reply.workspace:
            self.state = reply.workspace
            self.changed.emit(self.state)
        self.busy_changed.emit(False)
        if reply:
            self.completed.emit(reply)

    def cancel_before_start(self):
        if self.worker:
            self.worker.cancel_requested.set()
