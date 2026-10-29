from __future__ import annotations

import time
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from network_audio_selector.stream import AudioStream, _StreamState


def test_audio_stream_start_stop_repetition():
    stream = AudioStream(port=0, bind_address="127.0.0.1")

    fake_process = MagicMock()
    fake_process.poll.return_value = None
    fake_process.stdout = MagicMock()
    fake_process.stdout.read.return_value = b""
    fake_process.stderr = MagicMock()
    fake_process.wait.return_value = 0

    with patch("subprocess.Popen", return_value=fake_process):
        # Repeated start/stop cycle 5 times
        for _ in range(5):
            url = stream.start()
            assert url.startswith("http://127.0.0.1:")
            assert url.endswith("/audio.mp3")
            stream.stop()
            assert stream._server is None
            assert stream._process is None
            assert stream._thread is None
            assert stream._http_thread is None


def test_audio_stream_partial_startup_cleanup():
    stream = AudioStream(port=0, bind_address="127.0.0.1")

    fake_process = MagicMock()
    fake_process.poll.return_value = 1
    fake_process.stderr = MagicMock()
    fake_process.stderr.read.return_value = b"Pulse audio connection error"

    with patch("subprocess.Popen", return_value=fake_process):
        with pytest.raises(RuntimeError) as exc:
            stream.start()
        assert "Pulse audio connection error" in str(exc.value)

        # Ensure server was cleanly stopped and not left listening
        assert stream._server is None
        assert stream._process is None
        assert stream._http_thread is None


def test_stream_state_publish_and_close():
    state = _StreamState()
    client = state.add()

    state.publish(b"CHUNK1")
    assert client.get_nowait() == b"CHUNK1"

    state.close()
    assert client.get_nowait() is None


def test_audio_stream_serves_http_client_and_shuts_down():
    stream = AudioStream(port=0, bind_address="127.0.0.1")

    fake_process = MagicMock()
    fake_process.poll.return_value = None
    fake_process.stdout = MagicMock()
    # Stay alive during test
    fake_process.stdout.read.side_effect = lambda size: time.sleep(0.5) or b""
    fake_process.stderr = MagicMock()
    fake_process.wait.return_value = 0

    with patch("subprocess.Popen", return_value=fake_process):
        url = stream.start()
        # Publish chunk to stream state
        stream._state.publish(b"AUDIO_STREAM_TEST_BYTES")

        # Test request
        req = urllib.request.Request(url)
        # 404 for invalid path
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(url.replace("audio.mp3", "other.mp3"), timeout=2.0)
        assert exc.value.code == 404

        stream.stop()
        assert stream._server is None


def test_audio_stream_modes():
    from network_audio_selector.stream import StreamMode, get_source_ip_for_target

    # 1. Default / Loopback mode
    s_default = AudioStream()
    assert s_default.mode == StreamMode.LOOPBACK
    assert s_default.bind_address == "127.0.0.1"

    # 2. LAN mode with specific receiver address
    s_lan = AudioStream(mode=StreamMode.LAN, receiver_address="192.168.1.100")
    assert s_lan.mode == StreamMode.LAN
    assert s_lan.bind_address != "127.0.0.1"
    assert s_lan.bind_address != "0.0.0.0"

    # 3. Custom mode with explicit bind
    s_custom = AudioStream(mode=StreamMode.CUSTOM, bind_address="10.0.0.5")
    assert s_custom.mode == StreamMode.CUSTOM
    assert s_custom.bind_address == "10.0.0.5"

    # 4. Route resolution returns a valid IPv4
    resolved_ip = get_source_ip_for_target("127.0.0.1")
    assert resolved_ip == "127.0.0.1"


def test_stream_state_queue_full_drain_on_close():
    state = _StreamState()
    client = state.add()

    # Fill queue to maximum capacity (32 items)
    for i in range(32):
        state.publish(f"CHUNK_{i}".encode())

    # Verify queue is full
    assert client.full()

    # Close must drain queue so None can be delivered without blocking
    state.close()
    assert state.is_closed
    # After close, client queue must receive None
    item = client.get_nowait()
    assert item is None


def test_chromecast_rejects_loopback_stream_url():
    from network_audio_selector.backends import ChromecastBackend
    from network_audio_selector.models import Speaker, SpeakerKind

    spk = Speaker(
        id="chromecast:1",
        name="Living Room TV",
        kind=SpeakerKind.CHROMECAST,
        address="192.168.1.55",
    )
    backend = ChromecastBackend()
    res = backend.route(spk, stream_url="http://127.0.0.1:8000/audio.mp3")
    assert not res.ok
    assert "loopback" in res.message.lower()

    # Also test rejection of 0.0.0.0
    res_zero = backend.route(spk, stream_url="http://0.0.0.0:8000/audio.mp3")
    assert not res_zero.ok
