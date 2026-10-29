from __future__ import annotations

import subprocess


SINK_NAME = "network_audio_selector"


class AudioRouter:
    def __init__(self) -> None:
        self.module_id: int | None = None
        self.previous_default_sink: str | None = None

    def create_virtual_sink(self) -> str:
        if self.module_id is not None:
            return SINK_NAME
        current = self._default_sink()
        if current and current != SINK_NAME:
            self.previous_default_sink = current
        elif self.previous_default_sink is None:
            self.previous_default_sink = self._first_physical_sink()
        existing = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if any(f"\t{SINK_NAME}\t" in f"\t{line}\t" for line in existing):
            self.module_id = self._existing_module_id()
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
        return SINK_NAME

    def remove_virtual_sink(self) -> None:
        module_id = self.module_id or self._existing_module_id()
        if module_id is not None:
            subprocess.run(["pactl", "unload-module", str(module_id)], check=False)
        restore = self.previous_default_sink or self._first_physical_sink()
        if restore and restore != SINK_NAME:
            subprocess.run(["pactl", "set-default-sink", restore], check=False)
        self.module_id = None
        self.previous_default_sink = None

    def set_default(self) -> None:
        self.create_virtual_sink()
        subprocess.run(["pactl", "set-default-sink", SINK_NAME], check=True)

    def sink_exists(self) -> bool:
        return any(
            f"\t{SINK_NAME}\t" in f"\t{line}\t"
            for line in self._sink_lines()
        )

    def is_default(self) -> bool:
        return self._default_sink() == SINK_NAME

    def playback_sink_for_local_client(self) -> str | None:
        """Prefer a physical sink so a local Snapclient does not loop into itself."""
        if self.previous_default_sink and self.previous_default_sink != SINK_NAME:
            return self.previous_default_sink
        return self._first_physical_sink()

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
