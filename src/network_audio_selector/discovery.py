from __future__ import annotations

import asyncio
from dataclasses import replace

from zeroconf import InterfaceChoice, ServiceBrowser, ServiceListener, Zeroconf

from .models import Speaker, SpeakerKind
from .spotify import SpotifyInfoResult, probe_get_info


def _kind_for_service(service_type: str) -> SpeakerKind | None:
    lower = service_type.lower()
    if "googlecast" in lower:
        return SpeakerKind.CHROMECAST
    if "_raop" in lower or "_airplay" in lower:
        return SpeakerKind.AIRPLAY
    if "_upnp" in lower or "_dial" in lower:
        return SpeakerKind.UPNP
    if "_spotify-connect" in lower:
        return SpeakerKind.SPOTIFY_CONNECT
    return None


class _Listener(ServiceListener):
    def __init__(self) -> None:
        self.items: dict[str, Speaker] = {}

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        kind = _kind_for_service(service_type)
        if kind is None:
            return
        info = zc.get_service_info(service_type, name, timeout=3000)
        if info is None:
            return

        parsed = info.parsed_addresses()
        addresses: tuple[str, ...] = tuple(parsed) if parsed else ()
        # Prefer IPv4 address as primary address, fallback to first available
        ipv4_addrs = [a for a in addresses if ":" not in a]
        primary_address = ipv4_addrs[0] if ipv4_addrs else (addresses[0] if addresses else None)

        device_name = info.properties.get(b"fn", name.split(".", 1)[0].encode())
        properties = {
            (key.decode(errors="replace") if isinstance(key, bytes) else str(key)):
            (value.decode(errors="replace") if isinstance(value, bytes) else str(value))
            for key, value in info.properties.items()
        }

        path: str | None = None
        if kind is SpeakerKind.SPOTIFY_CONNECT:
            cpath_raw = properties.get("CPath")
            if cpath_raw and cpath_raw.strip():
                path = cpath_raw.strip()
                note = "Spotify Connect (unverified)"
            else:
                path = None
                note = "Spotify Connect · missing CPath metadata"
        else:
            note = "Discovered on the local network"

        display_name = device_name.decode(errors="replace")
        key = f"{kind.value}:{name}"
        supports_routing = (kind is SpeakerKind.CHROMECAST)

        self.items[key] = Speaker(
            id=key,
            name=display_name,
            kind=kind,
            address=primary_address,
            addresses=addresses,
            port=info.port,
            path=path,
            note=note,
            supports_routing=supports_routing,
        )

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self.add_service(zc, service_type, name)

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        kind = _kind_for_service(service_type)
        if kind is not None:
            self.items.pop(f"{kind.value}:{name}", None)
        else:
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
                or not speaker.port
            ):
                continue
            if not speaker.path:
                continue

            candidates = list(speaker.addresses) if speaker.addresses else ([speaker.address] if speaker.address else [])
            # Prioritize IPv4 candidates before IPv6
            candidates.sort(key=lambda a: 1 if ":" in a else 0)

            result: SpotifyInfoResult | None = None
            resolved_address = speaker.address
            for candidate in candidates:
                res = probe_get_info(
                    candidate,
                    speaker.port,
                    speaker.path,
                    timeout=1.5,
                )
                if res.ok:
                    result = res
                    resolved_address = candidate
                    break
                result = res

            if result is not None and result.ok:
                note = "Spotify Connect"
                if result.product:
                    note = f"Spotify Connect · {result.product}"
                listener.items[key] = replace(
                    speaker,
                    name=result.name or speaker.name,
                    address=resolved_address,
                    note=note,
                )
            elif result is not None:
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
