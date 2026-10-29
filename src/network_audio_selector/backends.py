from __future__ import annotations

from dataclasses import dataclass

from .models import Speaker, SpeakerKind


@dataclass
class RouteResult:
    speaker: Speaker
    ok: bool
    message: str
    stage: str = "init"


class SpeakerBackend:
    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        raise NotImplementedError


class ChromecastBackend(SpeakerBackend):
    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
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
                if stream_url is None:
                    return RouteResult(speaker, False, "A live stream URL is required", stage="missing_url")
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


class UnsupportedBackend(SpeakerBackend):
    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        if speaker.kind is SpeakerKind.ALEXA:
            message = (
                "Echo/Alexa does not accept arbitrary Linux desktop audio over the LAN. "
                "Account control only is available; arbitrary audio routing is unsupported."
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
    return ChromecastBackend() if speaker.kind is SpeakerKind.CHROMECAST else UnsupportedBackend()
