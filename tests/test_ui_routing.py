from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from network_audio_selector.backends import RouteResult
from network_audio_selector.models import Speaker, SpeakerKind
from network_audio_selector.source import HealthReport, SourcePhase
from network_audio_selector.ui import Selector
from network_audio_selector.workers import AlexaSignInWorker


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_ui_route_truthful_when_all_fail(qapp):
    fake_health = HealthReport(
        phase=SourcePhase.IDLE,
        sink_exists=False,
        sink_is_default=False,
        snapserver_running=False,
        ffmpeg_running=False,
        stream_status=None,
        detail="Stopped",
    )
    with patch.object(Selector, "refresh"):
        selector = Selector()
    selector.source = MagicMock()
    selector.source.health.return_value = fake_health
    selector.stream = MagicMock()
    selector.stream.url = "http://127.0.0.1:8000/audio.mp3"

    spk1 = Speaker(id="alexa:1", name="Echo Dot 1", kind=SpeakerKind.ALEXA)
    spk2 = Speaker(id="spotify-connect:2", name="Echo Dot 2", kind=SpeakerKind.SPOTIFY_CONNECT)

    item1 = QListWidgetItem()
    item1.setData(Qt.ItemDataRole.UserRole, spk1)
    item1.setCheckState(Qt.CheckState.Checked)
    selector.devices.addItem(item1)

    item2 = QListWidgetItem()
    item2.setData(Qt.ItemDataRole.UserRole, spk2)
    item2.setCheckState(Qt.CheckState.Checked)
    selector.devices.addItem(item2)

    with patch("network_audio_selector.ui.QMessageBox.warning") as mock_warn:
        selector.route()

        # Must NOT be labeled "Routing active"
        assert selector.route_button.text() == "Routing failed"
        assert "Routing failed" in selector.status.text()
        mock_warn.assert_called_once()

    selector.close()


def test_ui_route_truthful_when_mixture_of_success_and_failure(qapp):
    fake_health = HealthReport(
        phase=SourcePhase.TRANSPORT_ACTIVE,
        sink_exists=True,
        sink_is_default=True,
        snapserver_running=True,
        ffmpeg_running=True,
        stream_status="playing",
        detail="Active",
    )
    with patch.object(Selector, "refresh"):
        selector = Selector()
    selector.source = MagicMock()
    selector.source.health.return_value = fake_health
    selector.stream = MagicMock()
    selector.stream.url = "http://127.0.0.1:8000/audio.mp3"

    chromecast_spk = Speaker(id="chromecast:1", name="Living Room TV", kind=SpeakerKind.CHROMECAST)
    unsupported_spk = Speaker(id="alexa:1", name="Echo Dot", kind=SpeakerKind.ALEXA)

    item1 = QListWidgetItem()
    item1.setData(Qt.ItemDataRole.UserRole, chromecast_spk)
    item1.setCheckState(Qt.CheckState.Checked)
    selector.devices.addItem(item1)

    item2 = QListWidgetItem()
    item2.setData(Qt.ItemDataRole.UserRole, unsupported_spk)
    item2.setCheckState(Qt.CheckState.Checked)
    selector.devices.addItem(item2)

    mock_chromecast_backend = MagicMock()
    mock_chromecast_backend.route.return_value = RouteResult(chromecast_spk, True, "Streaming ok")

    mock_unsupported_backend = MagicMock()
    mock_unsupported_backend.route.return_value = RouteResult(unsupported_spk, False, "Echo unsupported")

    def mock_backend_for(spk):
        return mock_chromecast_backend if spk.kind == SpeakerKind.CHROMECAST else mock_unsupported_backend

    with patch("network_audio_selector.ui.backend_for", side_effect=mock_backend_for):
        selector.route()

        assert selector.route_button.text() == "Routing active (1/2)"
        assert "Living Room TV" in selector.status.text()
        assert "Echo unsupported" in selector.status.text()

    selector.close()


def test_alexa_worker_runs_in_background(qapp):
    with patch("network_audio_selector.workers.alexa_sign_in_sync") as mock_signin:
        mock_signin.return_value = [
            Speaker(id="alexa:1", name="Echo Dot", kind=SpeakerKind.ALEXA)
        ]

        worker = AlexaSignInWorker("test@example.com", "pass123", "")
        finished_mock = MagicMock()
        worker.finished.connect(finished_mock)

        worker.run()
        finished_mock.assert_called_once()
        targets = finished_mock.call_args[0][0]
        assert len(targets) == 1
        assert targets[0].name == "Echo Dot"


def test_ui_preflight_does_not_touch_source_when_all_unsupported(qapp):
    with patch.object(Selector, "refresh"):
        selector = Selector()
    selector.source = MagicMock()
    selector.stream = None

    spk = Speaker(id="alexa:1", name="Echo Dot", kind=SpeakerKind.ALEXA)
    item = QListWidgetItem()
    item.setData(Qt.ItemDataRole.UserRole, spk)
    item.setCheckState(Qt.CheckState.Checked)
    selector.devices.addItem(item)

    with patch("network_audio_selector.ui.QMessageBox.warning"):
        selector.route()

    # Audio source and virtual sink must NOT have been started
    selector.source.start.assert_not_called()
    assert selector.stream is None
    selector.close()


def test_ui_route_transactional_rollback_when_backend_fails(qapp):
    fake_health = HealthReport(
        phase=SourcePhase.TRANSPORT_ACTIVE,
        sink_exists=True,
        sink_is_default=True,
        snapserver_running=True,
        ffmpeg_running=True,
        stream_status="playing",
        detail="Active",
    )
    with patch.object(Selector, "refresh"):
        selector = Selector()
    selector.source = MagicMock()
    selector.source.is_running = False
    def mock_start():
        selector.source.is_running = True
        return fake_health
    selector.source.start.side_effect = mock_start
    selector.source.health.return_value = fake_health

    spk = Speaker(
        id="chromecast:1",
        name="Living Room TV",
        kind=SpeakerKind.CHROMECAST,
        address="192.168.1.50",
    )
    item = QListWidgetItem()
    item.setData(Qt.ItemDataRole.UserRole, spk)
    item.setCheckState(Qt.CheckState.Checked)
    selector.devices.addItem(item)

    mock_chromecast_backend = MagicMock()
    mock_chromecast_backend.can_route.return_value = True
    mock_chromecast_backend.route.return_value = RouteResult(spk, False, "Connection timed out")

    with patch("network_audio_selector.ui.backend_for", return_value=mock_chromecast_backend), \
         patch("network_audio_selector.ui.AudioStream") as mock_stream_cls, \
         patch("network_audio_selector.ui.QMessageBox.warning"):
        mock_stream = MagicMock()
        mock_stream.start.return_value = "http://192.168.1.10:8000/audio.mp3"
        mock_stream_cls.return_value = mock_stream

        selector.route()

        # Since backend failed and source was not running prior to route, rollback must stop source & stream
        selector.source.stop.assert_called_once()
        mock_stream.stop.assert_called_once()
        assert selector.route_button.text() == "Routing failed"

    selector.close()
