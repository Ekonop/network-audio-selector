from __future__ import annotations

import signal
import time

from .audio import AudioRouter
from .stream import AudioStream
from .snapcast import SnapcastManager, SnapcastUnavailable


def main() -> None:
    router = AudioRouter()
    router.create_virtual_sink()
    stream = AudioStream()
    stream.start()
    snapcast = SnapcastManager()
    try:
        snapcast.start()
    except SnapcastUnavailable as exc:
        print(f"Snapcast unavailable: {exc}", flush=True)
    stopped = False

    def stop(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Network Audio stream is ready at {stream.url}", flush=True)
    try:
        while not stopped:
            time.sleep(1)
    finally:
        stream.stop()
        snapcast.stop()
        router.remove_virtual_sink()
