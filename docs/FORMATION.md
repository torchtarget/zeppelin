# Formation protocol notes

Everything here was measured against real hardware on 2026-09-20, not inferred from the
app. Read-only calls only: no `set_*`, no `send_command`, nothing that could interrupt
playback or reboot a speaker.

**Test bench**

| Room | Model | Type id | `ModelNumber` | FP | Firmware |
|---|---|---|---|---|---|
| Frida's Room | Formation Flex | `com.bowerswilkins.liberty.lcms` | `L-CMS MP` | FP41343 | 3H006 |
| Natalia's Room | Formation Flex | `com.bowerswilkins.liberty.lcms` | `L-CMS MP` | FP41343 | 3G025 |
| Bar | Formation Wedge | `com.bowerswilkins.liberty.ps1` | `L-PS MP` | FP40664 | 3H006 |

All three are members of one mesh (`mesh.0.4`), each `role: primary`.

## Transport

Two ways in, both on port **42425** over HTTPS with a self-signed certificate
(`O=Bowers & Wilkins Engineering, CN=formatFormation 1.3`).

**REST**, three working paths. Everything else returns 404.

| Path | Returns |
|---|---|
| `GET /mesh/node` | `{"node_id": "..."}` — **the node at this address**, which is the only way to tell which speaker answered |
| `GET /1/mesh/nodes` | every member of the mesh: `nodeID`, `space-name`, `type`, `role`, `version` |
| `GET /software/version` | `{"version": "3H006"}` |

**StateD**, a fire-and-forget RPC.

```
POST /mesh/node/{nodeId}/channel/com.bowerswilkins.stated.service+provider/message
{"type":"query","method":{"name":"get_property","parameters":{"property":"..."}}}
```

The POST returns **202 with an empty body**. The answer arrives asynchronously on the
WebSocket at `wss://<host>:42425/messages`, so a client that does not hold the socket open
never sees replies. This is the single most important thing about the protocol.

An unknown *channel* also returns 202, so channel names cannot be probed by status code.
An unknown *method* is silently dropped, and an unknown *property* replies `success` with
`value: null`, which makes property enumeration easy and method enumeration possible.

### Reply envelope

```jsonc
{"type":"mesh","payload":{"args":{
  "sendingNodeID":"57deec65-…",        // which speaker this is about
  "channel":"com.bowerswilkins.stated.service+subscriber",
  "message":{"type":"reply","method":{
     "name":"success",                  // or "fail", or "property_changed"
     "parameters":{"property":"…","value":…},
     "reply-to":"get_property"}}}}}
```

**Every member relays every other member's messages.** Open one socket and you receive the
whole mesh. `sendingNodeID` is the only way to tell them apart, and ignoring it is what made
all three players show the same track.

## Methods that exist

Confirmed by reply. Only read-only names were sent; the `set_*` and command names come from
upstream's notes and the `enabledPlaybackActions` field, and are listed here unverified.

| Method | Parameters | Notes |
|---|---|---|
| `get_property` | `property` | Unknown property replies `value: null`. Replies `fail` with no parameters |
| `get_all_properties` | none | **Returns all five `liberty.property.*` values in one call.** Not used upstream |
| `get_volume` | `source` (`""` for the speaker) | Replies `{value: 73.699…, muted: false, source: ""}`. Note the float |
| `get_device_info` | none | Short form: `ble_mac_address`, `mn`, `sn`. Distinct from the property of the same name |
| `set_property`, `set_volume`, `send_command`, `check_software_update`, `start_software_update` | — | Used by the integration, not re-probed |
| `request_restart` | — | Listed upstream. **Not probed** |

## Properties

`get_all_properties` returns exactly these five, identical on all three speakers:

| Property | Value | Exposed? |
|---|---|---|
| `liberty.property.gain.bass` | `0` | yes, number |
| `liberty.property.gain.treble` | `0` | yes, number |
| `liberty.property.gain.offset` | `0` | **no** — a third gain trim |
| `liberty.property.connect.analog.autoswitch` | `true` | **no** — auto-switch to the analog input |
| `liberty.property.connect.digital.autoswitch` | `true` | **no** — auto-switch to the optical input |

Outside that set:

| Property | Value |
|---|---|
| `liberty.oobed.device_info` | `BTMacAddress`, `Capabilities`, `FPNumber`, `ModelNumber`, `SerialNumber` |
| `liberty.oobed.audiotile` | now playing, see below. `{}` when idle |
| `liberty.oobed.audiotile.artwork` | artwork URL, fetched separately |
| `liberty.lights.hardware-downlight` | **`null` on all three Formation models.** Zeppelin-only |

### Capabilities, and a firmware gotcha

```
audio-audiogum, audio-repeat-shuffle, audio-audiogum-send-playback-action,
audio-audiogum-aes-encryption, audio-audiogum-enhanced
```

Natalia's Flex reports only the first three. It is on firmware **3G025** while the other two
are on **3H006**, so the capability list tracks firmware, not model. Anything built on these
flags has to read them per speaker rather than assume them per model.

### The audiotile

Captured from the Wedge with a paused Spotify Connect session:

```jsonc
{"57deec65-…+audiod_plugin_spotify": {
  "title":"The Duck Song 2", "artist":"Bryant Oden",
  "album":"The Songdrops Collection, Vol. 1",
  "trackURI":"spotify:track:2sKau00a99lEh04qmo2Gug",
  "albumURI":"spotify:album:…", "artistURI":"spotify:artist:…",
  "duration":154440, "elapsedTime":88000,          // milliseconds
  "state":2,                                        // 1 playing, 2 paused
  "serviceName":"Spotify", "serviceID":"audiod_plugin_spotify",
  "shuffle":false, "repeat":false, "repeatMode":"none",
  "sinkNodeIDs":["57deec65-…"],                     // every speaker playing this stream
  "enabledPlaybackActions":["play","seek_relative_forward",
       "seek_relative_backward","previous_track","next_track"],
  "audio_format":{"codec":"Vorbis","src_bit_rate":320000,
       "src_sample_rate":44100,"src_sample_size":24}}}
```

Keys are `<nodeID>+<service>`. `sinkNodeIDs` is how grouped playback appears: one tile,
several sinks. The metadata is populated for **Spotify Connect** without any Spotify
integration in Home Assistant, which is the point — the speaker reports what it is playing
whatever the source.

## Worth adding

Ordered by value for effort. Nothing here is implemented yet.

1. **Seek, shuffle and repeat.** The tile carries `shuffle`, `repeat`, `repeatMode`, and
   `enabledPlaybackActions` advertises `seek_relative_forward` / `seek_relative_backward`;
   upstream's notes also name `liberty.command.seek_absolute`. All three map onto standard
   media player features, and `audio-repeat-shuffle` is in every speaker's capability list.
2. **Gate features on what the stream allows.** `enabledPlaybackActions` changes per source,
   so buttons that cannot work could be hidden instead of failing silently.
3. **Report group membership.** Map `sinkNodeIDs` to the entity ids of the other configured
   speakers and publish `group_members`. Purely read-only, and it makes a grouped mesh legible.
4. **`media_content_id` from `trackURI`.** A `spotify:track:` id lets other integrations and
   dashboard cards link straight to the track.
5. **One `get_all_properties` call** at startup instead of a `get_property` per EQ value.
6. **Expose `gain.offset`**, and the two `connect.*.autoswitch` flags as diagnostic switches.
7. **Per-speaker firmware sensor.** The mesh list carries every member's `version`, so a
   speaker left behind on older firmware, as Natalia's is, would be visible.
8. **Drop the LED light on Formation** when `liberty.lights.hardware-downlight` is `null`,
   and the Audio Output Delay number when port 80 is closed. Both are breaking changes, so
   they belong in a major version.

## Ports

`nmap` of a Formation Flex and the Wedge: **7000** (AirPlay) and **42425** (StateD) only.
Port 80, which the Zeppelin uses for the StreamSDK `audioOutputDelay` setting, is **closed**.
mDNS advertises `_airplay._tcp`, `_raop._tcp`, `_spotify-connect._tcp` and `_eva-mesh._tcp`;
the last one carries the mesh id and `space-name` in its TXT record and is what the
integration's zeroconf discovery matches on.
