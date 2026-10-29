from __future__ import annotations

from dataclasses import dataclass

from .models import Speaker, SpeakerKind


@dataclass
class RouteResult:
    speaker: Speaker
    ok: bool
    message: str


class SpeakerBackend:
    def route(self, speaker: Speaker) -> RouteResult:
        raise NotImplementedError


class ChromecastBackend(SpeakerBackend):
    def route(self, speaker: Speaker, stream_url: str | None = None) -> RouteResult:
        try:
            import pychromecast
        except ImportError as exc:
            return RouteResult(speaker, False, f"Chromecast support unavailable: {exc}")
        try:
            devices, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[speaker.name]
            )
            try:
                if not devices:
                    return RouteResult(speaker, False, "Chromecast was not reachable")
                devices[0].wait()
                if stream_url is None:
                    return RouteResult(speaker, False, "A live stream URL is required")
                devices[0].media_controller.play_media(
                    stream_url,
                    "audio/mpeg",
                    title="Linux Network Audio",
                    stream_type="LIVE",
                )
                devices[0].media_controller.block_until_active()
                return RouteResult(
                    speaker,
                    True,
                    f"Streaming live audio from {stream_url}",
                )
            finally:
                pychromecast.stop_discovery(browser)
        except Exception as exc:
            return RouteResult(speaker, False, f"Chromecast connection failed: {exc}")


class UnsupportedBackend(SpeakerBackend):
    def route(self, speaker: Speaker) -> RouteResult:
        if speaker.kind is SpeakerKind.ALEXA:
            message = (
                "Alexa target identified, but Echo does not accept arbitrary raw "
                "LAN audio. Account control is available for playback/volume."
            )
        else:
            message = (
                f"{speaker.kind.value} is discovered, but its live sender adapter "
                "is not enabled yet."
            )
        if speaker.kind is SpeakerKind.SPOTIFY_CONNECT:
            message = (
                "Spotify Connect receiver identified. Spotify Connect is a "
                "playback-control integration, not a generic raw Linux-audio "
                "transport; use Snapcast for arbitrary system audio."
            )
        return RouteResult(speaker, False, message)


def backend_for(speaker: Speaker) -> SpeakerBackend:
    return ChromecastBackend() if speaker.kind is SpeakerKind.CHROMECAST else UnsupportedBackend()
