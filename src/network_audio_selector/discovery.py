from __future__ import annotations

import asyncio
from dataclasses import replace

from zeroconf import InterfaceChoice, ServiceBrowser, ServiceListener, Zeroconf

from .models import Speaker, SpeakerKind
from .spotify import probe_get_info


class _Listener(ServiceListener):
    def __init__(self) -> None:
        self.items: dict[str, Speaker] = {}

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        info = zc.get_service_info(service_type, name, timeout=3000)
        if info is None:
            return
        address = info.parsed_addresses()[0] if info.parsed_addresses() else None
        lower = service_type.lower()
        if "googlecast" in lower:
            kind = SpeakerKind.CHROMECAST
        elif "_raop" in lower or "_airplay" in lower:
            kind = SpeakerKind.AIRPLAY
        elif "_upnp" in lower or "_dial" in lower:
            kind = SpeakerKind.UPNP
        elif "_spotify-connect" in lower:
            kind = SpeakerKind.SPOTIFY_CONNECT
        else:
            return
        device_name = info.properties.get(b"fn", name.split(".", 1)[0].encode())
        properties = {
            (key.decode(errors="replace") if isinstance(key, bytes) else str(key)):
            (value.decode(errors="replace") if isinstance(value, bytes) else str(value))
            for key, value in info.properties.items()
        }
        path = properties.get("CPath", "/zeroconf")
        note = "Discovered on the local network"
        display_name = device_name.decode(errors="replace")
        self.items[f"{kind.value}:{name}"] = Speaker(
            id=f"{kind.value}:{name}",
            name=display_name,
            kind=kind,
            address=address,
            port=info.port,
            path=path if kind is SpeakerKind.SPOTIFY_CONNECT else None,
            note=note,
        )

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self.add_service(zc, service_type, name)

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        for key in list(self.items):
            if key.endswith(f":{name}"):
                self.items.pop(key, None)


async def discover(timeout: float = 5.0) -> list[Speaker]:
    listener = _Listener()
    zc = Zeroconf(interfaces=InterfaceChoice.All)
    browsers = [
        ServiceBrowser(zc, "_googlecast._tcp.local.", listener),
        ServiceBrowser(zc, "_airplay._tcp.local.", listener),
        ServiceBrowser(zc, "_raop._tcp.local.", listener),
        ServiceBrowser(zc, "_upnp._tcp.local.", listener),
        ServiceBrowser(zc, "_spotify-connect._tcp.local.", listener),
    ]
    try:
        await asyncio.sleep(timeout)
        for key, speaker in list(listener.items.items()):
            if (
                speaker.kind is not SpeakerKind.SPOTIFY_CONNECT
                or not speaker.address
                or not speaker.port
            ):
                continue
            result = probe_get_info(
                speaker.address,
                speaker.port,
                speaker.path or "/zeroconf",
            )
            if result.ok:
                note = "Spotify Connect"
                if result.product:
                    note = f"Spotify Connect · {result.product}"
                listener.items[key] = replace(
                    speaker,
                    name=result.name or speaker.name,
                    note=note,
                )
            else:
                listener.items[key] = replace(
                    speaker,
                    note=f"Spotify Connect · {result.message}",
                )
        return sorted(listener.items.values(), key=lambda item: item.name.lower())
    finally:
        for browser in browsers:
            browser.cancel()
        zc.close()


def discover_sync(timeout: float = 5.0) -> list[Speaker]:
    return asyncio.run(discover(timeout))
