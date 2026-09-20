# Bowers & Wilkins — Home Assistant Integration

Local control of B&W Formation and Zeppelin speakers. No cloud, no account, no polling.

> **This is a fork** of [space192/zeppelin](https://github.com/space192/zeppelin), which is
> developed against a Zeppelin Pro. This fork adds **Formation** support and is developed
> against a live mesh of three speakers: two Formation Flex and one Formation Wedge, on
> Home Assistant 2026.8, playing from Spotify Connect and Roon.
>
> The fixes are offered back upstream in
> [`fix/formation-mesh-support`](https://github.com/torchtarget/zeppelin/tree/fix/formation-mesh-support).

## What this fork fixes

Four bugs kept the integration from working with more than one Formation speaker.

**Adding a speaker failed with "Unknown error occurred".**
`get_version()` was lost in a merge somewhere in 0.5.x, its body left dangling at the end of
another method. The config flow calls it first, so setup died on an `AttributeError`, which
Home Assistant reports with that unhelpful message. Version 0.4.3 was the last one that
worked. The method is restored, and the config flow now turns any unexpected exception into
a plain "cannot connect" so the next slip of this kind says something useful.

**Speakers got each other's names, then collided.**
Formation speakers pair into a mesh, and asking one for the node list returns *every*
member. The config flow took the first entry, which is arbitrary. With three speakers, each
one came back named after a different room, and two of them claimed the same unique id and
were rejected as duplicates. The flow now asks the speaker which node it is and picks that
entry, falling back to the old behaviour when there is only one.

**Every speaker showed the same track.**
Each member of the mesh relays the other members' messages over its own WebSocket. The
client ignored the sender field and applied the first now-playing record it saw, so once any
speaker played something, all of them claimed to be playing it. Messages are now filtered by
the speaker's own node id, and a record is still accepted when the speaker appears in the
stream's sink list, so grouped playback keeps working. A speaker that reports nothing
playing now returns to idle instead of holding the last track forever.

**Volume always read zero.**
The initial volume was requested the moment the entity was added, but the reply comes back
over the WebSocket, which is usually still connecting. The answer was dropped and the
speaker showed volume 0.0 until someone touched it. Setup now waits for the connection, then
asks for the volume and the current track, so a speaker that is already playing shows it
immediately.

### Also in this fork, not sent upstream

- **Corrected model names.** A Formation Flex was labelled "Formation Solo", a product that
  does not exist, and a Wedge was labelled "Formation Duo". Both are fixed from the model
  numbers and AirPlay records of real hardware.
- **Version `0.5.3-formation.1`**, which sorts above upstream 0.5.2 and below a future
  upstream 0.5.3. HACS offers this build now and steps aside when upstream ships its own.

### Inert on Formation

Two entities are created but do nothing on Formation hardware. They are left in place
because removing entities breaks existing installs; they are candidates for a major version.

- **The LED light.** `liberty.lights.hardware-downlight` returns `null` on all three
  Formation models tested. The downlight is a Zeppelin feature.
- **Audio Output Delay.** It writes through the StreamSDK API on port 80, which Formation
  speakers do not open. Only 7000 and 42425 are listening.

## Supported devices

| Device | Type id | Status |
|---|---|---|
| Formation Flex | `com.bowerswilkins.liberty.lcms` | tested |
| Formation Wedge | `com.bowerswilkins.liberty.ps1` | tested |
| Zeppelin Pro | `com.bowerswilkins.liberty.zpr` | tested upstream |
| Zeppelin | `com.bowerswilkins.liberty.zep` | untested |
| Panorama 3 | `com.bowerswilkins.liberty.alb` | untested |
| Formation Duo | `com.bowerswilkins.liberty.st1` | unconfirmed |
| Formation Bar | `com.bowerswilkins.liberty.sb1` | unconfirmed |
| Formation Bass | `com.bowerswilkins.liberty.sw1` | unconfirmed |
| Formation Audio | `com.bowerswilkins.liberty.connect` | unconfirmed |

The ids marked unconfirmed came from upstream and no longer match the two models that were
checked against real hardware, so treat them with suspicion. If yours is mislabelled, the
`type` field from `GET /1/mesh/nodes` and the model on the speaker's label are enough to fix
it.

## Entities

Per speaker:

| Entity | Notes |
|---|---|
| `media_player` | State, title, artist, album, artwork, volume, mute, play, pause, next, previous |
| `light` | LED colour and brightness. Zeppelin only |
| `number` | Bass and treble |
| `number` | Audio output delay. Zeppelin only |
| `update` | Firmware, checked nightly between 3 and 6 AM |

### Audio output delay, for Zeppelin owners

If a Zeppelin runs ahead of other speakers in a mixed AirPlay 2 group, it is over-reporting
its output latency and the sender is shipping audio too early. A **negative** value lowers
the reported latency, the sender delays the audio, and it lands back in sync.

- The entity is in milliseconds; the speaker stores microseconds.
- The setting survives a full power cycle. Zero restores factory behaviour.
- It applies at stream start, so restart playback after changing it.
- It only corrects a consistent offset, never random Wi-Fi drift.
- Tune by ear. Many people land near −150 ms, but it depends on the speaker mix.

## Installation

Add this repository to HACS as a custom repository, category Integration, install it, and
restart Home Assistant. To install by hand instead, copy `custom_components/bw_zeppelin`
into your `custom_components` directory and restart.

Then go to **Settings → Devices & Services → Add Integration**, search for Bowers &
Wilkins, and enter the speaker's IP address. The name and node id are discovered for you.
Repeat for each speaker. Give them static leases so the addresses do not move.

## How it works

Everything runs over the speaker's local API on port 42425, HTTPS with a self-signed
certificate. A request looks like this:

```
POST /mesh/node/{nodeId}/channel/com.bowerswilkins.stated.service+provider/message
```

```json
{
  "type": "query",
  "method": {
    "name": "get_property",
    "parameters": { "property": "liberty.oobed.audiotile" }
  }
}
```

The POST returns `202` with an empty body. The answer arrives separately on a WebSocket at
`wss://<host>:42425/messages`, which the integration holds open, so there is no polling.

[**docs/FORMATION.md**](docs/FORMATION.md) documents the protocol as measured on real
hardware: what the REST endpoints return, how the mesh relays messages between speakers,
every property the speakers hold, the shape of the now-playing record, and what could be
built next.

## Limitations

- The self-signed certificate means TLS verification is off for local traffic.
- The B&W app sometimes fails to find speakers on Ethernet. This integration does not care.
- Source selection, seek, shuffle and repeat all exist in the API but are not exposed yet.

## License

MIT
