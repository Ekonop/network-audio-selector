from __future__ import annotations

import argparse
import time

from .alexa import alexa_targets_sync
from .audio import AudioRouter
from .backends import backend_for
from .discovery import discover_sync
from .installer import SnapcastInstallError, install_snapcast
from .models import SpeakerKind
from .snapcast import SnapcastManager, SnapcastUnavailable
from .source import PHASE_LABELS, SourceController
from .stream import AudioStream, StreamMode


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
    stream_parser = sub.add_parser("stream", help="start a live HTTP audio stream")
    stream_parser.add_argument(
        "--mode",
        choices=["loopback", "lan", "custom"],
        default="loopback",
        help="network exposure mode (default: loopback)",
    )
    stream_parser.add_argument("--bind", default=None, help="explicit bind address")
    stream_parser.add_argument("--port", type=int, default=0, help="stream port")

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
        stream_mode = StreamMode(args.mode)
        stream = AudioStream(
            port=args.port,
            bind_address=args.bind,
            mode=stream_mode,
        )
        if stream_mode is not StreamMode.LOOPBACK:
            print("WARNING: Live HTTP stream is unauthenticated and unencrypted.", flush=True)
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

    # Preflight check: reject if none can be routed
    routable = [s for s in selected if backend_for(s).can_route(s)]
    if not routable:
        for s in selected:
            print(f"{s.name}: {backend_for(s).route(s).message}")
        raise SystemExit("None of the requested speakers support arbitrary system audio.")

    # Check for remote Chromecast receiver
    receiver_address = None
    for c in selected:
        if c.kind is SpeakerKind.CHROMECAST and c.address and c.address not in ("127.0.0.1", "localhost", "::1"):
            receiver_address = c.address
            break

    controller.start()
    if receiver_address:
        print("WARNING: Opening unauthenticated LAN stream for remote receiver.", flush=True)
        stream = AudioStream(mode=StreamMode.LAN, receiver_address=receiver_address)
    else:
        stream = AudioStream(mode=StreamMode.LOOPBACK)

    stream_url = stream.start()
    try:
        results = [backend_for(speaker).route(speaker, stream_url) for speaker in selected]
        succeeded = [r for r in results if r.ok]
        for r in results:
            print(f"{r.speaker.name}: {r.message}")
        if not succeeded:
            stream.stop()
            controller.stop()
            raise SystemExit("Routing failed for all selected speakers.")
        print("Streaming. Press Ctrl-C to stop.", flush=True)
        while True:
            input()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        stream.stop()
        controller.stop()
