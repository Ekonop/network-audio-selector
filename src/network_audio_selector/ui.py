from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .alexa import (
    AlexaConfig,
    alexa_login_url,
)
from .audio import AudioRouter
from .backends import backend_for
from .installer import SnapcastInstallError, install_snapcast
from .models import Speaker, SpeakerKind
from .snapcast import SnapcastManager, SnapcastUnavailable
from .source import PHASE_LABELS, HealthReport, SourceController, SourcePhase
from .stream import AudioStream, StreamMode
from .workers import (
    AlexaBrowserSignInWorker,
    AlexaSignInWorker,
    AlexaTargetsWorker,
    DiscoveryWorker,
    SourceStartWorker,
    SourceStopWorker,
    run_in_thread,
)


STYLE = """
QWidget {
    background: #101318;
    color: #eef2f6;
    font-family: "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 14px;
}
QFrame#panel {
    background: #171c23;
    border: 1px solid #29313c;
    border-radius: 12px;
}
QLabel#eyebrow {
    color: #8d99a8;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#title {
    color: #ffffff;
    font-size: 27px;
    font-weight: 700;
}
QLabel#subtitle, QLabel#hint {
    color: #aab4c0;
}
QLabel#status {
    background: #202832;
    border: 1px solid #303b48;
    border-radius: 8px;
    color: #c9d3dd;
    padding: 9px 12px;
}
QLabel#health {
    background: #12171d;
    border: 1px solid #29313c;
    border-radius: 8px;
    color: #c9d3dd;
    padding: 10px 12px;
    font-family: "Noto Sans Mono", "DejaVu Sans Mono", monospace;
    font-size: 12px;
}
QListWidget {
    background: #12171d;
    border: 1px solid #29313c;
    border-radius: 10px;
    outline: none;
    padding: 5px;
}
QListWidget::item {
    border-radius: 7px;
    padding: 12px 10px;
    margin: 2px 0;
}
QListWidget::item:hover {
    background: #202934;
}
QListWidget::item:selected {
    background: #244b46;
    color: #ffffff;
}
QPushButton {
    background: #27313d;
    border: 1px solid #3a4653;
    border-radius: 8px;
    color: #eef2f6;
    min-height: 36px;
    padding: 0 15px;
}
QPushButton:hover {
    background: #33404e;
}
QPushButton:pressed {
    background: #1d2732;
}
QPushButton#primary {
    background: #3ab39d;
    border-color: #3ab39d;
    color: #071411;
    font-weight: 700;
}
QPushButton#primary:hover {
    background: #50c8b1;
}
QPushButton#quiet {
    background: transparent;
    border-color: #36414e;
}
QPushButton#quiet:hover {
    background: #202934;
}
QPushButton#danger {
    background: #3a2730;
    border-color: #5a3844;
    color: #f0c2c8;
}
QPushButton#danger:hover {
    background: #4a303a;
}
"""


class Selector(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("window")
        self.setWindowTitle("Network Audio")
        self.setMinimumSize(520, 720)
        self.resize(560, 820)
        self.router = AudioRouter()
        self.snapcast = SnapcastManager()
        self.source = SourceController(self.router, self.snapcast)
        self.stream: AudioStream | None = None
        self._busy = False
        self._threads: list[object] = []

        self.status = QLabel("Ready to find speakers on your network.")
        self.status.setObjectName("status")
        self.health_label = QLabel()
        self.health_label.setObjectName("health")
        self.health_label.setWordWrap(True)
        self.devices = QListWidget()
        self.devices.setAccessibleName("Available network speakers")
        self.devices.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.devices.setUniformItemSizes(True)
        self.spotify_devices = QListWidget()
        self.spotify_devices.setAccessibleName("Spotify Connect receivers")
        self.spotify_devices.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.spotify_devices.setMaximumHeight(110)
        self.alexa_devices = QListWidget()
        self.alexa_devices.setAccessibleName("Alexa account devices and groups")
        self.alexa_devices.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.alexa_devices.setMaximumHeight(150)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("quiet")
        self.refresh_button.clicked.connect(self.refresh)
        self.alexa_button = QPushButton("Connect Alexa")
        self.alexa_button.setObjectName("quiet")
        self.alexa_button.clicked.connect(self.load_alexa_targets)
        self.start_source_button = QPushButton("Start source")
        self.start_source_button.setObjectName("primary")
        self.start_source_button.clicked.connect(self.start_source)
        self.stop_source_button = QPushButton("Stop source")
        self.stop_source_button.setObjectName("danger")
        self.stop_source_button.setEnabled(False)
        self.stop_source_button.clicked.connect(self.stop_source)
        self.local_client_button = QPushButton("Start local test client")
        self.local_client_button.setObjectName("quiet")
        self.local_client_button.setEnabled(False)
        self.local_client_button.clicked.connect(self.toggle_local_client)
        self.probe_button = QPushButton("Probe tone")
        self.probe_button.setObjectName("quiet")
        self.probe_button.setEnabled(False)
        self.probe_button.clicked.connect(self.probe_source)
        self.route_button = QPushButton("Start routing")
        self.route_button.setObjectName("quiet")
        self.route_button.setEnabled(False)
        self.route_button.clicked.connect(self.route)
        self.install_snapcast_button = QPushButton("Install Snapcast")
        self.install_snapcast_button.setObjectName("quiet")
        self.install_snapcast_button.clicked.connect(self.install_snapcast)

        self._build_layout()
        self.setStyleSheet(STYLE)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(2000)
        self._health_timer.timeout.connect(self.refresh_health)
        self._health_timer.start()
        self.refresh_health()
        self.refresh()

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(16)

        eyebrow = QLabel("SYSTEM AUDIO")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Network Audio")
        title.setObjectName("title")
        subtitle = QLabel(
            "Start the source to capture Linux audio, then connect Snapclients "
            "or supported network receivers."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(eyebrow)
        root.addWidget(title)
        root.addWidget(subtitle)

        source_panel = QFrame()
        source_panel.setObjectName("panel")
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(16, 14, 16, 14)
        source_layout.setSpacing(8)
        source_title = QLabel("Audio source")
        source_title.setStyleSheet("font-weight: 700; color: #ffffff;")
        self.source_hint = QLabel(
            "Start source creates the PipeWire sink, makes it the default output, "
            "and starts Snapcast transport. Health checks stay separate until a "
            "receiver connects and playback is confirmed."
        )
        self.source_hint.setObjectName("hint")
        self.source_hint.setWordWrap(True)
        source_actions = QHBoxLayout()
        source_actions.setSpacing(8)
        source_actions.addWidget(self.start_source_button)
        source_actions.addWidget(self.stop_source_button)
        source_actions.addWidget(self.local_client_button)
        source_actions.addWidget(self.probe_button)
        source_layout.addWidget(source_title)
        source_layout.addWidget(self.source_hint)
        source_layout.addLayout(source_actions)
        source_layout.addWidget(self.health_label)
        root.addWidget(source_panel)

        devices_header = QHBoxLayout()
        devices_title = QLabel("Available speakers")
        devices_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        devices_header.addWidget(devices_title)
        devices_header.addStretch()
        devices_header.addWidget(self.alexa_button)
        devices_header.addWidget(self.refresh_button)
        root.addLayout(devices_header)
        root.addWidget(self.devices, 1)

        spotify_header = QHBoxLayout()
        spotify_title = QLabel("Spotify Connect receivers")
        spotify_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        spotify_header.addWidget(spotify_title)
        spotify_header.addStretch()
        root.addLayout(spotify_header)
        spotify_note = QLabel(
            "Echo devices can appear here for Spotify playback, but they cannot "
            "receive arbitrary Linux audio. Use Snapcast for system audio."
        )
        spotify_note.setObjectName("hint")
        spotify_note.setWordWrap(True)
        root.addWidget(spotify_note)
        root.addWidget(self.spotify_devices)

        alexa_header = QHBoxLayout()
        alexa_title = QLabel("Alexa account targets")
        alexa_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        alexa_header.addWidget(alexa_title)
        alexa_header.addStretch()
        root.addLayout(alexa_header)
        alexa_note = QLabel(
            "Use Alexa groups for account controls. They are not raw Linux audio outputs."
        )
        alexa_note.setObjectName("hint")
        alexa_note.setWordWrap(True)
        root.addWidget(alexa_note)
        root.addWidget(self.alexa_devices)

        hint = QLabel("Select one or more speakers, then start routing.")
        hint.setObjectName("hint")
        root.addWidget(hint)
        root.addWidget(self.status)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.install_snapcast_button)
        actions.addWidget(self.route_button)
        root.addLayout(actions)

    def _add_speaker(self, speaker: Speaker) -> None:
        item = QListWidgetItem()
        label = speaker.name
        if speaker.note:
            label = f"{label}  ·  {speaker.note}"
        item.setText(label)
        item.setData(Qt.ItemDataRole.UserRole, speaker)
        if speaker.kind is SpeakerKind.ALEXA:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.alexa_devices.addItem(item)
        elif speaker.kind is SpeakerKind.SPOTIFY_CONNECT:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.spotify_devices.addItem(item)
        else:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.devices.addItem(item)

    def _run_worker(self, worker: QObject) -> QThread:
        thread, _ = run_in_thread(worker)
        self._threads.append(thread)
        thread.finished.connect(
            lambda: self._threads.remove(thread) if thread in self._threads else None
        )
        return thread

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.refresh_button.setEnabled(False)
        self.devices.clear()
        self.spotify_devices.clear()
        self._set_status("Looking for speakers on your network…", busy=True)
        worker = DiscoveryWorker()
        worker.finished.connect(self._on_discovery_finished)
        worker.failed.connect(self._on_discovery_failed)
        self._run_worker(worker)

    def _on_discovery_finished(self, speakers: list) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)
        self.devices.clear()
        self.spotify_devices.clear()
        for speaker in speakers:
            if speaker.kind is SpeakerKind.ALEXA:
                continue
            self._add_speaker(speaker)
        local_speakers = [
            speaker
            for speaker in speakers
            if speaker.kind not in {SpeakerKind.ALEXA, SpeakerKind.SPOTIFY_CONNECT}
        ]
        if local_speakers:
            self.route_button.setEnabled(True)
            self._set_status(f"{len(local_speakers)} local receiver(s) ready to select.")
        else:
            self.route_button.setEnabled(False)
            empty = QListWidgetItem(
                "No local receivers found\n"
                "Use Connect Alexa for Echo devices, or check that Chromecast, "
                "AirPlay, or UPnP speakers are on this network."
            )
            empty.setForeground(Qt.GlobalColor.gray)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.devices.addItem(empty)
            self._set_status(
                "Nothing is selected yet. Connect Alexa or refresh local speakers."
            )
        if self.spotify_devices.count():
            self._set_status(
                f"Detected {self.spotify_devices.count()} Spotify Connect receiver(s). "
                "Snapcast is required for Linux system audio."
            )

    def _on_discovery_failed(self, message: str) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)
        self._set_status(f"Discovery failed: {message}", error=True)

    def load_alexa_targets(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Connect Alexa account")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        info = QLabel(
            "Enter your Amazon account email or mobile phone number to connect your Alexa devices and groups."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        id_input = QLineEdit()
        saved_id = (
            os.environ.get("NETWORK_AUDIO_ALEXA_EMAIL", "")
            or os.environ.get("NETWORK_AUDIO_ALEXA_PHONE", "")
            or os.environ.get("NETWORK_AUDIO_ALEXA_IDENTIFIER", "")
        )
        id_input.setText(saved_id)
        id_input.setPlaceholderText("e.g. user@example.com or +919876543210")
        form.addRow("Email or phone number:", id_input)

        region_combo = QComboBox()
        regions = [
            ("amazon.in", "India (amazon.in)"),
            ("amazon.com", "United States / Global (amazon.com)"),
            ("amazon.co.uk", "United Kingdom (amazon.co.uk)"),
            ("amazon.de", "Germany (amazon.de)"),
            ("amazon.ca", "Canada (amazon.ca)"),
            ("amazon.fr", "France (amazon.fr)"),
            ("amazon.co.jp", "Japan (amazon.co.jp)"),
            ("amazon.com.au", "Australia (amazon.com.au)"),
        ]
        for domain, label in regions:
            region_combo.addItem(label, domain)

        current_domain = os.environ.get("NETWORK_AUDIO_ALEXA_DOMAIN", "amazon.in")
        found_idx = region_combo.findData(current_domain)
        if found_idx >= 0:
            region_combo.setCurrentIndex(found_idx)
        form.addRow("Amazon region:", region_combo)
        layout.addLayout(form)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        identifier = id_input.text().strip()
        if not identifier:
            return

        selected_domain = str(region_combo.currentData())
        os.environ["NETWORK_AUDIO_ALEXA_IDENTIFIER"] = identifier
        os.environ["NETWORK_AUDIO_ALEXA_EMAIL"] = identifier
        os.environ["NETWORK_AUDIO_ALEXA_PHONE"] = identifier
        os.environ["NETWORK_AUDIO_ALEXA_DOMAIN"] = selected_domain

        config = AlexaConfig(identifier=identifier, domain=selected_domain)
        if not config.cookie_file.exists():
            choice = QMessageBox(self)
            choice.setWindowTitle("Set up Alexa")
            choice.setIcon(QMessageBox.Icon.Information)
            choice.setText("Network Audio needs an Alexa session before it can load groups.")
            choice.setInformativeText(
                "Choose how to connect. Browser login is recommended. "
                "The Alexa app option imports an exported alexapy session; "
                "the Alexa QR option completes login in Amazon’s browser page."
            )
            sign_in_button = choice.addButton(
                "Login with Amazon", QMessageBox.ButtonRole.AcceptRole
            )
            alexa_app_button = choice.addButton(
                "Use Alexa app QR code", QMessageBox.ButtonRole.ActionRole
            )
            import_button = choice.addButton(
                "Use Alexa app session", QMessageBox.ButtonRole.ActionRole
            )
            choice.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            if choice.clickedButton() is sign_in_button:
                self._browser_sign_in_alexa(identifier, domain=selected_domain)
                return
            if choice.clickedButton() is alexa_app_button:
                self._browser_sign_in_alexa(identifier, domain=selected_domain, use_app=True)
                return
            if choice.clickedButton() is not import_button:
                return
            if not self._import_alexa_app_session(config):
                return
        self._set_status("Connecting to Alexa account…", busy=True)
        self.alexa_button.setEnabled(False)
        worker = AlexaTargetsWorker()
        worker.finished.connect(self._on_alexa_targets)
        worker.failed.connect(self._on_alexa_failed)
        self._run_worker(worker)

    def _on_alexa_targets(self, targets: list) -> None:
        self.alexa_button.setEnabled(True)
        self.alexa_devices.clear()
        for target in targets:
            self._add_speaker(target)
        self._set_status(f"Loaded {len(targets)} Alexa device/group target(s).")

    def _on_alexa_failed(self, message: str) -> None:
        self.alexa_button.setEnabled(True)
        self._set_status(f"Alexa account unavailable: {message}", error=True)
        QMessageBox.warning(self, "Alexa account unavailable", message)

    def _import_alexa_app_session(self, config: AlexaConfig) -> bool:
        info = QMessageBox(self)
        info.setWindowTitle("Use Alexa app session")
        info.setIcon(QMessageBox.Icon.Information)
        info.setText("Export an Alexa session from a trusted device first.")
        info.setInformativeText(
            "Select the resulting alexapy .cookies, .pickle, or .txt file. "
            "Network Audio copies it into its protected local session folder. "
            "The Alexa mobile app itself cannot send a login token directly to Linux."
        )
        choose = info.addButton("Choose session file", QMessageBox.ButtonRole.AcceptRole)
        info.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        info.exec()
        if info.clickedButton() is not choose:
            return False
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Alexa app session export",
            str(Path.home()),
            "Alexa session (*.cookies *.pickle *.txt);;All files (*)",
        )
        if not source:
            return False
        try:
            config.import_cookie(Path(source))
        except Exception as exc:
            self._set_status(f"Could not import Alexa session: {exc}", error=True)
            QMessageBox.warning(self, "Session import failed", str(exc))
            return False
        return True

    def _browser_sign_in_alexa(
        self, identifier: str, domain: str = "amazon.com", *, use_app: bool = False
    ) -> None:
        if use_app:
            prompt = (
                "Amazon login will open in your browser.\n\n"
                "1. Click “scan the QR code here”.\n"
                "2. Scan it with the Alexa app on your phone.\n"
                "3. Complete approval in the Alexa app.\n"
                "4. Copy the final Amazon address-bar URL and paste it back here."
            )
        else:
            prompt = (
                "Amazon login will open in your browser.\n\n"
                "Complete sign-in and verification, then copy the final address-bar "
                "URL and paste it back here."
            )
        try:
            url, code_verifier, uuid = alexa_login_url(identifier, domain=domain)
        except Exception as exc:
            self._set_status(f"Could not create Amazon login: {exc}", error=True)
            return
        answer = QMessageBox(self)
        answer.setWindowTitle("Continue with Amazon")
        answer.setIcon(QMessageBox.Icon.Information)
        answer.setText(prompt)
        start = answer.addButton("Open Amazon login", QMessageBox.ButtonRole.AcceptRole)
        answer.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        answer.exec()
        if answer.clickedButton() is not start:
            return
        import webbrowser

        webbrowser.open(url)
        redirect_url, accepted = QInputDialog.getText(
            self,
            "Finish Alexa connection",
            f"{prompt}\n\nPaste the complete Amazon URL:",
            QLineEdit.EchoMode.Normal,
        )
        if not accepted or not redirect_url.strip():
            return
        self._set_status("Completing Alexa connection in background…", busy=True)
        self.alexa_button.setEnabled(False)
        worker = AlexaBrowserSignInWorker(
            identifier,
            redirect_url.strip(),
            domain=domain,
            code_verifier=code_verifier,
            uuid=uuid,
        )
        worker.finished.connect(self._on_alexa_targets)
        worker.failed.connect(self._on_alexa_failed)
        self._run_worker(worker)

    def _sign_in_alexa(self, identifier: str, domain: str = "amazon.com") -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Sign in with Amazon")
        form = QFormLayout(dialog)
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Amazon password", password)
        note = QLabel(
            "Amazon will request a verification code only if needed. "
            "Your password is used only for this sign-in and is not saved."
        )
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not password.text():
            return
        self._start_alexa_credential_signin(
            identifier, password.text(), domain=domain
        )

    def _start_alexa_credential_signin(
        self,
        identifier: str,
        password: str,
        code: str = "",
        domain: str = "amazon.com",
    ) -> None:
        self._set_status("Signing in to Amazon in background…", busy=True)
        self.alexa_button.setEnabled(False)
        worker = AlexaSignInWorker(
            identifier, password, code, domain=domain
        )
        worker.finished.connect(self._on_alexa_targets)
        worker.failed.connect(self._on_alexa_failed)
        worker.verification_required.connect(
            lambda msg: self._on_alexa_verification_required(
                identifier, password, msg, domain=domain
            )
        )
        self._run_worker(worker)

    def _on_alexa_verification_required(
        self,
        identifier: str,
        password: str,
        message: str,
        domain: str = "amazon.com",
    ) -> None:
        self.alexa_button.setEnabled(True)
        code, accepted = QInputDialog.getText(
            self,
            "Amazon verification",
            f"{message}\n\nEnter the code Amazon sent you:",
            QLineEdit.EchoMode.Normal,
        )
        if not accepted or not code.strip():
            self._set_status("Amazon verification was cancelled.", error=True)
            return
        self._start_alexa_credential_signin(
            identifier, password, code.strip(), domain=domain
        )

    def start_source(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.start_source_button.setEnabled(False)
        self._set_status("Starting virtual sink and Snapcast transport…", busy=True)
        worker = SourceStartWorker(self.source)
        worker.finished.connect(self._on_source_started)
        worker.failed.connect(self._on_source_start_failed)
        self._run_worker(worker)

    def _on_source_started(self, report: object) -> None:
        self._busy = False
        assert isinstance(report, HealthReport)
        self._apply_health(report)
        self._set_status(report.detail)
        self.source_hint.setText(
            "Source is running. New app audio goes into Network Audio. "
            "Connect a Snapclient or start the local test client to confirm playback."
        )

    def _on_source_start_failed(self, message: str) -> None:
        self._busy = False
        self.start_source_button.setEnabled(True)
        self.refresh_health()
        self._set_status(message, error=True)
        if "not installed" in message.lower():
            choice = QMessageBox(self)
            choice.setWindowTitle("Snapcast required")
            choice.setIcon(QMessageBox.Icon.Information)
            choice.setText(message)
            choice.setInformativeText(
                "Network Audio can install the native Snapcast package using "
                "your Linux package manager."
            )
            install = choice.addButton(
                "Install Snapcast", QMessageBox.ButtonRole.AcceptRole
            )
            choice.addButton("Close", QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            if choice.clickedButton() is install:
                self.install_snapcast()

    def stop_source(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.stop_source_button.setEnabled(False)
        self.local_client_button.setEnabled(False)
        self._set_status("Stopping source and restoring the previous output…", busy=True)
        worker = SourceStopWorker(self.source)
        worker.finished.connect(self._on_source_stopped)
        worker.failed.connect(self._on_source_stop_failed)
        self._run_worker(worker)

    def _on_source_stopped(self, report: object) -> None:
        self._busy = False
        assert isinstance(report, HealthReport)
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
            self.route_button.setText("Start routing")
        self._apply_health(report)
        self.source_hint.setText(
            "Start source creates the PipeWire sink, makes it the default output, "
            "and starts Snapcast transport. Health checks stay separate until a "
            "receiver connects and playback is confirmed."
        )
        self._set_status("Source stopped. Physical audio output restored.")

    def _on_source_stop_failed(self, message: str) -> None:
        self._busy = False
        self.refresh_health()
        self._set_status(f"Could not stop source cleanly: {message}", error=True)

    def toggle_local_client(self) -> None:
        report = self.source.health()
        try:
            if report.local_client_running:
                self.source.stop_local_test_client()
                self._set_status("Local Snapclient test client stopped.")
            else:
                message = self.source.start_local_test_client()
                self._set_status(message)
        except SnapcastUnavailable as exc:
            self._set_status(str(exc), error=True)
        self.refresh_health()

    def probe_source(self) -> None:
        self.probe_button.setEnabled(False)
        self._set_status("Playing probe tone and sampling Snapcast health…", busy=True)
        try:
            report = self.source.probe()
        except SnapcastUnavailable as exc:
            self._set_status(str(exc), error=True)
            self.refresh_health()
            return
        self._apply_health(report)
        if report.phase is SourcePhase.PLAYBACK_CONFIRMED:
            self._set_status("Playback confirmed while the probe tone was playing.")
        elif report.stream_status == "playing":
            self._set_status(
                "Probe tone reached Snapcast (stream=playing). Connect a Snapclient to confirm receivers."
            )
        else:
            self._set_status(report.detail, error=True)

    def install_snapcast(self) -> None:
        self._set_status("Installing Snapcast through the system package manager…", busy=True)
        try:
            message = install_snapcast()
        except SnapcastInstallError as exc:
            self._set_status(str(exc), error=True)
            QMessageBox.warning(self, "Snapcast installation failed", str(exc))
            return
        self._set_status(message)
        self.install_snapcast_button.setText("Snapcast installed")

    def route(self) -> None:
        selected = [
            self.devices.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.devices.count())
            if self.devices.item(index).checkState() == Qt.CheckState.Checked
            and self.devices.item(index).data(Qt.ItemDataRole.UserRole) is not None
        ]
        if not selected:
            self._set_status("Select at least one speaker before starting.", error=True)
            return

        # Preflight: Check if any selected speaker can be routed
        routable = [s for s in selected if backend_for(s).can_route(s)]
        if not routable:
            unsupported_reasons = "\n".join(
                f"• {s.name} ({s.kind.value}): {backend_for(s).route(s).message}"
                for s in selected
            )
            self.route_button.setText("Routing failed")
            self._set_status(
                "Routing failed: None of the selected receivers support arbitrary Linux desktop audio.",
                error=True,
            )
            QMessageBox.warning(
                self,
                "Unsupported Receivers",
                "None of the selected receivers can receive arbitrary Linux desktop audio:\n\n"
                f"{unsupported_reasons}\n\n"
                "Tip: Use Snapcast with compatible clients for arbitrary system audio.",
            )
            return

        # Check if remote LAN streaming is needed
        chromecast_targets = [s for s in selected if s.kind is SpeakerKind.CHROMECAST]
        receiver_address = None
        for c in chromecast_targets:
            if c.address and c.address not in ("127.0.0.1", "localhost", "::1"):
                receiver_address = c.address
                break

        source_was_running = self.source.is_running
        stream_was_running = self.stream is not None

        try:
            if not self.source.is_running:
                self.source.start()
            if self.stream is None:
                if receiver_address:
                    self.stream = AudioStream(
                        mode=StreamMode.LAN,
                        receiver_address=receiver_address,
                    )
                else:
                    self.stream = AudioStream(mode=StreamMode.LOOPBACK)
                stream_url = self.stream.start()
            else:
                stream_url = self.stream.url
            results = [backend_for(speaker).route(speaker, stream_url) for speaker in selected]
        except Exception as exc:
            # Transactional rollback: stop resources created by this operation
            if not stream_was_running and self.stream is not None:
                self.stream.stop()
                self.stream = None
            if not source_was_running and self.source.is_running:
                self.source.stop()

            self.route_button.setText("Routing failed")
            self._set_status(f"Routing failed: {exc}", error=True)
            self.refresh_health()
            return

        succeeded = [result for result in results if result.ok]
        failed = [result for result in results if not result.ok]

        if not succeeded:
            # Complete failure: rollback resources started by this operation
            if not stream_was_running and self.stream is not None:
                self.stream.stop()
                self.stream = None
            if not source_was_running and self.source.is_running:
                self.source.stop()

            self.route_button.setText("Routing failed")
            failure_lines = [f"{r.speaker.name}: {r.message}" for r in failed]
            self._set_status("Routing failed: " + " · ".join(failure_lines), error=True)
            QMessageBox.warning(
                self,
                "Routing Failed",
                "None of the selected speakers could be routed:\n\n"
                + "\n\n".join(f"• {r.speaker.name} ({r.speaker.kind.value}):\n  {r.message}" for r in failed),
            )
        elif failed:
            self.route_button.setText(f"Routing active ({len(succeeded)}/{len(selected)})")
            succ_names = ", ".join(r.speaker.name for r in succeeded)
            fail_details = "; ".join(f"{r.speaker.name}: {r.message}" for r in failed)
            self._set_status(
                f"Routing active to {succ_names}. Failed: {fail_details}",
                error=True,
            )
        else:
            self.route_button.setText("Routing active")
            self._set_status(f"Routing audio to {len(selected)} selected speaker(s).")
        self.refresh_health()

    def refresh_health(self) -> None:
        if self._busy:
            return
        self._apply_health(self.source.health())

    def _apply_health(self, report: HealthReport) -> None:
        lines = [f"Phase: {PHASE_LABELS[report.phase]}"]
        for phase, ok, text in report.steps:
            mark = "OK" if ok else "--"
            lines.append(f"[{mark}] {PHASE_LABELS[phase]} — {text}")
        if report.stream_status:
            lines.append(f"Stream status: {report.stream_status}")
        if report.connected_clients:
            lines.append("Clients: " + ", ".join(report.connected_clients))
        elif report.phase in {
            SourcePhase.TRANSPORT_ACTIVE,
            SourcePhase.SOURCE_ACTIVE,
        }:
            lines.append("Clients: none")
        if report.local_client_running:
            lines.append("Local test client: running")
        lines.append(report.detail)
        self.health_label.setText("\n".join(lines))

        running = report.phase not in {SourcePhase.IDLE, SourcePhase.ERROR} or self.source.is_running
        transport_ready = report.snapserver_running
        self.start_source_button.setEnabled(not self.source.is_running and not self._busy)
        self.stop_source_button.setEnabled(running and not self._busy)
        self.local_client_button.setEnabled(transport_ready and not self._busy)
        self.probe_button.setEnabled(transport_ready and not self._busy)
        self.local_client_button.setText(
            "Stop local test client"
            if report.local_client_running
            else "Start local test client"
        )
        if self.source.is_running:
            self.start_source_button.setText("Source active")
        else:
            self.start_source_button.setText("Start source")

    def _set_status(self, text: str, *, error: bool = False, busy: bool = False) -> None:
        self.status.setText(text)
        color = "#f0a6a6" if error else "#c9d3dd"
        if busy:
            color = "#f1ca86"
        self.status.setStyleSheet(f"color: {color};")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._health_timer.stop()
        for thread in list(self._threads):
            try:
                if hasattr(thread, "isRunning") and thread.isRunning():
                    thread.quit()
                    thread.wait(500)
            except RuntimeError:
                pass
        self._threads.clear()
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        try:
            self.source.stop()
        except Exception:
            self.snapcast.stop()
            self.router.remove_virtual_sink()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Network Audio")
    app.setFont(QFont("Noto Sans", 10))
    window = Selector()
    window.show()
    raise SystemExit(app.exec())
