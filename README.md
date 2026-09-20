# Bowers & Wilkins Zeppelin — Home Assistant Integration

Custom [HACS](https://hacs.xyz/) integration for controlling B&W Formation/Zeppelin speakers over the local network.

Built by reverse-engineering the B&W Splice Android app. All communication is local — no cloud, no account required.

---

## About this fork

Fork of [space192/zeppelin](https://github.com/space192/zeppelin) maintained by
[@torchtarget](https://github.com/torchtarget), adding **Formation** support. Upstream is
developed against a Zeppelin Pro; this fork is developed and tested against a real
Formation mesh of three speakers (2× Formation Flex, 1× Formation Wedge) on
Home Assistant 2026.8.x, with Spotify Connect and Roon as sources.

The four fixes below are offered back upstream in
[`fix/formation-mesh-support`](https://github.com/torchtarget/zeppelin/tree/fix/formation-mesh-support).
See [docs/FORMATION.md](docs/FORMATION.md) for the measured protocol notes behind them.

### What this fork changes

| # | Problem | Cause | Fix |
|---|---|---|---|
| 1 | Adding **any** speaker fails with *"Unknown error occurred"* on v0.5.0–v0.5.2 | `get_version()` was lost in a merge — its body was left dangling at the end of `set_audio_output_delay()`, so the config flow raised `AttributeError` | Method restored; the config flow also maps unexpected exceptions to `cannot_connect` so a future slip shows a real error |
| 2 | With several speakers, entries get the **wrong name and node id**, and the second and third are rejected as duplicates | `GET /1/mesh/nodes` returns *every* member of the mesh, and the flow used `nodes[0]` | Ask the speaker which node it is (`GET /mesh/node`) and pick that entry; falls back to `nodes[0]` for a single speaker |
| 3 | **Every speaker shows the same track** — whatever any one of them last played | Each member's WebSocket relays the other members' messages; the client took the first tile in the map regardless of sender | Filter audiotile, artwork and volume by the speaker's own node id, matching `sinkNodeIDs` too so a grouped speaker still follows its group. An empty tile now returns the player to idle |
| 4 | Players report **volume 0.0** until someone changes the volume | The initial volume was requested before the WebSocket carrying the reply was connected, so the answer was dropped | Wait for the connection, then request volume *and* the current track, so a speaker that is already playing shows it straight away |

Fork-only, not part of the upstream pull request:

- **Corrected model names.** `liberty.lcms` is a Formation **Flex** (not "Formation Solo") and
  `liberty.ps1` is a Formation **Wedge** (not "Formation Duo"), per the `ModelNumber` and
  AirPlay records of real hardware. Before this, Home Assistant labelled the speakers wrongly.
- **Version `0.5.3-formation.1`**, which sorts above upstream 0.5.2 and below a future
  upstream 0.5.3, so HACS offers this build now and steps aside when upstream ships its own.

### Known-dead on Formation

- **The LED light entity does nothing.** `liberty.lights.hardware-downlight` returns `null`
  on all three Formation models tested; the downlight is a Zeppelin feature. The entity is
  still created — removing it is a breaking change deferred to a later release.
- **Audio Output Delay does nothing.** It uses the StreamSDK API on port 80, which Formation
  speakers do not open. Only ports 7000 (AirPlay) and 42425 (StateD) are listening. Setup
  tolerates the failure.

---

## Supported Devices

| Device | Type ID |
|---|---|
| Zeppelin Pro | `com.bowerswilkins.liberty.zpr` |
| Zeppelin | `com.bowerswilkins.liberty.zep` |
| Panorama 3 | `com.bowerswilkins.liberty.alb` |
| Formation Wedge | `com.bowerswilkins.liberty.ps1` |
| Formation Flex | `com.bowerswilkins.liberty.lcms` |
| Formation Bar | `com.bowerswilkins.liberty.sb1` |
| Formation Bass | `com.bowerswilkins.liberty.sw1` |
| Formation Audio | `com.bowerswilkins.liberty.connect` |

| Formation Duo | `com.bowerswilkins.liberty.st1` (unconfirmed) |

> Upstream is tested on Zeppelin Pro. This fork is tested on Formation Flex and Formation
> Wedge. The type ids for Duo, Bar, Bass and Audio are inherited from upstream and remain
> unconfirmed — if yours is mislabelled in Home Assistant, please open an issue with the
> `type` reported by `GET /1/mesh/nodes` and the model on the speaker's label.

## Features

### LED Light Control

Exposed as a standard `light` entity with:
- On / Off
- RGB color picker (full 0-255 range per channel)
- Brightness slider (0-100%)

### Audio Output Delay (AirPlay 2 sync fix)

Exposed as a `number` entity (**Audio Output Delay**, milliseconds, −1000 to +1000, 1 ms steps, text input).

If your Zeppelin plays a fixed amount **ahead of** your other speakers in a mixed-brand AirPlay 2 group, the speaker over-reports its output latency and the sender ships audio too early. This entity writes the speaker's undocumented `audioOutputDelay` setting (via the local StreamSDK API on port 80): a **negative** value lowers the reported latency so the sender delays the audio and it lands back in sync. The current value is read on startup and shown in Home Assistant.

- **Units**: the entity is in milliseconds for readability; the speaker API stores microseconds.
- **Persists** across a full power cycle (verified). Set to `0` to revert to factory behaviour.
- **Applies at stream start**, not mid-playback — change the value, then stop and restart playback to hear the effect.
- Only fixes a *consistent fixed offset* (early or late). It will not correct random Wi-Fi drift.
- Tune by ear: type a value, restart playback, and listen. Many users land around **−150 ms**; the ideal value depends on your speaker mix.

### Firmware Update Check

Checks for firmware updates once per night at a random time between 3:00 and 5:59 AM. If an update is available, a persistent notification is created in Home Assistant with the version number and release notes.

## How It Works

The integration communicates with the speaker over its local REST API on port **42425** (HTTPS with self-signed certificate). Devices are discovered via the speaker's mesh node list.

The audio output delay setting uses a **second, separate HTTP API on port 80** (the StreamSDK API, plain HTTP), at `/api/getData` and `/api/setData`. This API is independent of the StateD protocol and is only used for the `audioOutputDelay` setting.

The StateD protocol is a JSON-RPC-like system, sent to:

```
POST /mesh/node/{nodeId}/channel/com.bowerswilkins.stated.service+provider/message
```

```json
{
  "type": "query",
  "method": {
    "name": "get_property",
    "parameters": {
      "property": "liberty.lights.hardware-downlight"
    }
  }
}
```

No polling — the integration fetches the LED state once on startup and tracks it locally after that.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "Bowers & Wilkins Zeppelin"
3. Restart Home Assistant

### Manual

Copy `custom_components/bw_zeppelin` to your Home Assistant `custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Integrations → Add Integration**
2. Search for "Bowers & Wilkins Zeppelin"
3. Enter the speaker's IP address
4. The integration auto-discovers the speaker name and node ID

> Tip: assign a static IP or DHCP reservation to your speaker so the address doesn't change.

## Known Limitations

- The speaker uses a self-signed TLS certificate — SSL verification is disabled for local communication.
- The Splice app may not discover speakers connected via Ethernet. This integration works fine over Ethernet.
- LED control, EQ, media player, and firmware update checks are implemented. Source control,
  seek, shuffle and repeat are possible via the same API but not yet exposed — see
  [docs/FORMATION.md](docs/FORMATION.md) for what is confirmed to exist.
- On Formation speakers the LED light and Audio Output Delay entities are inert; see
  *Known-dead on Formation* above.

## API Reference

The speaker exposes more capabilities that could be added in the future:

| Feature | API |
|---|---|
| Volume | `set_volume` / `get_volume` (0-100) |
| EQ | `liberty.property.gain.treble` / `.bass` / `.offset` |
| Playback | `liberty.command.play_pause` / `next_track` / `previous_track` |
| Seek | `liberty.command.seek_absolute` / `seek_relative` (ms) |
| Source switch | `liberty.command.pull_source` |
| Now playing | `liberty.oobed.audiotile` |
| AUX input | `liberty.property.connect.analog.*` |
| Optical input | `liberty.property.connect.digital.*` |
| Bluetooth | `liberty.oobed.bluetooth.command.*` |
| Device info | `liberty.oobed.device_info` |
| Restart | `request_restart` |

Audio sources supported by the hardware: AirPlay 2, Spotify Connect, Roon, DLNA, Bluetooth, AUX, Optical, QPlay.

## License

MIT
