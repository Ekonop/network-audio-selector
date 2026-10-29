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

    def __init__(self, email: str, password: str, securitycode: str = "") -> None:
        super().__init__()
        self.email = email
        self.password = password
        self.securitycode = securitycode

    def run(self) -> None:
        try:
            targets = alexa_sign_in_sync(self.email, self.password, self.securitycode)
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

    def __init__(self, email: str, redirect_url: str) -> None:
        super().__init__()
        self.email = email
        self.redirect_url = redirect_url

    def run(self) -> None:
        try:
            targets = alexa_browser_sign_in_sync(self.email, self.redirect_url)
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


def run_in_thread(worker: QObject) -> tuple[QThread, QObject]:
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    if hasattr(worker, "verification_required"):
        getattr(worker, "verification_required").connect(thread.quit)
    # Keep worker alive until the thread finishes
    worker.setParent(None)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
