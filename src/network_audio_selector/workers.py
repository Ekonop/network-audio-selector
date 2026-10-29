from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .alexa import (
    AlexaVerificationRequired,
    alexa_browser_sign_in_sync,
    alexa_sign_in_sync,
    alexa_targets_sync,
)
from .discovery import discover_sync
from .snapcast import SnapcastUnavailable
from .source import SourceController


class DiscoveryWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, timeout: float = 5.0) -> None:
        super().__init__()
        self.timeout = timeout

    def run(self) -> None:
        try:
            speakers = discover_sync(timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(list(speakers))


class AlexaTargetsWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            targets = alexa_targets_sync()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(list(targets))


class AlexaSignInWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)
    verification_required = pyqtSignal(str)

    def __init__(
        self,
        identifier: str,
        password: str,
        securitycode: str = "",
        domain: str = "amazon.com",
    ) -> None:
        super().__init__()
        self.identifier = identifier
        self.email = identifier  # backwards compatibility
        self.password = password
        self.securitycode = securitycode
        self.domain = domain

    def run(self) -> None:
        try:
            targets = alexa_sign_in_sync(
                self.identifier, self.password, self.securitycode, domain=self.domain
            )
        except AlexaVerificationRequired as exc:
            self.verification_required.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(list(targets))


class AlexaBrowserSignInWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self, identifier: str, redirect_url: str, domain: str = "amazon.com"
    ) -> None:
        super().__init__()
        self.identifier = identifier
        self.email = identifier  # backwards compatibility
        self.redirect_url = redirect_url
        self.domain = domain

    def run(self) -> None:
        try:
            targets = alexa_browser_sign_in_sync(
                self.identifier, self.redirect_url, domain=self.domain
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(list(targets))


class SourceStartWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller: SourceController) -> None:
        super().__init__()
        self.controller = controller

    def run(self) -> None:
        try:
            report = self.controller.start()
        except SnapcastUnavailable as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)


class SourceStopWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller: SourceController) -> None:
        super().__init__()
        self.controller = controller

    def run(self) -> None:
        try:
            report = self.controller.stop()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)


_ACTIVE_TASKS: set[tuple[QThread, QObject]] = set()


def run_in_thread(worker: QObject) -> tuple[QThread, QObject]:
    thread = QThread()
    worker.moveToThread(thread)
    task = (thread, worker)
    _ACTIVE_TASKS.add(task)
    thread._worker = worker  # Prevent premature GC of worker
    worker._thread = thread

    def cleanup() -> None:
        _ACTIVE_TASKS.discard(task)

    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    if hasattr(worker, "verification_required"):
        getattr(worker, "verification_required").connect(thread.quit)
    thread.finished.connect(cleanup)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
