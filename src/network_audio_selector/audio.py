from __future__ import annotations

import json
from pathlib import Path
import subprocess


SINK_NAME = "network_audio_selector"


class AudioRouter:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or Path("~/.local/state/network-audio").expanduser()
        self.state_file = self.state_dir / "sink_state.json"
        self.module_id: int | None = None
        self.previous_default_sink: str | None = None
        self.created_by_us: bool = False
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                prev = data.get("previous_default_sink")
                if prev and self._sink_is_valid(prev):
                    self.previous_default_sink = prev
                self.created_by_us = bool(data.get("created_by_us", False))
                mod_id = data.get("module_id")
                if mod_id is not None:
                    try:
                        self.module_id = int(mod_id)
                    except (ValueError, TypeError):
                        self.module_id = None
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    def _save_state(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "previous_default_sink": self.previous_default_sink,
                "created_by_us": self.created_by_us,
                "module_id": self.module_id,
            }
            self.state_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _clear_state(self) -> None:
        self.state_file.unlink(missing_ok=True)

    def create_virtual_sink(self) -> str:
        existing_sinks = self._sink_names()
        sink_already_exists = SINK_NAME in existing_sinks

        current = self._default_sink()
        if current and current != SINK_NAME:
            self.previous_default_sink = current
        elif not self.previous_default_sink:
            self.previous_default_sink = self._first_physical_sink()

        if sink_already_exists:
            if self.module_id is None:
                self.module_id = self._existing_module_id()
                self.created_by_us = False
            self._save_state()
            return SINK_NAME

        result = subprocess.run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                f"sink_name={SINK_NAME}",
                "sink_properties=device.description=Network Audio",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.module_id = int(result.stdout.strip())
        self.created_by_us = True
        self._save_state()
        return SINK_NAME

    def _verify_module_for_unload(self, module_id: int) -> bool:
        """Confirm that module_id exists, is module-null-sink, and owns network_audio_selector."""
        result = subprocess.run(
            ["pactl", "list", "short", "modules"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t") if "\t" in line else line.split(None, 2)
            if len(parts) >= 2:
                try:
                    mod_id = int(parts[0].strip())
                except ValueError:
                    continue
                if mod_id == module_id:
                    mod_type = parts[1].strip()
                    mod_args = parts[2].strip() if len(parts) >= 3 else ""
                    return mod_type == "module-null-sink" and f"sink_name={SINK_NAME}" in mod_args
        return False

    def cleanup_loopbacks(self) -> None:
        try:
            res = subprocess.run(
                ["pactl", "list", "short", "modules"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and parts[1] == "module-loopback" and SINK_NAME in parts[2]:
                    subprocess.run(["pactl", "unload-module", parts[0]], check=False)
        except Exception:
            pass

    def remove_virtual_sink(self) -> None:
        self.cleanup_loopbacks()
        # Load any saved state if we don't have it in memory
        if self.previous_default_sink is None and self.state_file.exists():
            self._load_state()

        # Only unload the module if we created it ourselves AND its identity is verified
        if self.created_by_us and self.module_id is not None:
            if self._verify_module_for_unload(self.module_id):
                subprocess.run(["pactl", "unload-module", str(self.module_id)], check=False)

        restore = self.previous_default_sink
        if not restore or not self._sink_is_valid(restore):
            restore = self._first_physical_sink()

        if restore and restore != SINK_NAME and self._sink_is_valid(restore):
            subprocess.run(["pactl", "set-default-sink", restore], check=False)

        self.module_id = None
        self.created_by_us = False
        self.previous_default_sink = None
        self._clear_state()

    def set_default(self) -> None:
        self.create_virtual_sink()
        current = self._default_sink()
        if current and current != SINK_NAME and not self.previous_default_sink:
            self.previous_default_sink = current
            self._save_state()
        subprocess.run(["pactl", "set-default-sink", SINK_NAME], check=True)

    def sink_exists(self) -> bool:
        return SINK_NAME in self._sink_names()

    def is_default(self) -> bool:
        return self._default_sink() == SINK_NAME

    def playback_sink_for_local_client(self) -> str | None:
        """Prefer a physical sink so a local Snapclient does not loop into itself."""
        if (
            self.previous_default_sink
            and self.previous_default_sink != SINK_NAME
            and self._sink_is_valid(self.previous_default_sink)
        ):
            return self.previous_default_sink
        return self._first_physical_sink()

    def _sink_is_valid(self, name: str) -> bool:
        return name in self._sink_names()

    def _sink_names(self) -> list[str]:
        names: list[str] = []
        for line in self._sink_lines():
            fields = line.split("\t")
            if len(fields) >= 2:
                names.append(fields[1])
        return names

    @staticmethod
    def _default_sink() -> str | None:
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            check=False,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value or None

    @staticmethod
    def _sink_lines() -> list[str]:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.splitlines()

    def _first_physical_sink(self) -> str | None:
        for line in self._sink_lines():
            fields = line.split("\t")
            if len(fields) >= 2 and fields[1] != SINK_NAME:
                return fields[1]
        return None

    @staticmethod
    def _existing_module_id() -> int | None:
        result = subprocess.run(
            ["pactl", "list", "short", "modules"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            fields = line.split("\t", 2)
            if len(fields) == 3 and fields[1] == "module-null-sink":
                if f"sink_name={SINK_NAME}" in fields[2]:
                    return int(fields[0])
        return None

    def list_sink_inputs(self) -> str:
        return subprocess.run(
            ["pactl", "list", "short", "sink-inputs"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def start_probe_tone(self, seconds: float = 2.0) -> subprocess.Popen[bytes]:
        """Start a short tone into the virtual sink so Snapcast can leave idle."""
        self.create_virtual_sink()
        return subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=880:duration={seconds}",
                "-f",
                "pulse",
                SINK_NAME,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
