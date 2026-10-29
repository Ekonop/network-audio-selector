from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch
import pytest

from network_audio_selector.tunnel import (
    CloudflareTunnel,
    NgrokTunnel,
    TunnelError,
    create_tunnel,
)


def test_tunnel_not_installed():
    tunnel = CloudflareTunnel(local_port=8080, binary_path="/nonexistent/cloudflared")
    with pytest.raises(TunnelError, match="cloudflared binary not found"):
        tunnel.start()


def test_ngrok_not_installed():
    tunnel = NgrokTunnel(local_port=8080, binary_path="/nonexistent/ngrok")
    with pytest.raises(TunnelError, match="ngrok binary not found"):
        tunnel.start()


def test_tunnel_stream_url_formatting():
    tunnel = CloudflareTunnel(local_port=8080)
    tunnel.public_url = "https://example-test.trycloudflare.com"
    assert tunnel.stream_url() == "https://example-test.trycloudflare.com/audio.mp3"
    assert tunnel.stream_url("stream.ogg") == "https://example-test.trycloudflare.com/stream.ogg"
    assert tunnel.stream_url("/custom.mp3") == "https://example-test.trycloudflare.com/custom.mp3"


def test_tunnel_parses_url_from_stderr():
    tunnel = CloudflareTunnel(local_port=8080, binary_path="/bin/true")
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stderr.readline.side_effect = [
        "2026-10-29 INF starting tunnel...\n",
        "2026-10-29 INF |  https://my-stream-subdomain.trycloudflare.com  |\n",
    ]

    with patch("network_audio_selector.tunnel.subprocess.Popen", return_value=mock_proc):
        url = tunnel.start(timeout=2.0)
        assert url == "https://my-stream-subdomain.trycloudflare.com"
        assert tunnel.public_url == "https://my-stream-subdomain.trycloudflare.com"
        assert tunnel.is_running

    tunnel.stop()
    assert tunnel.public_url is None


def test_ngrok_parses_api_response():
    tunnel = NgrokTunnel(local_port=8080, binary_path="/bin/true")
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    mock_resp_data = json.dumps({
        "tunnels": [
            {"name": "command_line", "public_url": "https://ngrok-stream-test.ngrok-free.app"}
        ]
    }).encode("utf-8")

    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = BytesIO(mock_resp_data)

    with patch("network_audio_selector.tunnel.subprocess.Popen", return_value=mock_proc), \
         patch("network_audio_selector.tunnel.urlopen", mock_urlopen):
        url = tunnel.start(timeout=2.0)
        assert url == "https://ngrok-stream-test.ngrok-free.app"
        assert tunnel.stream_url() == "https://ngrok-stream-test.ngrok-free.app/audio.mp3"
        assert tunnel.is_running

    tunnel.stop()
    assert tunnel.public_url is None


def test_create_tunnel_factory():
    cf_tunnel = create_tunnel(8080, provider="cloudflare")
    assert isinstance(cf_tunnel, CloudflareTunnel)

    ng_tunnel = create_tunnel(8080, provider="ngrok")
    assert isinstance(ng_tunnel, NgrokTunnel)

    auto_tunnel = create_tunnel(8080, provider="auto")
    assert isinstance(auto_tunnel, (CloudflareTunnel, NgrokTunnel))
