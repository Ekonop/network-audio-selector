from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_audio_selector.snapcast import SnapcastManager, SnapcastUnavailable


def test_snapcast_not_installed(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    with patch.object(mgr, "_which", return_value=None):
        with pytest.raises(SnapcastUnavailable) as exc:
            mgr.start()
        assert "not installed" in str(exc.value)


def test_snapcast_control_port_conflict(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    with patch.object(mgr, "_which", return_value="/usr/bin/snapserver"), \
         patch.object(mgr, "is_server_alive", return_value=False), \
         patch.object(mgr, "_port_open", return_value=True):

        with pytest.raises(SnapcastUnavailable) as exc:
            mgr.start()
        assert "already in use" in str(exc.value)


def test_snapcast_ffmpeg_exits_immediately(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)

    fake_server = MagicMock()
    fake_server.poll.return_value = None
    fake_server.pid = 1111
    fake_server.communicate.return_value = ("", "")

    fake_ffmpeg = MagicMock()
    fake_ffmpeg.poll.return_value = 1
    fake_ffmpeg.pid = 2222
    fake_ffmpeg.communicate.return_value = ("", "")

    with patch.object(mgr, "_which", return_value="/usr/bin/snapserver"), \
         patch.object(mgr, "is_server_alive", return_value=False), \
         patch.object(mgr, "_port_open", return_value=False), \
         patch.object(mgr, "_open_fifo_writer", return_value=MagicMock()), \
         patch.object(mgr, "_kill_matching_config_servers"), \
         patch("subprocess.Popen", side_effect=[fake_server, fake_ffmpeg]), \
         patch.object(mgr, "_read_stderr", side_effect=lambda proc: "Pulse sink monitor not found" if proc == fake_ffmpeg else ""):

        with pytest.raises(SnapcastUnavailable) as exc:
            mgr.start()
        assert "FFmpeg encoder exited while starting" in str(exc.value)
        assert "Pulse sink monitor not found" in str(exc.value)


def test_snapcast_startup_timeout(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)

    fake_server = MagicMock()
    fake_server.poll.return_value = None
    fake_server.pid = 1111
    fake_server.communicate.return_value = ("", "")

    fake_ffmpeg = MagicMock()
    fake_ffmpeg.poll.return_value = None
    fake_ffmpeg.pid = 2222
    fake_ffmpeg.communicate.return_value = ("", "")

    with patch.object(mgr, "_which", return_value="/usr/bin/snapserver"), \
         patch.object(mgr, "is_server_alive", return_value=False), \
         patch.object(mgr, "_port_open", return_value=False), \
         patch.object(mgr, "_open_fifo_writer", return_value=MagicMock()), \
         patch.object(mgr, "_kill_matching_config_servers"), \
         patch("subprocess.Popen", side_effect=[fake_server, fake_ffmpeg]), \
         patch("time.sleep", return_value=None):

        with pytest.raises(SnapcastUnavailable) as exc:
            mgr.start()
        assert "control port 1705 never became ready" in str(exc.value)


def test_snapcast_stale_pid_reaped(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    mgr.server_pid_file.write_text("99999\n", encoding="utf-8")
    mgr.encoder_pid_file.write_text("88888\n", encoding="utf-8")

    with patch.object(mgr, "_pid_is_process", return_value=False):
        mgr._reap_stale_managed_processes()
        assert not mgr.server_pid_file.exists()
        assert not mgr.encoder_pid_file.exists()


def test_snapcast_local_client_failure(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)

    fake_client = MagicMock()
    fake_client.poll.return_value = 1
    fake_client.pid = 3333

    with patch.object(mgr, "_which", return_value="/usr/bin/snapclient"), \
         patch.object(mgr, "is_server_alive", return_value=True), \
         patch.object(mgr, "_client_alive", return_value=False), \
         patch("subprocess.Popen", return_value=fake_client), \
         patch.object(mgr, "_read_stderr", return_value="Connection refused"):

        with pytest.raises(SnapcastUnavailable) as exc:
            mgr.start_local_client()
        assert "Local Snapclient exited immediately" in str(exc.value)


def test_open_fifo_writer_raises_when_server_exits(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    mgr.fifo = tmp_path / "test.fifo"
    import os
    os.mkfifo(mgr.fifo)

    fake_server = MagicMock()
    fake_server.poll.return_value = 1
    mgr.server = fake_server

    with patch.object(mgr, "_read_stderr", return_value="FIFO config error"):
        with pytest.raises(SnapcastUnavailable) as exc:
            mgr._open_fifo_writer(timeout=0.2)
        assert "Snapserver exited while waiting for audio FIFO" in str(exc.value)


def test_open_fifo_writer_raises_on_timeout(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    mgr.fifo = tmp_path / "test.fifo"
    import os
    os.mkfifo(mgr.fifo)

    fake_server = MagicMock()
    fake_server.poll.return_value = None
    mgr.server = fake_server

    # Without a reader, opening O_NONBLOCK | O_WRONLY will raise ENXIO
    with pytest.raises(SnapcastUnavailable) as exc:
        mgr._open_fifo_writer(timeout=0.1)
    assert "Timeout waiting for Snapserver to open audio FIFO" in str(exc.value)


def test_terminate_never_kills_unverified_recycled_pids(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    pid_file = tmp_path / "stale_snapserver.pid"
    pid_file.write_text("12345\n")

    with patch.object(mgr, "_pid_is_process", return_value=False), \
         patch.object(mgr, "_signal_pid") as mock_signal:

        mgr._terminate(
            None,
            pid_file,
            expected_name="snapserver",
            config_marker=str(mgr.config),
        )

        # Must NOT send SIGTERM or SIGKILL to unverified PID 12345
        mock_signal.assert_not_called()
        # Stale PID file is cleaned up
        assert not pid_file.exists()


def test_terminate_signals_verified_pid(tmp_path):
    mgr = SnapcastManager(state_dir=tmp_path)
    pid_file = tmp_path / "verified_snapserver.pid"
    pid_file.write_text("12345\n")

    # First call returns True (process exists), subsequent calls return False (process exited after SIGTERM)
    with patch.object(mgr, "_pid_is_process", side_effect=[True, False, False]), \
         patch.object(mgr, "_signal_pid") as mock_signal:

        mgr._terminate(
            None,
            pid_file,
            expected_name="snapserver",
            config_marker=str(mgr.config),
        )

        import signal
        mock_signal.assert_called_once_with(12345, signal.SIGTERM)
        assert not pid_file.exists()
