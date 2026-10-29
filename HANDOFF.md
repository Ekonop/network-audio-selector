# Network Audio Selector Handoff

## Purpose

Network Audio Selector is a Linux desktop application intended to route arbitrary
system audio to network speakers. The target experience is Spotify-like:
choose one or more receivers, keep applications playing normally, and send
the system mix to those receivers.

## Verified Environment

- Repository: `/home/vansh/Projects/network-audio-selector`
- Branch: `main` (Latest commit: `6652ebd`)
- Host: Arch Linux / CachyOS (x86_64), KDE Plasma
- PipeWire 1.6.10 with PulseAudio compatibility layer
- Python 3.14.0 in `.venv`
- Native Snapcast 0.35.0 (`snapserver` and `snapclient` installed at `/usr/bin/`)
- Native FFmpeg (`/usr/bin/ffmpeg`) and PulseAudio control (`/usr/bin/pactl`)
- BlueZ Bluetooth with paired Echo Dots (`Black Echo Dot`, `Echo Do white`)
- Cloudflare Tunnel (`~/.local/bin/cloudflared`) and ngrok support
- Local network: Discovered Amazon Echo Dots via `_spotify-connect._tcp` and Alexa account bridge (`Everywhere`, `Black Echo Dot`)

## Verified Architecture & Capabilities

1. **Virtual PipeWire/PulseAudio Sink & Module Ownership Validation**:
   - Virtual sink creation: `network_audio_selector` with monitor `network_audio_selector.monitor`.
   - Adopts pre-existing virtual sink safely without destroying it upon exit.
   - Validates module ownership prior to unloading via `pactl list short modules`.
   - Records and preserves previous default sink in `~/.local/state/network-audio/sink_state.json`.
   - Restores the original physical output on clean shutdown, partial startup failures, or emergency recovery.
   - Automatically unloads any `module-loopback` instances attached to `network_audio_selector.monitor` on sink teardown to prevent loopback leaks.

2. **mDNS Discovery & Receiver Identification**:
   - Discovers Chromecast, AirPlay, UPnP, and Spotify Connect services.
   - Preserves all resolved IPv4 and IPv6 addresses.
   - Properly handles IPv6 endpoints with bracketed URL notation (`http://[<ipv6>]:<port>/...`).
   - Discovers Echo Dot devices via Spotify Connect `getInfo` probe and resolves friendly names (`Black Echo Dot`, `Vansh's Echo Dot`).

3. **Snapcast Synchronized Audio Pipeline (Wi-Fi Multi-Room)**:
   - Local `snapserver` configuration generated with `Linux System Audio` pipe stream.
   - Non-blocking bidirectional FIFO writer (`os.O_RDWR`) eliminates startup timeout races while Snapserver initializes Avahi and control sockets.
   - FFmpeg captures 48,000 Hz 16-bit stereo PCM from `network_audio_selector.monitor` directly into the FIFO without blocking.
   - Server listens globally on `0.0.0.0:1705` and `*:1704`, enabling direct Wi-Fi connections from phones, tablets, PCs, or Raspberry Pis running Snapclient.
   - UI explicitly displays the local Wi-Fi Snapcast endpoint (`Snapcast Wi-Fi endpoint: <IP>:1704`).

4. **Amazon Alexa Account Bridge & Routing**:
   - PKCE OAuth authentication preserved (`code_verifier` and device `uuid`) across browser sign-in.
   - Full support for phone numbers with country codes (`+91...`) and email logins with regional domain selection (`amazon.com`, `amazon.in`, etc.).
   - Multi-room groups (`Everywhere`) and individual Echo devices parsed and presented with user checkboxes.
   - Dynamic route button shows selected count (`Start routing (N selected)`).

5. **Dual-Path Alexa Media Routing**:
   - **Path A: Bluetooth A2DP Loopback (Hardware)**:
     - Exact name matching against paired BlueZ devices.
     - Auto-connects Bluetooth (`bluetoothctl connect <MAC>`) and establishes low-latency `module-loopback` directly from `network_audio_selector.monitor` to `bluez_output.<MAC>.1`.
     - Zero-cloud dependency, 60 ms latency.
   - **Path B: Public HTTPS Tunnel Bridge (Wi-Fi / Cloud)**:
     - Managed via `tunnel.py` supporting both **Cloudflare Tunnel** (`cloudflared`) and **ngrok**.
     - Auto-creates a secure public HTTPS endpoint (e.g. `https://<subdomain>.trycloudflare.com/audio.mp3`) with valid TLS certificates for Amazon Echo ingestion.
     - Alexa Bridge issues playback commands (`AlexaAPI.run_custom(f"play {stream_url}")`) to stream over Wi-Fi without Bluetooth.

6. **Transactional Routing & Truthful UI**:
   - Preflight verification: if all selected speakers are unsupported, routing fails immediately before modifying audio state.
   - Transactional rollback: if routing fails mid-route, any source or stream started by that operation is stopped and previous audio output is restored.
   - Discrete health model reports real-time stream status, connected clients, and Wi-Fi endpoints.

## Reproducing Verification

Run the full automated test suite (50 tests passing):

```bash
cd /home/vansh/Projects/network-audio-selector
.venv/bin/pytest -v
```

All 50 unit and integration tests pass:
- `tests/test_alexa.py` (5 passed)
- `tests/test_audio.py` (7 passed)
- `tests/test_discovery_spotify.py` (6 passed)
- `tests/test_snapcast.py` (10 passed)
- `tests/test_source_lifecycle.py` (3 passed)
- `tests/test_stream.py` (7 passed)
- `tests/test_tunnel.py` (6 passed)
- `tests/test_ui_routing.py` (6 passed)
