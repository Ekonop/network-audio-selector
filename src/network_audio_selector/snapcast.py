from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .audio import SINK_NAME


class SnapcastUnavailable(RuntimeError):
    """Snapserver is not installed or cannot be started."""


@dataclass(frozen=True)
class SnapcastHealth:
    installed: bool
    server_running: bool
    encoder_running: bool
    stream_status: str | None
    connected_clients: tuple[str, ...]
    local_client_running: bool
    detail: str


class SnapcastManager:
    """Run a local Snapserver stream from the Network Audio PipeWire monitor."""

    CONTROL_HOST = "127.0.0.1"
    CONTROL_PORT = 1705

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or Path("~/.local/state/network-audio").expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.fifo = self.state_dir / "system-audio.pcm"
        self.config = self.state_dir / "snapserver.conf"
        self.server_pid_file = self.state_dir / "snapserver.pid"
        self.encoder_pid_file = self.state_dir / "ffmpeg.pid"
        self.client_pid_file = self.state_dir / "snapclient.pid"
        self.server: subprocess.Popen[bytes] | None = None
        self.encoder: subprocess.Popen[bytes] | None = None
        self.local_client: subprocess.Popen[bytes] | None = None

    @property
    def installed(self) -> bool:
        return self._which("snapserver") is not None

    @property
    def client_installed(self) -> bool:
        return self._which("snapclient") is not None

    def start(self) -> None:
        if not self.installed:
            raise SnapcastUnavailable(
                "Snapcast is not installed. Install the snapcast package, then restart Network Audio."
            )
        self._reap_stale_managed_processes()
        if self.is_server_alive() and self._encoder_alive():
            self._adopt_running()
            return

        # Check if control port is occupied by another unmanaged process
        if not self.is_server_alive() and self._port_open(self.CONTROL_HOST, self.CONTROL_PORT):
            raise SnapcastUnavailable(
                f"Snapcast control port {self.CONTROL_PORT} is already in use by another process."
            )

        self.stop()
        if not self.fifo.exists():
            os.mkfifo(self.fifo, 0o600)
        self.config.write_text(
            "[http]\n"
            "enabled = true\n"
            "bind_to_address = 127.0.0.1\n"
            "\n"
            "[tcp]\n"
            "enabled = true\n"
            "bind_to_address = 0.0.0.0\n"
            "\n"
            "[stream]\n"
            f"stream = pipe://{self.fifo}?name=Linux%20System%20Audio&mode=read\n",
            encoding="utf-8",
        )
        self.server = subprocess.Popen(
            ["snapserver", "-c", str(self.config), "--logging", "warning"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._write_pid(self.server_pid_file, self.server.pid)

        # Open FIFO write end with non-blocking check and explicit timeout
        try:
            fifo_writer = self._open_fifo_writer(timeout=2.0)
        except Exception:
            self.stop()
            raise

        self.encoder = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "pulse",
                "-i",
                f"{SINK_NAME}.monitor",
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "pipe:1",
            ],
            stdout=fifo_writer,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._write_pid(self.encoder_pid_file, self.encoder.pid)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                err = self._read_stderr(self.server)
                self.stop()
                raise SnapcastUnavailable(
                    f"Snapserver exited while starting.{f' {err}' if err else ''}"
                )
            if self.encoder is not None and self.encoder.poll() is not None:
                err = self._read_stderr(self.encoder)
                self.stop()
                raise SnapcastUnavailable(
                    f"FFmpeg encoder exited while starting.{f' {err}' if err else ''}"
                )
            if self._port_open(self.CONTROL_HOST, self.CONTROL_PORT):
                return
            time.sleep(0.1)

        self.stop()
        raise SnapcastUnavailable("Snapserver started but control port 1705 never became ready.")

    def _open_fifo_writer(self, timeout: float = 5.0) -> IO[bytes]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server is not None and self.server.poll() is not None:
                err = self._read_stderr(self.server)
                raise SnapcastUnavailable(
                    f"Snapserver exited while waiting for audio FIFO: {err or 'unknown error'}"
                )
            try:
                # Opening a FIFO with O_RDWR avoids blocking and ENXIO while reader initializes
                fd = os.open(self.fifo, os.O_RDWR)
                return os.fdopen(fd, "wb")
            except OSError:
                time.sleep(0.05)
        raise SnapcastUnavailable(
            f"Timeout waiting for Snapserver to open audio FIFO ({self.fifo}) for reading."
        )

    def stop(self) -> None:
        self.stop_local_client()
        self._terminate(
            self.encoder,
            self.encoder_pid_file,
            expected_name="ffmpeg",
            arg_marker="network_audio_selector.monitor",
        )
        self._terminate(
            self.server,
            self.server_pid_file,
            expected_name="snapserver",
            config_marker=str(self.config),
        )
        self.encoder = None
        self.server = None
        self._kill_matching_config_servers()

    def start_local_client(self, *, pulse_sink: str | None = None) -> str:
        if not self.client_installed:
            raise SnapcastUnavailable(
                "snapclient is not installed. Install the snapcast package to use local test mode."
            )
        if not self.is_server_alive():
            raise SnapcastUnavailable("Snapserver is not running.")
        if self._client_alive():
            return "Local Snapclient test client is already running."
        env = os.environ.copy()
        if pulse_sink:
            env["PULSE_SINK"] = pulse_sink
        self.local_client = subprocess.Popen(
            [
                "snapclient",
                "--host",
                "127.0.0.1",
                "--player",
                "pulse",
                "--logsink",
                "null",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        self._write_pid(self.client_pid_file, self.local_client.pid)
        time.sleep(0.4)
        if self.local_client.poll() is not None:
            err = self._read_stderr(self.local_client)
            self.local_client = None
            self.client_pid_file.unlink(missing_ok=True)
            raise SnapcastUnavailable(
                f"Local Snapclient exited immediately.{f' {err}' if err else ''}"
            )
        target = pulse_sink or "default PulseAudio/PipeWire sink"
        return f"Local Snapclient connected to 127.0.0.1, playing through {target}."

    def stop_local_client(self) -> None:
        self._terminate(
            self.local_client,
            self.client_pid_file,
            expected_name="snapclient",
            arg_marker="127.0.0.1",
        )
        self.local_client = None

    def status(self) -> str:
        health = self.health()
        if not health.installed:
            return "Snapcast is not installed"
        if not health.server_running:
            return "Snapcast is ready but stopped"
        clients = ", ".join(health.connected_clients) or "no clients"
        stream = health.stream_status or "unknown"
        return (
            f"Snapcast synchronized stream is running "
            f"(stream={stream}; clients={clients})"
        )

    def health(self) -> SnapcastHealth:
        installed = self.installed
        server_running = self.is_server_alive()
        encoder_running = self._encoder_alive()
        local_client = self._client_alive()
        stream_status = None
        clients: tuple[str, ...] = ()
        detail = "Snapcast is not installed"
        if installed and server_running:
            rpc = self.rpc_status()
            if rpc is not None:
                stream_status, clients = self._parse_rpc_status(rpc)
                detail = f"stream={stream_status or 'unknown'}; clients={len(clients)}"
            else:
                detail = "Snapserver is listening but RPC status is unavailable"
        elif installed:
            detail = "Snapcast is ready but stopped"
        return SnapcastHealth(
            installed=installed,
            server_running=server_running,
            encoder_running=encoder_running,
            stream_status=stream_status,
            connected_clients=clients,
            local_client_running=local_client,
            detail=detail,
        )

    def is_server_alive(self) -> bool:
        if self.server is not None and self.server.poll() is None:
            return True
        pid = self._read_pid(self.server_pid_file)
        if pid is not None and self._pid_is_process(pid, "snapserver", config_marker=str(self.config)):
            return True
        return False

    def rpc_status(self) -> dict | None:
        try:
            with socket.create_connection((self.CONTROL_HOST, self.CONTROL_PORT), timeout=1.5) as sock:
                sock.sendall(
                    b'{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}\n'
                )
                raw = self._recv_line(sock)
        except OSError:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if "result" not in payload:
            return None
        return payload["result"]

    def _adopt_running(self) -> None:
        pid = self._read_pid(self.server_pid_file)
        if pid is not None and self._pid_is_process(pid, "snapserver", config_marker=str(self.config)):
            self.server = None  # managed via pid file
        encoder_pid = self._read_pid(self.encoder_pid_file)
        if encoder_pid is not None and self._pid_is_process(
            encoder_pid, "ffmpeg", arg_marker="network_audio_selector.monitor"
        ):
            self.encoder = None

    def _encoder_alive(self) -> bool:
        if self.encoder is not None and self.encoder.poll() is None:
            return True
        pid = self._read_pid(self.encoder_pid_file)
        return pid is not None and self._pid_is_process(
            pid, "ffmpeg", arg_marker="network_audio_selector.monitor"
        )

    def _client_alive(self) -> bool:
        if self.local_client is not None and self.local_client.poll() is None:
            return True
        pid = self._read_pid(self.client_pid_file)
        return pid is not None and self._pid_is_process(pid, "snapclient", arg_marker="127.0.0.1")

    def _reap_stale_managed_processes(self) -> None:
        checks = (
            (self.encoder_pid_file, "ffmpeg", None, "network_audio_selector.monitor"),
            (self.server_pid_file, "snapserver", str(self.config), None),
            (self.client_pid_file, "snapclient", None, "127.0.0.1"),
        )
        for pid_file, name, config_marker, arg_marker in checks:
            pid = self._read_pid(pid_file)
            if pid is not None and not self._pid_is_process(
                pid, name, config_marker=config_marker, arg_marker=arg_marker
            ):
                pid_file.unlink(missing_ok=True)

    def _kill_matching_config_servers(self) -> None:
        """Stop orphaned snapservers that were started with this project's config."""
        marker = str(self.config)
        try:
            result = subprocess.run(
                ["pgrep", "-af", "snapserver"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return
        for line in result.stdout.splitlines():
            if marker not in line:
                continue
            pid_text = line.split(None, 1)[0]
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if self._pid_is_process(pid, "snapserver", config_marker=marker):
                self._signal_pid(pid, signal.SIGTERM)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self._port_open(self.CONTROL_HOST, self.CONTROL_PORT):
            time.sleep(0.1)
        for line in result.stdout.splitlines():
            if marker not in line:
                continue
            pid_text = line.split(None, 1)[0]
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if self._pid_is_process(pid, "snapserver", config_marker=marker):
                self._signal_pid(pid, signal.SIGKILL)

    @staticmethod
    def _parse_rpc_status(result: dict) -> tuple[str | None, tuple[str, ...]]:
        server = result.get("server") or result
        streams = server.get("streams") or []
        stream_status = None
        for stream in streams:
            if stream.get("id") == "Linux System Audio":
                stream_status = stream.get("status")
                break
        if stream_status is None and streams:
            stream_status = streams[0].get("status")
        clients: list[str] = []
        for group in server.get("groups") or []:
            for client in group.get("clients") or []:
                if not client.get("connected"):
                    continue
                host = client.get("host") or {}
                name = (
                    (client.get("config") or {}).get("name")
                    or host.get("name")
                    or client.get("id")
                    or "Snapclient"
                )
                ip = host.get("ip")
                clients.append(f"{name} ({ip})" if ip else str(name))
        return stream_status, tuple(clients)

    @staticmethod
    def _recv_line(sock: socket.socket) -> str:
        chunks = bytearray()
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            chunks.extend(piece)
            if b"\n" in piece:
                break
        lines = chunks.decode("utf-8", errors="replace").splitlines()
        return lines[0] if lines else ""

    @staticmethod
    def _which(name: str) -> str | None:
        from shutil import which

        return which(name)

    @staticmethod
    def _write_pid(path: Path, pid: int) -> None:
        path.write_text(f"{pid}\n", encoding="utf-8")

    @staticmethod
    def _read_pid(path: Path) -> int | None:
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None

    @classmethod
    def _pid_is_process(
        cls,
        pid: int,
        expected_name: str,
        config_marker: str | None = None,
        arg_marker: str | None = None,
    ) -> bool:
        if not expected_name or not expected_name.strip():
            return False

        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False

        # On Linux, inspect /proc/{pid}/comm and /proc/{pid}/cmdline
        comm_file = Path(f"/proc/{pid}/comm")
        cmdline_file = Path(f"/proc/{pid}/cmdline")

        if comm_file.exists():
            try:
                comm = comm_file.read_text(encoding="utf-8", errors="replace").strip()
                if expected_name not in comm and comm not in expected_name:
                    return False
            except (OSError, ProcessLookupError):
                return False

        if cmdline_file.exists():
            try:
                cmdline = cmdline_file.read_text(encoding="utf-8", errors="replace")
                if expected_name not in cmdline and comm_file.exists() and expected_name not in comm_file.read_text(encoding="utf-8", errors="replace"):
                    return False
                if config_marker is not None and config_marker not in cmdline:
                    return False
                if arg_marker is not None and arg_marker not in cmdline:
                    return False
            except (OSError, ProcessLookupError):
                return False

        return True

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False

    @staticmethod
    def _read_stderr(process: subprocess.Popen[bytes]) -> str:
        if process.stderr is None:
            return ""
        try:
            data = process.stderr.read()
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace").strip()

    def _terminate(
        self,
        process: subprocess.Popen[bytes] | None,
        pid_file: Path,
        expected_name: str,
        config_marker: str | None = None,
        arg_marker: str | None = None,
    ) -> None:
        pid = self._read_pid(pid_file)
        if process is not None:
            pid = process.pid
            if process.poll() is None:
                self._signal_pid(pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._signal_pid(pid, signal.SIGKILL)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            else:
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            if process.stdout:
                try:
                    process.stdout.close()
                except Exception:
                    pass
            if process.stderr:
                try:
                    process.stderr.close()
                except Exception:
                    pass
        elif (
            pid is not None
            and self._pid_is_process(
                pid, expected_name, config_marker=config_marker, arg_marker=arg_marker
            )
        ):
            self._signal_pid(pid, signal.SIGTERM)
            deadline = time.monotonic() + 5.0
            while (
                time.monotonic() < deadline
                and self._pid_is_process(
                    pid, expected_name, config_marker=config_marker, arg_marker=arg_marker
                )
            ):
                time.sleep(0.1)
            if self._pid_is_process(
                pid, expected_name, config_marker=config_marker, arg_marker=arg_marker
            ):
                self._signal_pid(pid, signal.SIGKILL)
        else:
            # Identity not confirmed; do NOT terminate arbitrary process
            pass
        pid_file.unlink(missing_ok=True)

    @staticmethod
    def _signal_pid(pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            return
