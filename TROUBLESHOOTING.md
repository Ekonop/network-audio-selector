# Troubleshooting

## The app says audio is active but no speaker plays

Check the source state machine first. Transport active is not the same as
playback confirmed:

```bash
.venv/bin/network-audio source status
pactl get-default-sink
pactl list short sinks
pactl list short sources
```

The default sink should be `network_audio_selector` while the source is active,
and `network_audio_selector.monitor` should exist. Expected phases:

1. `source_active` — virtual sink is default
2. `transport_active` — Snapserver + FFmpeg capture are running
3. `receiver_connected` — at least one Snapclient is connected
4. `playback_confirmed` — stream status is `playing` with a client

On this machine you can verify the full path with:

```bash
.venv/bin/network-audio source start
.venv/bin/network-audio source test-client
.venv/bin/network-audio source status
```

A running Snapserver alone does not produce sound. A Snapclient must connect to
the server and play the `Linux System Audio` stream.

## Snapcast is installed but the app says unavailable

Check the executable visible to the same user/environment:

```bash
command -v snapserver
snapserver --version
.venv/bin/network-audio snapcast status
```

Restart the UI after installing a native package.

## Snapcast AUR installation fails

The app-managed installer uses `yay` or `paru` on Arch-like systems. If the
package builds but installation fails because `sudo` needs a terminal, install
the generated package with graphical privilege escalation:

```bash
pkexec pacman -U --noconfirm \
  ~/.cache/yay/snapcast/snapcast-*-x86_64.pkg.tar.zst
```

If compilation reports `boost/version.hpp` missing, install the development
package:

```bash
pkexec pacman -S --needed --noconfirm boost
```

## No Echo devices appear

Spotify Connect discovery depends on local multicast traffic. Check:

```bash
avahi-browse -rt _spotify-connect._tcp
.venv/bin/network-audio devices
```

Wi-Fi client isolation, VLAN boundaries, firewall rules, and multiple network
interfaces can prevent discovery. Discovery also does not guarantee arbitrary
audio support.

## Echo appears but cannot be selected for Linux audio

This is intentional. Echo devices discovered through Spotify Connect are shown
as informational receivers. Alexa account targets are shown separately.
Neither path currently provides a supported arbitrary desktop-audio input.

Use Snapcast with a receiver that can run `snapclient`, or use a complete
ecosystem-specific adapter.

## The UI freezes during Alexa login

Discovery and source start/stop now run on worker threads. Some Alexa browser
and password dialogs still perform blocking network work on the GUI thread after
the user confirms a dialog. If the window freezes during Alexa sign-in, wait for
the request to finish or cancel and retry.

## Restore the physical audio output

Close the app normally. It should restore the sink that was active before
Network Audio started. If the process was killed:

```bash
pactl list short sinks
pactl set-default-sink <physical-sink-name>
```

Do not unload a sink that belongs to another application.

## Useful logs and state

```bash
ls -la ~/.local/state/network-audio/
sed -n '1,120p' ~/.local/state/network-audio/snapserver.conf
```

The HTTP stream is intentionally not covered by this runbook for production
use because it currently has no authentication or encryption.

