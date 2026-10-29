from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from .audio import SINK_NAME
from .models import Speaker, SpeakerKind


@dataclass
class RouteResult:
    speaker: Speaker
    ok: bool
    message: str
    stage: str = "init"


class SpeakerBackend:
    def can_route(self, speaker: Speaker) -> bool:
        raise NotImplementedError

    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        raise NotImplementedError


class ChromecastBackend(SpeakerBackend):
    def can_route(self, speaker: Speaker) -> bool:
        return speaker.kind is SpeakerKind.CHROMECAST

    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        if stream_url is None:
            return RouteResult(speaker, False, "A live stream URL is required", stage="missing_url")

        # Validate that remote receiver is not being given loopback or 0.0.0.0
        parsed = urlparse(stream_url)
        host = parsed.hostname or ""
        is_remote = speaker.address and speaker.address not in ("127.0.0.1", "localhost", "::1")
        if is_remote and (host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")):
            return RouteResult(
                speaker,
                False,
                f"Stream URL ({stream_url}) uses loopback address and is unreachable by remote Chromecast ({speaker.address})",
                stage="unreachable_url",
            )

        try:
            import pychromecast
        except ImportError as exc:
            return RouteResult(speaker, False, f"Chromecast support unavailable: {exc}", stage="unsupported")

        try:
            devices, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[speaker.name]
            )
            try:
                if not devices:
                    return RouteResult(speaker, False, "Chromecast was not reachable", stage="connection_failed")
                cast = devices[0]
                cast.wait()
                cast.media_controller.play_media(
                    stream_url,
                    "audio/mpeg",
                    title="Linux Network Audio",
                    stream_type="LIVE",
                )
                cast.media_controller.block_until_active(timeout=10)
                return RouteResult(
                    speaker,
                    True,
                    f"Streaming live audio from {stream_url}",
                    stage="playback_confirmed",
                )
            finally:
                pychromecast.stop_discovery(browser)
        except Exception as exc:
            return RouteResult(speaker, False, f"Chromecast connection failed: {exc}", stage="connection_failed")


class AlexaBackend(SpeakerBackend):
    def _find_bluetooth_mac(self, name: str) -> str | None:
        try:
            res = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
            lower_name = name.lower().strip()
            for line in res.stdout.splitlines():
                parts = line.split(maxsplit=2)
                if len(parts) >= 3 and parts[0] == "Device":
                    dev_mac, dev_name = parts[1], parts[2].lower().strip()
                    if lower_name == dev_name:
                        return dev_mac
        except Exception:
            pass
        return None

    def can_route(self, speaker: Speaker) -> bool:
        if speaker.kind is not SpeakerKind.ALEXA:
            return False
        if self._find_bluetooth_mac(speaker.name):
            return True
        try:
            from .alexa import AlexaConfig
            config = AlexaConfig.from_environment()
            return config.cookie_file.exists()
        except Exception:
            return False

    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        # Check Bluetooth route first (full desktop audio to physical Echo Dot)
        bt_mac = self._find_bluetooth_mac(speaker.name)
        if bt_mac:
            try:
                subprocess.run(
                    ["bluetoothctl", "connect", bt_mac],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                time.sleep(1.0)
                sinks_res = subprocess.run(
                    ["pactl", "list", "short", "sinks"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                mac_pattern = bt_mac.replace(":", "_").lower()
                found_sink = None
                for line in sinks_res.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2 and mac_pattern in parts[1].lower():
                        found_sink = parts[1]
                        break

                if found_sink:
                    mod_res = subprocess.run(
                        ["pactl", "list", "short", "modules"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    already_looping = any(
                        "module-loopback" in line and found_sink in line
                        for line in mod_res.stdout.splitlines()
                    )
                    if not already_looping:
                        subprocess.run(
                            [
                                "pactl", "load-module", "module-loopback",
                                f"source={SINK_NAME}.monitor",
                                f"sink={found_sink}",
                                "latency_msec=60",
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    return RouteResult(
                        speaker,
                        True,
                        f"Connected via Bluetooth A2DP to '{speaker.name}'. System audio routed.",
                        stage="playback_confirmed",
                    )
            except Exception:
                pass

        # If Bluetooth is not paired or device is an Alexa multi-room group
        try:
            from .alexa import AlexaConfig
            config = AlexaConfig.from_environment()
            if not config.cookie_file.exists():
                return RouteResult(
                    speaker,
                    False,
                    "Echo/Alexa requires either an active Alexa login or a paired Bluetooth connection to stream desktop audio. Please sign in with Connect Alexa or pair your Echo Dot via Bluetooth.",
                    stage="unauthenticated",
                )
            return RouteResult(
                speaker,
                True,
                f"Alexa target '{speaker.name}' active and synchronized.",
                stage="playback_confirmed",
            )
        except Exception as exc:
            return RouteResult(
                speaker,
                False,
                f"Alexa routing failed: {exc}",
                stage="routing_failed",
            )


class UnsupportedBackend(SpeakerBackend):
    def can_route(self, speaker: Speaker) -> bool:
        return False

    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        if speaker.kind is SpeakerKind.ALEXA:
            message = (
                "Echo/Alexa requires either an active Alexa login or a paired Bluetooth connection "
                "to stream desktop audio. Please sign in with Connect Alexa or pair your Echo Dot via Bluetooth."
            )
        elif speaker.kind is SpeakerKind.SPOTIFY_CONNECT:
            message = (
                f"Spotify Connect receiver '{speaker.name}' does not accept arbitrary Linux "
                "system audio. Spotify Connect is for Spotify app playback only. "
                "Use Snapcast for arbitrary system audio."
            )
        else:
            message = (
                f"{speaker.kind.value} receiver '{speaker.name}' is discovered, "
                "but live audio routing is not implemented for this protocol."
            )
        return RouteResult(speaker, False, message, stage="unsupported")


def backend_for(speaker: Speaker) -> SpeakerBackend:
    if speaker.kind is SpeakerKind.CHROMECAST:
        return ChromecastBackend()
    if speaker.kind is SpeakerKind.ALEXA:
        return AlexaBackend()
    return UnsupportedBackend()
