# Network Audio Selector Handoff

## Purpose

Network Audio Selector is a Linux desktop app intended to route arbitrary
system audio to network speakers. The target experience is Spotify-like:
choose one or more receivers, keep applications playing normally, and send
the system mix to those receivers.

## Current product decision

The project uses a hybrid architecture:

1. **Snapcast** is the transport for arbitrary Linux system audio and
   synchronized playback.
2. **Chromecast, AirPlay, and UPnP** are ecosystem-specific adapters. Discovery
   exists, but sender support is incomplete except for the initial Chromecast
   path.
3. **Alexa/Echo** is an account-control integration only. Amazon's consumer
   Alexa APIs do not expose a supported arbitrary desktop-audio input for Echo
   devices or groups.
4. **Spotify Connect discovery** identifies receivers but does not turn them
   into generic Linux audio sinks.

Do not present Alexa or Spotify Connect discovery as proof that raw Linux audio
can be sent to Echo devices.

## Verified environment

- Project: `/home/vansh/Projects/network-audio-selector`
- Python environment: `.venv`
- PipeWire 1.6.10 with PulseAudio compatibility
- Python 3.14
- Snapcast 0.35.0 installed from AUR
- Snapserver and Snapclient binaries are available
- Two local `_spotify-connect._tcp` advertisements were observed:
  - `10.42.0.24:4070/spotifyConnect`
  - `10.42.0.254:4070/spotifyConnect`
- Both returned valid Spotify `getInfo` responses identifying Amazon `Echo_Dot`

## What is currently working

- Virtual PipeWire/PulseAudio sink creation:
  - Sink: `network_audio_selector`
  - Monitor: `network_audio_selector.monitor`
- Default sink switching and restoration.
- mDNS discovery for Chromecast, AirPlay/RAOP, UPnP/DIAL, and Spotify Connect.
- Read-only Spotify Connect `getInfo` identification.
- Snapserver configuration generation.
- FFmpeg capture from the virtual sink monitor into a Snapcast PCM FIFO.
- Local Snapserver startup with control-port readiness checks.
- **Source state machine** with distinct phases:
  - `idle`
  - `source_active`
  - `transport_active`
  - `receiver_connected`
  - `playback_confirmed`
- Health indicators in the UI and via `network-audio source status`.
- **Local Snapclient test mode** that plays through the previous physical sink
  (avoids feedback into the virtual sink).
- Verified end-to-end path: source start → local Snapclient →
  `playback_confirmed` with stream status `playing`.
- Explicit start/stop source controls and cleanup on app close.
- Discovery and source start/stop moved off the Qt GUI thread.
- PyQt6 UI construction and CLI startup checks.
- Alexa session/login boundary and account target listing when a valid session is
  available.

## What is not complete

- The UI does not yet provision or launch Snapclients on Echo devices.
- Echo groups such as `Everywhere` and `Bedroom` cannot be used as arbitrary
  Linux audio outputs through the current public/private API boundary.
- Chromecast live routing has not been validated against a physical Chromecast.
- AirPlay and UPnP sender implementations are incomplete.
- Alexa login still has interactive/browser steps on the GUI thread.
- The HTTP MP3 stream is unauthenticated and should remain a trusted-LAN
  experiment, not the primary transport.
- Remote Snapclient provisioning / second-machine documentation still needs a
  polished guided flow in the UI.

## Reproduce the current state

```bash
cd /home/vansh/Projects/network-audio-selector
.venv/bin/python -m compileall -q src
.venv/bin/network-audio devices
.venv/bin/network-audio source status
QT_QPA_PLATFORM=offscreen .venv/bin/python -c \
  'from PyQt6.QtWidgets import QApplication; from network_audio_selector.ui import Selector; app=QApplication([]); w=Selector(); print("ui-ok"); w.close()'
```

Start the UI:

```bash
.venv/bin/network-audio-ui
```

Verify the synchronized source path end-to-end on this machine:

```bash
.venv/bin/network-audio source start
# in another terminal:
.venv/bin/network-audio source test-client
.venv/bin/network-audio source probe
.venv/bin/network-audio source status
# expect phase=playback_confirmed after the probe tone
.venv/bin/network-audio source stop
```

If no app is playing into the virtual sink, PipeWire may suspend the
monitor and Snapcast stays `idle` even with a connected client. Use
`source probe` (or the UI **Probe tone** button) to inject a short tone.

## Recommended next implementation order

1. Document / UI-guide a second Linux Snapclient on another machine.
2. Move remaining Alexa interactive login work off the GUI thread where practical.
3. Harden or remove the unauthenticated HTTP stream.
4. Add receiver-specific adapters only after their transport and permissions are
   independently verified.
5. Validate Chromecast routing against a physical device.

## Important files

| File | Responsibility |
|---|---|
| `src/network_audio_selector/ui.py` | Qt UI and user flow |
| `src/network_audio_selector/source.py` | Source lifecycle state machine + health |
| `src/network_audio_selector/workers.py` | Background discovery/source workers |
| `src/network_audio_selector/audio.py` | Virtual sink and default output |
| `src/network_audio_selector/snapcast.py` | Snapserver, FIFO, FFmpeg, local Snapclient |
| `src/network_audio_selector/discovery.py` | mDNS/DNS-SD discovery |
| `src/network_audio_selector/spotify.py` | Read-only Spotify Connect probing |
| `src/network_audio_selector/backends.py` | Receiver routing adapters |
| `src/network_audio_selector/alexa.py` | Alexa account/session controls |
| `src/network_audio_selector/stream.py` | Experimental HTTP MP3 stream |
| `src/network_audio_selector/cli.py` | Command-line entry point |
