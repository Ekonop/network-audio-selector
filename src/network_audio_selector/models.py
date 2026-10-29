from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SpeakerKind(StrEnum):
    ALEXA = "alexa"
    CHROMECAST = "chromecast"
    AIRPLAY = "airplay"
    UPNP = "upnp"
    SPOTIFY_CONNECT = "spotify-connect"


@dataclass(frozen=True)
class Speaker:
    id: str
    name: str
    kind: SpeakerKind
    address: str | None = None
    port: int | None = None
    path: str | None = None
    available: bool = True
    note: str | None = None
    addresses: tuple[str, ...] = ()
    supports_routing: bool = False
