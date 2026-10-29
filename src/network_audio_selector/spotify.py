from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SpotifyInfoResult:
    ok: bool
    name: str | None = None
    product: str | None = None
    message: str = ""


def probe_get_info(
    address: str, port: int, path: str | None, timeout: float = 1.5
) -> SpotifyInfoResult:
    """Safely identify a discovered Spotify Connect endpoint.

    The probe only performs the documented read-only getInfo request. It does
    not send credentials, start playback, or attempt to claim the device.
    """
    if not address:
        return SpotifyInfoResult(False, message="missing address")
    if not path or not path.strip():
        return SpotifyInfoResult(False, message="missing CPath metadata")

    normalized_path = path if path.startswith("/") else f"/{path}"
    host = f"[{address}]" if ":" in address and not address.startswith("[") else address
    url = f"http://{host}:{port}{normalized_path}?action=getInfo"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_data = response.read(256 * 1024).decode("utf-8")
            payload = json.loads(raw_data)
    except (HTTPError, URLError, TimeoutError, ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        return SpotifyInfoResult(False, message=f"getInfo unavailable ({type(exc).__name__})")

    if not isinstance(payload, dict):
        return SpotifyInfoResult(False, message="getInfo returned an invalid response")

    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload

    alias_name = None
    aliases = info.get("aliases")
    if isinstance(aliases, list) and aliases and isinstance(aliases[0], dict):
        alias_name = _text(aliases[0].get("name"))

    name = _text(
        info.get("name")
        or info.get("remoteName")
        or alias_name
        or info.get("device_name")
        or info.get("modelDisplayName")
    )
    product = _text(
        info.get("product")
        or info.get("model")
        or info.get("modelDisplayName")
        or info.get("brandDisplayName")
    )
    return SpotifyInfoResult(True, name=name, product=product, message="getInfo OK")


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
