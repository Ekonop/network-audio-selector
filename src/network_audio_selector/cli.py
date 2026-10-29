from __future__ import annotations

import argparse
import time

from .alexa import alexa_targets_sync
from .audio import AudioRouter
from .backends import backend_for
from .discovery import discover_sync
from .installer import SnapcastInstallError, install_snapcast
from .snapcast import SnapcastManager, SnapcastUnavailable
from .source import PHASE_LABELS, SourceController
from .stream import AudioStream


def _print_health(controller: SourceController) -> None:
    report = controller.health()
    print(f"phase\t{report.phase.value}\t{PHASE_LABELS[report.phase]}")
    for phase, ok, text in report.steps:
        print(f"{'ok' if ok else '--'}\t{phase.value}\t{text}")
    print(f"stream\t{report.stream_status or 'n/a'}")
    clients = ", ".join(report.connected_clients) or "none"
    print(f"clients\t{clients}")
    print(f"local_client\t{'running' if report.local_client_running else 'stopped'}")
    print(report.detail)


def main() -> None:
    parser = argparse.ArgumentParser(prog="network-audio")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="discover network speakers")
    sub.add_parser("enable", help="create the Network Audio virtual sink")
    sub.add_parser("stream", help="start a live HTTP audio stream")
    source = sub.add_parser("source", help="manage the source state machine")
    source.add_argument(
        "action",
        choices=["start", "stop", "status", "test-client", "stop-test-client", "probe"],
        help="start/stop source, show health, probe PCM, or manage the local Snapclient",
    )
    snapcast = sub.add_parser("snapcast", help="manage synchronized Snapcast audio")
    snapcast.add_argument(
        "action",
        nargs="?",
        choices=["start", "install", "status", "stop"],
        default="start",
    )
    alexa = sub.add_parser("alexa", help="control Alexa account targets")
    alexa.add_argument("action", choices=["targets"], help="list account devices/groups")
    route = sub.add_parser("route", help="select one or more discovered speakers")
    route.add_argument("names", nargs="+", help="speaker names")
    args = parser.parse_args()

    if args.command == "devices":
        for speaker in discover_sync():
            detail = speaker.note or "no additional information"
            print(
                f"{speaker.kind.value:16} {speaker.name} "
                f"[{speaker.address or 'unknown'}] · {detail}"
            )
        return

    router = AudioRouter()
    snapcast_manager = SnapcastManager()
    controller = SourceController(router, snapcast_manager)

    if args.command == "enable":
        print(f"Virtual sink: {router.create_virtual_sink()}")
        print("Set it in the desktop volume selector as the output device.")
        return
    if args.command == "source":
        if args.action == "status":
            _print_health(controller)
            return
        if args.action == "stop":
            controller.stop()
            _print_health(controller)
            return
        if args.action == "test-client":
            try:
                print(controller.start_local_test_client())
            except SnapcastUnavailable as exc:
                raise SystemExit(str(exc)) from exc
            _print_health(controller)
            return
        if args.action == "stop-test-client":
            controller.stop_local_test_client()
            _print_health(controller)
            return
        if args.action == "probe":
            try:
                report = controller.probe()
            except SnapcastUnavailable as exc:
                raise SystemExit(str(exc)) from exc
            print(f"phase\t{report.phase.value}\t{PHASE_LABELS[report.phase]}")
            for phase, ok, text in report.steps:
                print(f"{'ok' if ok else '--'}\t{phase.value}\t{text}")
            print(f"stream\t{report.stream_status or 'n/a'}")
            clients = ", ".join(report.connected_clients) or "none"
            print(f"clients\t{clients}")
            print(report.detail)
            return
        try:
            controller.start()
        except SnapcastUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        _print_health(controller)
        print("Source running. Press Ctrl-C to stop.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            controller.stop()
        return
    if args.command == "stream":
        router.create_virtual_sink()
        stream = AudioStream()
        print(stream.start(), flush=True)
        try:
            while True:
                input()
        except (EOFError, KeyboardInterrupt):
            stream.stop()
            router.remove_virtual_sink()
        return
    if args.command == "snapcast":
        if args.action == "install":
            try:
                print(install_snapcast())
            except SnapcastInstallError as exc:
                raise SystemExit(str(exc)) from exc
            return
        if args.action == "status":
            print(snapcast_manager.status())
            _print_health(controller)
            return
        if args.action == "stop":
            controller.stop()
            print(snapcast_manager.status())
            return
        try:
            controller.start()
            print(snapcast_manager.status(), flush=True)
            while True:
                input()
        except (EOFError, KeyboardInterrupt):
            pass
        except SnapcastUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        finally:
            controller.stop()
        return
    if args.command == "alexa":
        for speaker in alexa_targets_sync():
            print(f"{speaker.name}\t{speaker.id}\t{speaker.note}")
        return

    speakers = discover_sync()
    wanted = {name.casefold() for name in args.names}
    selected = [s for s in speakers if s.name.casefold() in wanted]
    if not selected:
        raise SystemExit("No requested speakers were discovered.")
    controller.start()
    stream = AudioStream()
    stream_url = stream.start()
    try:
        for speaker in selected:
            result = backend_for(speaker).route(speaker, stream_url)
            print(f"{speaker.name}: {result.message}")
        print("Streaming. Press Ctrl-C to stop.", flush=True)
        while True:
            input()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        stream.stop()
        controller.stop()
