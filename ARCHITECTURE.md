# Architecture

## Data flow

```text
Linux applications
        |
        v
PipeWire/PulseAudio default sink
        |
        v
network_audio_selector (null sink)
        |
        +--> network_audio_selector.monitor
                    |
                    +--> FFmpeg PCM --> Snapserver --> Snapclients --> speakers
                    |
                    +--> FFmpeg MP3 --> HTTP --> Chromecast experiment
```

The virtual sink is an interception point. It is not itself a network
transport. The source path is only complete when a receiver client is connected
and audible playback is confirmed.

## Receiver categories

### Snapcast

Snapcast is the primary arbitrary-audio transport. The app creates a local
Snapserver configuration and feeds it raw 48 kHz stereo signed 16-bit PCM from
the PipeWire monitor.

The app currently starts:

- `snapserver`
- `ffmpeg` reading `network_audio_selector.monitor`
- a FIFO at `~/.local/state/network-audio/system-audio.pcm`

The app does not currently start a Snapclient on a remote speaker. A Snapclient
must already exist on the receiver or on another Linux/embedded endpoint.

### Spotify Connect

The app browses `_spotify-connect._tcp.local.` and reads the advertised
`CPath`. It performs only:

```text
GET http://<address>:<port><CPath>?action=getInfo
```

This identifies compatible receivers, including Echo Dot implementations seen
on the local network. It does not claim the receiver, authenticate playback,
or inject arbitrary PCM.

### Alexa

Alexa login and account sessions are separate from local audio transport. The
integration can list devices/groups and expose account-level controls through
unofficial `alexapy` APIs. It must not be used as a raw audio backend unless a
supported transport is independently established.

### HTTP MP3

The HTTP stream is a compatibility transport for receivers that can pull a
live MP3 URL (such as Chromecast).

It provides three exposure modes via `StreamMode`:
- `loopback`: listens strictly on `127.0.0.1` (default, safe for local testing).
- `lan`: listens on the local interface address reachable from the target receiver's IP address (without blind binding or 8.8.8.8 assumptions; never advertises `0.0.0.0` or `127.0.0.1` to remote receivers).
- `custom`: caller-configured bind address and port.

It is unauthenticated and has no encryption; clear security warnings are displayed before opening network listeners. Remote receivers reject loopback stream URLs. Graceful teardown drains client queues and unblocks handler loops on shutdown.

## Lifecycle and Transactional Rollback

1. **Preflight verification**: Before altering system audio, verify that at least one selected receiver supports arbitrary system routing. If only unsupported targets (e.g. Echo Dot) are selected, abort without creating virtual sinks or touching PipeWire.
2. Create the virtual sink and record module ownership and prior default output.
3. Set `network_audio_selector` as the default output.
4. Start the selected transport (Snapcast FIFO/FFmpeg, or HTTP MP3).
5. Confirm transport and receiver health.
6. **Transactional rollback**: If transport or backend routing fails, stop newly created resources and restore the user's prior physical output immediately.
7. Stop transport with strict PID/marker validation.
8. Unload the virtual sink only after verifying that the module ID in `pactl list short modules` matches `module-null-sink` and `sink_name=network_audio_selector`.
9. Restore the previous default sink.

`SourceController` exposes these as phases: `idle` → `source_active` →
`transport_active` → `receiver_connected` → `playback_confirmed`. The UI and
`network-audio source status` report each step independently. A local
Snapclient test mode and probe tone can confirm PCM without a remote speaker.
PipeWire may suspend the monitor while silent, so stream status can return to
`idle` until an app or probe tone produces audio.

## Design constraints

- Discovery is not authorization.
- A discovered IP address is not automatically a usable audio sink.
- Echo account groups are not equivalent to Snapcast groups.
- Spotify Connect control is not equivalent to desktop-audio forwarding.
- Native Snapcast binaries are installed through the host distribution rather
  than embedded in the Python package.

