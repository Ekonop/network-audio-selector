# Network Audio Selector

This project provides a Linux virtual audio sink, a Qt selector, and CLI/service
entry points for routing audio to compatible network receivers.

Project handoff and operational documentation:

- [Handoff](HANDOFF.md) — verified state, limitations, and next implementation order
- [Architecture](ARCHITECTURE.md) — audio data flow and receiver boundaries
- [Troubleshooting](TROUBLESHOOTING.md) — setup, diagnostics, and known failure modes

## Important device limitation

Echo/Alexa devices do not expose a generic local-network receiver for arbitrary
PCM audio. Spotify can control Alexa through authenticated Spotify/Alexa cloud
integrations, but a Linux system-audio selector cannot simply send raw audio to
an Echo IP address. This project therefore reports Echo devices honestly and
leaves account/group control to a future Alexa bridge.

Chromecast, AirPlay, and UPnP discovery are included, but each transport needs
its own sender implementation. For synchronized arbitrary system audio, the
planned hybrid architecture uses Snapcast-compatible clients as the reliable
core, with ecosystem-specific adapters alongside it.

Spotify Connect receivers are also discovered through the documented
`_spotify-connect._tcp` DNS-SD service. When advertised, Network Audio performs
only the read-only `getInfo` request used to identify the receiver. This does
not imply that an Echo or another receiver accepts arbitrary LAN audio:
Spotify Connect normally controls a receiver's supported Spotify playback
integration. Use Snapcast for arbitrary Linux system audio.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
network-audio devices
network-audio-ui
```

The service creates a `Network Audio` virtual sink and publishes its monitor as
a live MP3 stream for compatible network receivers:

```bash
network-audio-service
```

Chromecast routing can be tested with:

```bash
network-audio route "Living Room"
```

The stream is intended for LAN use only. It has no authentication or encryption;
do not expose the service port outside your trusted network.

## Alexa account targets

Alexa has no public API for receiving arbitrary live PCM/MP3 audio from a Linux
computer. The optional Alexa bridge can authenticate to the account and list Echo devices
and multi-room groups such as `Everywhere` and `Bedroom`. Alexa targets are
account-control targets only; they are not shown as arbitrary Linux audio
outputs. Amazon does not provide a public API for injecting desktop audio into
existing Echo groups.

Install the optional dependency and provide the account email:

```bash
.venv/bin/pip install -e '.[alexa]'
export NETWORK_AUDIO_ALEXA_EMAIL='you@example.com'
.venv/bin/network-audio alexa targets
```

The bridge reads an alexapy session cookie from
`~/.config/network-audio/alexa`. Do not put an Amazon password or cookie in
source code, shell history, or bug reports. alexapy uses an unofficial private
API and may stop working if Amazon changes it.

For a user service after installing the project, copy
`share/systemd/user/network-audio.service` to
`~/.config/systemd/user/`, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now network-audio.service
```

## Synchronized audio with Snapcast

Snapcast is the recommended transport for synchronized arbitrary Linux audio.
Network Audio manages the native Snapcast package as part of the app setup.
The UI has an **Install Snapcast** action, or install it from the CLI:

```bash
network-audio snapcast install
network-audio snapcast
```

Connect Snapcast clients to this computer and select the `Linux System Audio`
stream. Chromecast, AirPlay, UPnP, and Alexa remain separate ecosystem
integrations and cannot be synchronized reliably with Snapcast clients.

The package is installed through the host distribution (`pacman`, `apt-get`,
`dnf`, or `zypper`) because Snapcast is a native daemon, not a Python library.
The app never downloads or embeds an architecture-specific binary.
