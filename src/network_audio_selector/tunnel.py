from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.request import Request, urlopen


class TunnelError(RuntimeError):
    """Raised when an audio tunnel cannot be started or managed."""


class BaseTunnel(ABC):
    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether the tunnel process is actively running."""

    @abstractmethod
    def start(self, timeout: float = 15.0) -> str:
        """Start the tunnel and return the public HTTPS base URL."""

    @abstractmethod
    def stop(self) -> None:
        """Terminate the tunnel process and cleanup resources."""

    @abstractmethod
    def stream_url(self, path: str = "/audio.mp3") -> str | None:
        """Return the complete HTTPS streaming endpoint."""


class CloudflareTunnel(BaseTunnel):
    """Manages a quick Cloudflare Tunnel to expose a local HTTP audio stream via HTTPS."""

    def __init__(self, local_port: int, binary_path: str | None = None) -> None:
        self.local_port = local_port
        self.binary_path = (
            binary_path
            or shutil.which("cloudflared")
            or str(Path.home() / ".local/bin/cloudflared")
        )
        self.process: subprocess.Popen | None = None
        self.public_url: str | None = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stream_url(self, path: str = "/audio.mp3") -> str | None:
        if not self.public_url:
            return None
        norm_path = path if path.startswith("/") else f"/{path}"
        return f"{self.public_url.rstrip('/')}{norm_path}"

    def start(self, timeout: float = 15.0) -> str:
        if self.is_running and self.public_url:
            return self.public_url

        bin_file = Path(self.binary_path)
        if not shutil.which(self.binary_path) and not bin_file.is_file():
            raise TunnelError(
                f"cloudflared binary not found at '{self.binary_path}'. Please install cloudflared."
            )

        cmd = [str(bin_file), "tunnel", "--url", f"http://127.0.0.1:{self.local_port}"]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise TunnelError(f"Failed to execute cloudflared: {exc}") from exc

        url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                err = self.process.stderr.read() if self.process.stderr else ""
                self.process = None
                raise TunnelError(f"cloudflared exited unexpectedly: {err or 'unknown error'}")

            line = self.process.stderr.readline() if self.process.stderr else ""
            match = url_pattern.search(line)
            if match:
                self.public_url = match.group(0)
                return self.public_url
            time.sleep(0.05)

        self.stop()
        raise TunnelError("Timed out waiting for Cloudflare Tunnel URL.")

    def stop(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
                except Exception:
                    pass
            self.process = None
        self.public_url = None


class NgrokTunnel(BaseTunnel):
    """Manages an ngrok tunnel using the ngrok CLI and local API."""

    def __init__(self, local_port: int, binary_path: str | None = None) -> None:
        self.local_port = local_port
        self.binary_path = binary_path or shutil.which("ngrok") or "ngrok"
        self.process: subprocess.Popen | None = None
        self.public_url: str | None = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stream_url(self, path: str = "/audio.mp3") -> str | None:
        if not self.public_url:
            return None
        norm_path = path if path.startswith("/") else f"/{path}"
        return f"{self.public_url.rstrip('/')}{norm_path}"

    def start(self, timeout: float = 15.0) -> str:
        if self.is_running and self.public_url:
            return self.public_url

        if not shutil.which(self.binary_path) and not Path(self.binary_path).is_file():
            raise TunnelError(f"ngrok binary not found at '{self.binary_path}'.")

        cmd = [self.binary_path, "http", str(self.local_port), "--log=stdout"]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise TunnelError(f"Failed to execute ngrok: {exc}") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                err = self.process.stderr.read() if self.process.stderr else ""
                self.process = None
                raise TunnelError(f"ngrok exited unexpectedly: {err or 'unknown error'}")
            try:
                req = Request("http://127.0.0.1:4040/api/tunnels")
                with urlopen(req, timeout=1.0) as resp:
                    data = json.loads(resp.read().decode())
                    tunnels = data.get("tunnels", [])
                    for t in tunnels:
                        public_url = t.get("public_url", "")
                        if public_url.startswith("https://"):
                            self.public_url = public_url
                            return self.public_url
            except Exception:
                pass
            time.sleep(0.3)

        self.stop()
        raise TunnelError("Timed out waiting for ngrok tunnel endpoint.")

    def stop(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
                except Exception:
                    pass
            self.process = None
        self.public_url = None


def create_tunnel(local_port: int, provider: str = "auto") -> BaseTunnel:
    """Create a tunnel provider instance. Defaults to Cloudflare if available."""
    provider_lower = provider.lower().strip()
    if provider_lower == "ngrok":
        return NgrokTunnel(local_port=local_port)
    if provider_lower in {"cloudflare", "cloudflared"}:
        return CloudflareTunnel(local_port=local_port)

    # Auto: Prefer Cloudflare Tunnel since it requires no token/login
    cf = CloudflareTunnel(local_port=local_port)
    if Path(cf.binary_path).is_file() or shutil.which(cf.binary_path):
        return cf
    if shutil.which("ngrok"):
        return NgrokTunnel(local_port=local_port)
    return cf
