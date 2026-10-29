from __future__ import annotations

import queue
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO

from .audio import SINK_NAME


class _StreamState:
    def __init__(self) -> None:
        self.clients: set[queue.Queue[bytes | None]] = set()
        self.lock = threading.Lock()

    def add(self) -> queue.Queue[bytes | None]:
        client: queue.Queue[bytes | None] = queue.Queue(maxsize=32)
        with self.lock:
            self.clients.add(client)
        return client

    def remove(self, client: queue.Queue[bytes | None]) -> None:
        with self.lock:
            self.clients.discard(client)

    def publish(self, data: bytes) -> None:
        with self.lock:
            clients = tuple(self.clients)
        for client in clients:
            try:
                client.put_nowait(data)
            except queue.Full:
                self.remove(client)

    def close(self) -> None:
        with self.lock:
            clients = tuple(self.clients)
        for client in clients:
            try:
                client.put_nowait(None)
            except queue.Full:
                pass


class _Handler(BaseHTTPRequestHandler):
    server: "_AudioHTTPServer"

    def do_GET(self) -> None:
        if self.path != "/audio.mp3":
            self.send_error(404)
            return
        client = self.server.state.add()
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                chunk = client.get()
                if chunk is None:
                    return
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.server.state.remove(client)

    def log_message(self, *_: object) -> None:
        return


class _AudioHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: _StreamState) -> None:
        super().__init__(address, _Handler)
        self.state = state


class AudioStream:
    """Encode the virtual sink monitor once and fan it out to network clients."""

    def __init__(self, port: int = 0) -> None:
        self.port = port
        self._state = _StreamState()
        self._server: _AudioHTTPServer | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._http_thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Audio stream is not started")
        host = self._local_ip()
        return f"http://{host}:{self._server.server_port}/audio.mp3"

    def start(self) -> str:
        if self._server is not None:
            return self.url
        self._server = _AudioHTTPServer(("0.0.0.0", self.port), self._state)
        self._process = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "pulse",
                "-i",
                f"{SINK_NAME}.monitor",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-f",
                "mp3",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._http_thread = threading.Thread(
            target=self._server.serve_forever,
            name="network-audio-http",
            daemon=True,
        )
        self._http_thread.start()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.url

    def _serve(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        reader: IO[bytes] = self._process.stdout
        try:
            while True:
                data = reader.read(4096)
                if not data:
                    break
                self._state.publish(data)
        finally:
            self._state.close()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._http_thread = None
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=5)
            self._process = None
        self._thread = None

    @staticmethod
    def _local_ip() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            probe.close()
