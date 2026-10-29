# Network Audio Selector Handoff

## Purpose

Network Audio Selector is a Linux desktop application intended to route arbitrary
system audio to network speakers. The target experience is Spotify-like:
choose one or more receivers, keep applications playing normally, and send
the system mix to those receivers.

## Verified Environment

- Repository: `/home/vansh/Projects/network-audio-selector`
- Branch: `main`
- Host: Arch Linux / CachyOS (x86_64), KDE Plasma
- PipeWire 1.6.10 with PulseAudio compatibility layer
- Python 3.14.0 in `.venv`
- Native Snapcast 0.35.0 (`snapserver` and `snapclient` installed at `/usr/bin/`)
- Native FFmpeg (`/usr/bin/ffmpeg`) and PulseAudio control (`/usr/bin/pactl`)
- Local network: Discovered two Amazon Echo Dots via `_spotify-connect._tcp`:
  - `Black Echo Dot` at `10.42.0.24:4070`
  - `Vansh's Echo Dot` at `10.42.0.254:4070`

## Verified Functionality

1. **Virtual PipeWire/PulseAudio Sink & Restoration**:
   - Virtual sink creation: `network_audio_selector` with monitor `network_audio_selector.monitor`.
   - Safely adopts pre-existing virtual sink without destroying it upon exit.
   - Records and preserves previous default sink in `~/.local/state/network-audio/sink_state.json`.
   - Restores the original physical output on clean shutdown, partial startup failures, or emergency recovery.
   - If previous sink is disconnected, safely falls back to the first available physical sink.

2. **mDNS Discovery & Receiver Identification**:
   - Discovers Chromecast, AirPlay, UPnP, and Spotify Connect services.
   - Preserves all resolved IPv4 and IPv6 addresses.
   - Properly handles IPv6 endpoints with bracketed URL notation (`http://[<ipv6>]:<port>/...`).
   - Discovers Echo Dot devices via Spotify Connect `getInfo` probe and resolves friendly names (`Black Echo Dot`, `Vansh's Echo Dot`).
   - Does NOT silently assume `/zeroconf` when `CPath` is absent; records a clear diagnostic note.
   - Service removal correctly checks both protocol kind and service name.

3. **Snapcast Synchronized Audio Pipeline**:
   - Local `snapserver` configuration generated with `Linux System Audio` pipe stream.
   - FFmpeg captures 48,000 Hz 16-bit stereo PCM from `network_audio_selector.monitor` directly into the FIFO without blocking.
   - FFmpeg startup failures or early exits are caught immediately with stderr diagnostics.
   - Stale PID files are verified against `/proc/<pid>/cmdline` and reaped safely.
   - Control port conflicts (1705 occupied by an external service) are detected and reported clearly.
   - Local `snapclient` test mode routes audio through the physical hardware sink to avoid loopback feedback.

4. **Discrete Source Health Model**:
   - Six distinct health phases:
     - `idle`: source is stopped.
     - `source_active`: virtual sink created and set as default.
     - `transport_active`: Snapserver and FFmpeg encoder running.
     - `receiver_connected`: at least one Snapclient connected.
     - `playback_confirmed`: PCM audio confirmed flowing and stream is `playing`.
     - `error`: specific failure stage and error details reported.
   - Source probe tone verifies stream transition to `playing` and catches silence vs. broken pipeline.

5. **HTTP MP3 Stream**:
   - Bound to `127.0.0.1` by default for local testing; explicit warnings documented for untrusted LAN use.
   - Deterministic shutdown: closes client queues, terminates/kills FFmpeg, joins reader thread, shuts down HTTP server, and joins HTTP thread without deadlock or resource leaks.

6. **Truthful UI and Non-blocking Background Operations**:
   - Route button never says `Routing active` if all selected backends fail; displays `Routing failed` with per-device error details.
   - Shows truthful partial routing if some succeed and some fail (`Routing active (X/Y)`).
   - Echo Dot devices and Spotify Connect receivers are explicitly presented as unsupported for arbitrary desktop Linux audio.
   - Alexa browser OAuth URL processing and credential sign-in run asynchronously off the Qt GUI thread in `AlexaBrowserSignInWorker` and `AlexaSignInWorker`.

## Confirmed Boundaries and Non-arbitrary Receiver Limitations

1. **Echo / Alexa Devices**:
   - Discovered through Spotify Connect and Alexa account targets.
   - Amazon does not provide a public or local API to accept generic live PCM/MP3 streams from a Linux host.
   - Spotify Connect discovery only allows Spotify client handoff, not desktop audio forwarding.
   - Echo devices are explicitly marked as unsupported for arbitrary system audio in the UI and CLI.
   - For physical playback, a receiver must run `snapclient` or a complete ecosystem sender (e.g., Chromecast).

2. **Chromecast / AirPlay / UPnP**:
   - Chromecast backend implemented via `pychromecast` with live HTTP stream URL.
   - AirPlay and UPnP sender implementations remain stubbed as unsupported until verified against physical hardware.

## Reproducing Verification

Run the full automated test suite (27 tests):

```bash
cd /home/vansh/Projects/network-audio-selector
.venv/bin/python -m compileall -q src
.venv/bin/pytest -q
```

Run the Qt offscreen smoke test:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c \
  'from PyQt6.QtWidgets import QApplication; from network_audio_selector.ui import Selector; app=QApplication([]); w=Selector(); print("ui-ok"); w.close()'
```

Verify device discovery on the local network:

```bash
.venv/bin/network-audio devices
```

Verify local synchronized playback end-to-end:

```bash
.venv/bin/network-audio source start
# in another shell:
.venv/bin/network-audio source test-client
.venv/bin/network-audio source probe
.venv/bin/network-audio source status
.venv/bin/network-audio source stop
```
