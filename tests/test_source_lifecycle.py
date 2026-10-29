from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_audio_selector.audio import AudioRouter
from network_audio_selector.snapcast import SnapcastHealth, SnapcastManager, SnapcastUnavailable
from network_audio_selector.source import SourceController, SourcePhase


def test_source_startup_failure_restores_default_sink():
    router = MagicMock(spec=AudioRouter)
    snapcast = MagicMock(spec=SnapcastManager)

    # Snapcast fails to start
    snapcast.start.side_effect = SnapcastUnavailable("Snapserver failed")

    controller = SourceController(router, snapcast)

    with pytest.raises(SnapcastUnavailable):
        controller.start()

    # Must have created and set default sink, but upon snapcast failure, restored it
    router.create_virtual_sink.assert_called_once()
    router.set_default.assert_called_once()
    router.remove_virtual_sink.assert_called_once()
    assert controller.is_running is False

    report = controller.health()
    assert report.phase == SourcePhase.ERROR
    assert "Snapcast transport failed" in report.detail


def test_source_lifecycle_phases():
    router = MagicMock(spec=AudioRouter)
    snapcast = MagicMock(spec=SnapcastManager)

    controller = SourceController(router, snapcast)

    # 1. Idle
    router.sink_exists.return_value = False
    router.is_default.return_value = False
    snapcast.health.return_value = SnapcastHealth(
        installed=True,
        server_running=False,
        encoder_running=False,
        stream_status=None,
        connected_clients=(),
        local_client_running=False,
        detail="stopped",
    )
    report = controller.health()
    assert report.phase == SourcePhase.IDLE

    # 2. Source active only
    router.sink_exists.return_value = True
    router.is_default.return_value = True
    report = controller.health()
    assert report.phase == SourcePhase.SOURCE_ACTIVE

    # 3. Transport active
    snapcast.health.return_value = SnapcastHealth(
        installed=True,
        server_running=True,
        encoder_running=True,
        stream_status="idle",
        connected_clients=(),
        local_client_running=False,
        detail="ready",
    )
    report = controller.health()
    assert report.phase == SourcePhase.TRANSPORT_ACTIVE

    # 4. Receiver connected
    snapcast.health.return_value = SnapcastHealth(
        installed=True,
        server_running=True,
        encoder_running=True,
        stream_status="idle",
        connected_clients=("LivingRoom",),
        local_client_running=False,
        detail="client connected",
    )
    report = controller.health()
    assert report.phase == SourcePhase.RECEIVER_CONNECTED

    # 5. Playback confirmed
    snapcast.health.return_value = SnapcastHealth(
        installed=True,
        server_running=True,
        encoder_running=True,
        stream_status="playing",
        connected_clients=("LivingRoom",),
        local_client_running=False,
        detail="playing",
    )
    report = controller.health()
    assert report.phase == SourcePhase.PLAYBACK_CONFIRMED

    # 6. Error phase when source was started but transport dropped
    controller._wanted_running = True
    snapcast.health.return_value = SnapcastHealth(
        installed=True,
        server_running=False,
        encoder_running=False,
        stream_status=None,
        connected_clients=(),
        local_client_running=False,
        detail="dropped",
    )
    report = controller.health()
    assert report.phase == SourcePhase.ERROR
    assert "Snapcast transport stopped unexpectedly" in report.detail


def test_source_stop_restores_cleanly():
    router = MagicMock(spec=AudioRouter)
    snapcast = MagicMock(spec=SnapcastManager)
    controller = SourceController(router, snapcast)

    controller.stop()
    snapcast.stop_local_client.assert_called_once()
    snapcast.stop.assert_called_once()
    router.remove_virtual_sink.assert_called_once()
    assert controller.is_running is False
