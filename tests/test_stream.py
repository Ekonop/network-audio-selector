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
