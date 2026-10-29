# Troubleshooting

## Diagnostic Sequence

Run this sequence to diagnose each layer independently:

### 1. Device discovery

```bash
.venv/bin/python -m compileall -q src
.venv/bin/network-audio devices
avahi-browse -rt _spotify-connect._tcp
```

- Discovered Spotify Connect devices (e.g., Echo Dot) show resolved friendly names, IP addresses, and hardware models.
- If an advertisement lacks `CPath` or is malformed, a clear diagnostic note (`missing CPath metadata`) is displayed rather than silently assuming `/zeroconf`.
- Both IPv4 and IPv6 addresses are preserved and probed; bracket notation is used for IPv6 HTTP endpoints.

### 2. Audio source state machine and phases

```bash
.venv/bin/network-audio source status
pactl get-default-sink
pactl list short sinks
pactl list short sources
```

The health model reports the following discrete phases:
- `idle`: Source is stopped.
- `source_active`: Virtual sink `network_audio_selector` is created and default.
- `transport_active`: Local Snapserver and FFmpeg capture are running.
- `receiver_connected`: At least one Snapclient receiver is connected.
- `playback_confirmed`: PCM is actively flowing and stream status is `playing`.
- `error`: One of the stages failed (virtual sink creation, Snapcast transport, etc.).

### 3. Verify end-to-end synchronized playback on this machine

```bash
.venv/bin/network-audio source start
# in another terminal:
.venv/bin/network-audio source test-client
.venv/bin/network-audio source probe
.venv/bin/network-audio source status
# expect phase=playback_confirmed during or right after audio
.venv/bin/network-audio source stop
```

PipeWire may suspend the null sink monitor during silence, returning the stream status to `idle`. Use `network-audio source probe` or the UI **Probe tone** button to inject a 2.5-second test tone to verify PCM capture.

### 4. Audio sink state preservation and restoration

State is persisted safely in `~/.local/state/network-audio/sink_state.json`.
- When `network_audio_selector` is created, the previous default sink and creation ownership (`created_by_us`) are saved.
- If `network_audio_selector` pre-existed or belongs to another process, Network Audio adopts it safely and **does not** unload it upon shutdown.
- Before unloading a module via `pactl unload-module`, Network Audio inspects `pactl list short modules` to verify that the module ID still corresponds to `module-null-sink` with `sink_name=network_audio_selector`. This prevents killing recycled module IDs or another process's audio modules.
- Preflight validation: If only unsupported receivers are selected, routing fails before touching PipeWire or creating sinks.
- Transactional rollback: If routing fails mid-route, any source or stream started by that attempt is cleanly stopped and the original physical sink is restored.
- When stopping or on partial startup failure, the previous default output is restored automatically.
- If the previous sink is no longer available (e.g. disconnected USB DAC), Network Audio falls back to the first available physical sink.

Manual emergency recovery:
```bash
pactl list short sinks
pactl set-default-sink <physical-sink-name>
```

### 5. Snapcast process ownership, FIFO handling, and port conflicts

- Non-blocking FIFO writer: Network Audio opens the PCM audio FIFO using non-blocking I/O and bounded timeouts. It actively checks if `snapserver` has crashed, immediately raising `SnapcastUnavailable` rather than hanging on a blocking open.
- Control port 1705: Network Audio only manages Snapserver processes started with its configuration (`~/.local/state/network-audio/snapserver.conf`).
- If an unmanaged service already occupies port 1705, Network Audio will refuse to start and will report a clear port conflict error rather than adopting or terminating the unrelated process.
- Strict process termination: Stale PID files in `~/.local/state/network-audio/` are validated against `/proc/<pid>/comm` and `/proc/<pid>/cmdline` requiring expected names and config/arg markers (`snapserver`, `ffmpeg`, `snapclient`). Reaped or recycled PIDs are never signaled.
- FFmpeg capture errors (e.g. missing sink monitor) are detected immediately and reported with the process stderr.

### 6. Alexa account targets vs. arbitrary audio

- Amazon Echo devices discovered via Spotify Connect or Alexa account APIs do not accept generic raw PCM/MP3 LAN audio from Linux.
- The UI and CLI explicitly separate informational discovery from routeability.
- In the UI, selecting unsupported devices will keep the route button truthful: if all selected backends fail, the button displays `Routing failed` (never `Routing active`).
- Alexa credential sign-in and browser OAuth URL exchanges execute asynchronously in background worker threads (`AlexaSignInWorker`, `AlexaBrowserSignInWorker`), preventing the Qt main thread from freezing.

### 7. HTTP MP3 live stream and exposure modes

The HTTP MP3 stream is a compatibility transport.
- Explicit modes:
  - `loopback`: Binds strictly to `127.0.0.1` (safe default for local testing).
  - `lan`: Determines the route to the destination receiver's IP and binds to that local interface IP. Remote receivers (e.g. Chromecast) reject loopback or `0.0.0.0` addresses.
  - `custom`: Binds to a user-provided `--bind` address.
- WARNING: The HTTP stream has no encryption or authentication; clear warnings are shown before exposing unauthenticated audio to the LAN.
- Start/stop cycles are deterministic and cleanly release threads, sockets, and FFmpeg subprocesses. Subscriber queues are drained on shutdown to prevent worker threads from hanging on full queues.

### 8. Useful logs and runtime state

```bash
ls -la ~/.local/state/network-audio/
cat ~/.local/state/network-audio/sink_state.json
cat ~/.local/state/network-audio/snapserver.conf
```
