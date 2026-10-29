from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from .audio import AudioRouter
from .snapcast import SnapcastManager, SnapcastUnavailable


class SourcePhase(StrEnum):
    IDLE = "idle"
    SOURCE_ACTIVE = "source_active"
    TRANSPORT_ACTIVE = "transport_active"
    RECEIVER_CONNECTED = "receiver_connected"
    PLAYBACK_CONFIRMED = "playback_confirmed"
    ERROR = "error"


PHASE_LABELS = {
    SourcePhase.IDLE: "Idle",
    SourcePhase.SOURCE_ACTIVE: "Source active",
    SourcePhase.TRANSPORT_ACTIVE: "Transport active",
    SourcePhase.RECEIVER_CONNECTED: "Receiver connected",
    SourcePhase.PLAYBACK_CONFIRMED: "Playback confirmed",
    SourcePhase.ERROR: "Error",
}


@dataclass(frozen=True)
class HealthReport:
    phase: SourcePhase
    sink_exists: bool
    sink_is_default: bool
    snapserver_running: bool
    ffmpeg_running: bool
    stream_status: str | None
    connected_clients: tuple[str, ...] = ()
    local_client_running: bool = False
    detail: str = ""
    steps: tuple[tuple[SourcePhase, bool, str], ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return PHASE_LABELS[self.phase]


class SourceController:
    """Own the source → transport → receiver lifecycle as distinct phases."""

    def __init__(
        self,
        router: AudioRouter | None = None,
        snapcast: SnapcastManager | None = None,
    ) -> None:
        self.router = router or AudioRouter()
        self.snapcast = snapcast or SnapcastManager()
        self._wanted_running = False
        self.last_error: str | None = None
        self.error_stage: SourcePhase | None = None

    @property
    def is_running(self) -> bool:
        return self._wanted_running

    def start(self) -> HealthReport:
        self.last_error = None
        self.error_stage = None
        try:
            self.router.create_virtual_sink()
            self.router.set_default()
        except Exception as exc:
            self.last_error = f"Virtual sink creation failed: {exc}"
            self.error_stage = SourcePhase.SOURCE_ACTIVE
            self._wanted_running = False
            self.router.remove_virtual_sink()
            raise

        try:
            self.snapcast.start()
        except Exception as exc:
            self.last_error = f"Snapcast transport failed: {exc}"
            self.error_stage = SourcePhase.TRANSPORT_ACTIVE
            self._wanted_running = False
            self.router.remove_virtual_sink()
            raise

        self._wanted_running = True
        return self.health()

    def stop(self) -> HealthReport:
        self.last_error = None
        self.error_stage = None
        self.snapcast.stop_local_client()
        self.snapcast.stop()
        self.router.remove_virtual_sink()
        self._wanted_running = False
        return self.health()

    def start_local_test_client(self) -> str:
        if not self._wanted_running and not self.snapcast.is_server_alive():
            raise SnapcastUnavailable("Start the source before launching a local test client.")
        sink = self.router.playback_sink_for_local_client()
        return self.snapcast.start_local_client(pulse_sink=sink)

    def stop_local_test_client(self) -> None:
        self.snapcast.stop_local_client()

    def probe(self, seconds: float = 2.5) -> HealthReport:
        """Inject a short tone and sample health while PCM is flowing."""
        if not self._wanted_running and not self.snapcast.is_server_alive():
            raise SnapcastUnavailable("Start the source before probing playback.")
        process = self.router.start_probe_tone(seconds=seconds)
        best = self.health()
        deadline = time.monotonic() + seconds + 0.8
        try:
            while time.monotonic() < deadline:
                report = self.health()
                if report.phase == SourcePhase.PLAYBACK_CONFIRMED:
                    best = report
                    break
                if report.stream_status == "playing" and (
                    best.stream_status != "playing"
                    or len(report.connected_clients) > len(best.connected_clients)
                ):
                    best = report
                if process.poll() is not None and time.monotonic() > deadline - 0.4:
                    break
                time.sleep(0.15)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()
        if best.stream_status == "playing":
            return best
        # Fall back to the latest report with an explicit probe note.
        latest = self.health()
        return HealthReport(
            phase=latest.phase,
            sink_exists=latest.sink_exists,
            sink_is_default=latest.sink_is_default,
            snapserver_running=latest.snapserver_running,
            ffmpeg_running=latest.ffmpeg_running,
            stream_status=latest.stream_status,
            connected_clients=latest.connected_clients,
            local_client_running=latest.local_client_running,
            detail=(
                "Probe tone finished without seeing stream=playing. "
                + latest.detail
            ),
            steps=latest.steps,
        )

    def health(self) -> HealthReport:
        sink_exists = self.router.sink_exists()
        sink_is_default = self.router.is_default()
        snap_health = self.snapcast.health()
        clients = snap_health.connected_clients
        local_client = snap_health.local_client_running
        stream_status = snap_health.stream_status
        stream_playing = (stream_status or "").lower() == "playing"

        source_ok = sink_exists and sink_is_default
        transport_ok = snap_health.server_running and snap_health.encoder_running
        receiver_ok = bool(clients)
        playback_ok = transport_ok and receiver_ok and stream_playing

        if self.last_error and not self._wanted_running:
            phase = SourcePhase.ERROR
            detail = self.last_error
        elif self._wanted_running and not source_ok:
            phase = SourcePhase.ERROR
            detail = "Virtual sink was dropped or changed unexpectedly."
        elif self._wanted_running and not transport_ok:
            phase = SourcePhase.ERROR
            detail = "Snapcast transport stopped unexpectedly while source was active."
        elif playback_ok:
            phase = SourcePhase.PLAYBACK_CONFIRMED
            detail = (
                f"PCM is flowing and {len(clients)} Snapclient(s) are connected: "
                + ", ".join(clients)
            )
        elif receiver_ok and transport_ok:
            phase = SourcePhase.RECEIVER_CONNECTED
            detail = (
                f"{len(clients)} Snapclient(s) connected, waiting for confirmed PCM: "
                + ", ".join(clients)
            )
        elif transport_ok:
            phase = SourcePhase.TRANSPORT_ACTIVE
            detail = (
                "Snapserver and capture are running. Connect a Snapclient, or start "
                "the local test client."
            )
        elif source_ok:
            phase = SourcePhase.SOURCE_ACTIVE
            detail = "Virtual sink is the default output, but Snapcast transport is not healthy."
        else:
            phase = SourcePhase.IDLE
            if self._wanted_running:
                phase = SourcePhase.ERROR
                detail = "Source was started, but the sink or transport dropped."
            else:
                detail = "Source is stopped. Start source to create the sink and Snapcast transport."

        steps = (
            (
                SourcePhase.SOURCE_ACTIVE,
                source_ok,
                "Virtual sink exists and is the default output",
            ),
            (
                SourcePhase.TRANSPORT_ACTIVE,
                transport_ok,
                "Snapserver and FFmpeg capture are running",
            ),
            (
                SourcePhase.RECEIVER_CONNECTED,
                receiver_ok,
                "At least one Snapclient is connected",
            ),
            (
                SourcePhase.PLAYBACK_CONFIRMED,
                playback_ok,
                "Stream status is playing with a connected client",
            ),
        )
        return HealthReport(
            phase=phase,
            sink_exists=sink_exists,
            sink_is_default=sink_is_default,
            snapserver_running=snap_health.server_running,
            ffmpeg_running=snap_health.encoder_running,
            stream_status=stream_status,
            connected_clients=clients,
            local_client_running=local_client,
            detail=detail,
            steps=steps,
        )
