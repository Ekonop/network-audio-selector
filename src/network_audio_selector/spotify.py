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


def probe_get_info(address: str, port: int, path: str, timeout: float = 1.5) -> SpotifyInfoResult:
    """Safely identify a discovered Spotify Connect endpoint.

    The probe only performs the documented read-only getInfo request. It does
    not send credentials, start playback, or attempt to claim the device.
    """
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"http://{address}:{port}{normalized_path}?action=getInfo"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
        return SpotifyInfoResult(False, message=f"getInfo unavailable ({type(exc).__name__})")
    if not isinstance(payload, dict):
        return SpotifyInfoResult(False, message="getInfo returned an invalid response")
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    name = _text(
        info.get("name")
        or info.get("remoteName")
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
