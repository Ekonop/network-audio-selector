from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest
from zeroconf import ServiceInfo

from network_audio_selector.discovery import _Listener, discover
from network_audio_selector.models import Speaker, SpeakerKind
from network_audio_selector.spotify import SpotifyInfoResult, probe_get_info


def test_spotify_probe_missing_or_malformed_cpath():
    res1 = probe_get_info("10.0.0.1", 4070, None)
    assert not res1.ok
    assert "missing CPath metadata" in res1.message

    res2 = probe_get_info("10.0.0.1", 4070, "")
    assert not res2.ok
    assert "missing CPath metadata" in res2.message

    res3 = probe_get_info("", 4070, "/spotifyConnect")
    assert not res3.ok
    assert "missing address" in res3.message


def test_spotify_probe_ipv6_formatting_and_success():
    fake_json = {
        "status": 101,
        "modelDisplayName": "Echo_Dot",
        "brandDisplayName": "Amazon",
        "aliases": [{"name": "Living Room Echo"}],
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_json).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("network_audio_selector.spotify.urlopen", return_value=mock_resp) as mock_urlopen:
        res = probe_get_info("fe80::1", 4070, "/spotifyConnect")
        assert res.ok
        assert res.name == "Living Room Echo"
        assert res.product == "Echo_Dot"

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://[fe80::1]:4070/spotifyConnect?action=getInfo"


def test_spotify_probe_http_errors_and_malformed_json():
    with patch("network_audio_selector.spotify.urlopen", side_effect=URLError("Connection refused")):
        res = probe_get_info("10.0.0.1", 4070, "/spotifyConnect")
        assert not res.ok
        assert "URLError" in res.message

    with patch("network_audio_selector.spotify.urlopen", side_effect=TimeoutError()):
        res = probe_get_info("10.0.0.1", 4070, "/spotifyConnect")
        assert not res.ok
        assert "TimeoutError" in res.message

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"NOT_JSON_DATA"
    mock_resp.__enter__.return_value = mock_resp
    with patch("network_audio_selector.spotify.urlopen", return_value=mock_resp):
        res = probe_get_info("10.0.0.1", 4070, "/spotifyConnect")
        assert not res.ok
        assert "JSONDecodeError" in res.message or "unavailable" in res.message

    mock_resp.read.return_value = json.dumps(["not", "a", "dict"]).encode("utf-8")
    with patch("network_audio_selector.spotify.urlopen", return_value=mock_resp):
        res = probe_get_info("10.0.0.1", 4070, "/spotifyConnect")
        assert not res.ok
        assert "invalid response" in res.message


def test_discovery_valid_and_invalid_metadata():
    listener = _Listener()
    zc = MagicMock()

    # Valid service info
    info_valid = MagicMock(spec=ServiceInfo)
    info_valid.parsed_addresses.return_value = ["192.168.1.100", "fe80::100"]
    info_valid.port = 4070
    info_valid.properties = {b"CPath": b"/spotifyConnect", b"fn": b"My Echo"}
    zc.get_service_info.return_value = info_valid

    listener.add_service(zc, "_spotify-connect._tcp.local.", "My Echo._spotify-connect._tcp.local.")
    key = "spotify-connect:My Echo._spotify-connect._tcp.local."
    assert key in listener.items
    speaker = listener.items[key]
    assert speaker.name == "My Echo"
    assert speaker.address == "192.168.1.100"
    assert speaker.addresses == ("192.168.1.100", "fe80::100")
    assert speaker.path == "/spotifyConnect"

    # Missing CPath
    info_no_cpath = MagicMock(spec=ServiceInfo)
    info_no_cpath.parsed_addresses.return_value = ["192.168.1.101"]
    info_no_cpath.port = 4070
    info_no_cpath.properties = {b"fn": b"Echo No CPath"}
    zc.get_service_info.return_value = info_no_cpath

    listener.add_service(zc, "_spotify-connect._tcp.local.", "Echo No CPath._spotify-connect._tcp.local.")
    key2 = "spotify-connect:Echo No CPath._spotify-connect._tcp.local."
    speaker2 = listener.items[key2]
    assert speaker2.path is None
    assert "missing CPath metadata" in (speaker2.note or "")


def test_discovery_service_removal_does_not_remove_different_kind_with_same_name():
    listener = _Listener()
    listener.items["spotify-connect:DeviceA"] = Speaker(
        id="spotify-connect:DeviceA",
        name="DeviceA",
        kind=SpeakerKind.SPOTIFY_CONNECT,
    )
    listener.items["upnp:DeviceA"] = Speaker(
        id="upnp:DeviceA",
        name="DeviceA",
        kind=SpeakerKind.UPNP,
    )

    zc = MagicMock()
    # Remove spotify-connect only
    listener.remove_service(zc, "_spotify-connect._tcp.local.", "DeviceA")

    assert "spotify-connect:DeviceA" not in listener.items
    assert "upnp:DeviceA" in listener.items


def test_discover_candidate_address_selection():
    import asyncio
    with patch("network_audio_selector.discovery.Zeroconf"), \
         patch("network_audio_selector.discovery.ServiceBrowser"), \
         patch("network_audio_selector.discovery.probe_get_info") as mock_probe:

        mock_probe.side_effect = [
            SpotifyInfoResult(False, message="IPv4 timeout"),
            SpotifyInfoResult(True, name="Resolved Echo", product="Echo_Dot"),
        ]

        def run_discovery():
            listener = _Listener()
            listener.items["spotify-connect:Echo"] = Speaker(
                id="spotify-connect:Echo",
                name="Echo",
                kind=SpeakerKind.SPOTIFY_CONNECT,
                address="10.0.0.24",
                addresses=("10.0.0.24", "fe80::24"),
                port=4070,
                path="/spotifyConnect",
            )
            return listener

        with patch("network_audio_selector.discovery._Listener", side_effect=run_discovery):
            speakers = asyncio.run(discover(timeout=0.01))
            assert len(speakers) == 1
            spk = speakers[0]
            assert spk.name == "Resolved Echo"
            assert "Echo_Dot" in spk.note
            assert spk.address == "fe80::24"
