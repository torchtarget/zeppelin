# The Formation protocol, as measured

Notes taken from real speakers on 20 September 2026, not inferred from the app. Only
read-only calls were made: nothing that writes a setting, sends a transport command, or
restarts a speaker.

The bench was three speakers in one mesh, protocol `mesh.0.4`, each holding the role
`primary`:

| Room | Model | Type id | Model number | Firmware |
|---|---|---|---|---|
| Frida's Room | Formation Flex | `…liberty.lcms` | `L-CMS MP` | 3H006 |
| Natalia's Room | Formation Flex | `…liberty.lcms` | `L-CMS MP` | 3G025 |
| Bar | Formation Wedge | `…liberty.ps1` | `L-PS MP` | 3H006 |

## Getting in

One port matters: **42425**, HTTPS, behind a self-signed certificate issued to
`O=Bowers & Wilkins Engineering`. There are two ways to talk to it.

### REST, for the three things you need up front

```
GET /mesh/node          →  {"node_id": "9382c902-…"}
GET /1/mesh/nodes       →  {"nodes": [ … every member of the mesh … ]}
GET /software/version   →  {"version": "3H006"}
```

Every other path returns 404.

The first one matters more than it looks. `/1/mesh/nodes` returns the whole mesh, in no
useful order, so it cannot tell you which speaker answered. `/mesh/node` is the only way to
identify the speaker at the address you dialled. Getting this wrong is what made the config
flow hand out the wrong names.

### StateD, for everything else

```
POST /mesh/node/{nodeId}/channel/com.bowerswilkins.stated.service+provider/message
```

```json
{"type": "query", "method": {"name": "get_property", "parameters": {"property": "…"}}}
```

The POST answers `202` with an empty body, always. The real reply arrives on a WebSocket at
`wss://<host>:42425/messages`. **A client that does not hold that socket open will never see
a single answer**, which is the one thing to know before writing any code against this API.

This also explains a bug worth avoiding: if you ask for something during startup before the
socket is connected, the reply is simply lost, and your entity sits on a default value until
something else changes.

### How the API answers questions it does not understand

Useful when mapping the surface:

- An **unknown property** replies `success` with `value: null`. So a property either exists
  and has a value, or it does not exist. Enumeration is easy.
- An **unknown method** is dropped silently. Send a candidate, wait, and a reply means the
  method is real.
- An **unknown channel** still answers `202`. Channel names cannot be probed this way.

## The mesh relays everything

A reply looks like this, trimmed:

```jsonc
{"type": "mesh", "payload": {"args": {
  "sendingNodeID": "57deec65-…",
  "channel": "com.bowerswilkins.stated.service+subscriber",
  "message": {"type": "reply", "method": {
      "name": "success",
      "parameters": {"property": "liberty.oobed.audiotile", "value": { … }},
      "reply-to": "get_property"}}}}}
```

Open a socket to one speaker and you receive traffic about **all of them**. Ask Frida's
speaker a question and the Wedge's answers arrive on the same socket. `sendingNodeID` is the
only thing separating them.

Ignoring that field is what made all three players show the same track: whichever speaker
spoke last won. Anything built on this protocol has to filter by node id first and interpret
second.

## What the speakers actually hold

`get_all_properties` returns the complete set in one call, and it is short. All three
speakers answered identically:

```json
{
  "liberty.property.gain.bass": 0,
  "liberty.property.gain.treble": 0,
  "liberty.property.gain.offset": 0,
  "liberty.property.connect.analog.autoswitch": true,
  "liberty.property.connect.digital.autoswitch": true
}
```

Only bass and treble are exposed as entities today. The gain offset is a third trim, and the
two auto-switch flags control whether the speaker jumps to its analog or optical input when
something starts playing there.

Three more properties live outside that set and have to be asked for by name:

- `liberty.oobed.device_info` — Bluetooth MAC, capability list, FP number, model number,
  serial.
- `liberty.oobed.audiotile` — what is playing. `{}` when idle.
- `liberty.oobed.audiotile.artwork` — an artwork URL, fetched separately.

And one that is worth knowing about because of what it does *not* do:

- `liberty.lights.hardware-downlight` returns **`null` on every Formation model tested**. The
  downlight belongs to the Zeppelin. The light entity this integration creates cannot work
  on Formation hardware.

### Methods

Confirmed by reply, sending read-only names only:

- `get_property`, one property at a time.
- `get_all_properties`, the whole set at once. The integration does not use it.
- `get_volume`, which answers `{"value": 73.699…, "muted": false, "source": ""}`. Note that
  the volume is a float, not an integer.
- `get_device_info`, a shorter form of the property, giving MAC, model number and serial.

`set_property`, `set_volume`, `send_command`, `check_software_update` and
`start_software_update` are all in use by the integration and were not re-probed.
`request_restart` appears in upstream's notes and was deliberately left alone.

### Capabilities drift with firmware, not model

Two of the speakers report five capabilities:

```
audio-audiogum, audio-repeat-shuffle, audio-audiogum-send-playback-action,
audio-audiogum-aes-encryption, audio-audiogum-enhanced
```

Natalia's Flex reports only the first three. It is the one still on firmware 3G025 while the
others are on 3H006, and it is otherwise identical hardware. So the capability list tracks
firmware. Anything that depends on these flags has to read them per speaker and not assume
them per model.

## The now-playing record

Captured from the Wedge, paused on a Spotify Connect session:

```jsonc
{"57deec65-…+audiod_plugin_spotify": {
  "title": "The Duck Song 2",
  "artist": "Bryant Oden",
  "album": "The Songdrops Collection, Vol. 1",
  "trackURI": "spotify:track:2sKau00a99lEh04qmo2Gug",
  "albumURI": "spotify:album:…",
  "artistURI": "spotify:artist:…",
  "duration": 154440,          // milliseconds
  "elapsedTime": 88000,
  "state": 2,                  // 1 playing, 2 paused
  "serviceName": "Spotify",
  "serviceID": "audiod_plugin_spotify",
  "shuffle": false,
  "repeat": false,
  "repeatMode": "none",
  "sinkNodeIDs": ["57deec65-…"],
  "enabledPlaybackActions": [
    "play", "seek_relative_forward", "seek_relative_backward",
    "previous_track", "next_track"
  ],
  "audio_format": {
    "codec": "Vorbis", "src_bit_rate": 320000,
    "src_sample_rate": 44100, "src_sample_size": 24
  }}}
```

Three things are worth pulling out.

**The key is `<nodeID>+<service>`**, which is how you tell whose record this is without
reading the envelope.

**`sinkNodeIDs` lists every speaker playing this stream.** That is how grouped playback
appears: one record, several sinks. A speaker in a group should follow the record even
though the key names another node.

**The metadata is populated for Spotify Connect** with no Spotify integration involved. The
speaker reports what it is playing regardless of how the music got there, which makes this
API a reliable answer to "is there music in this room".

## What could be built next

Roughly in order of value for effort. None of it is implemented.

1. **Seek, shuffle and repeat.** The record carries `shuffle`, `repeat` and `repeatMode`,
   `enabledPlaybackActions` advertises relative seeking, and upstream's notes name
   `liberty.command.seek_absolute`. Every speaker reports `audio-repeat-shuffle`. All three
   map onto standard media player features.
2. **Gate the controls on what the stream allows.** `enabledPlaybackActions` changes with the
   source, so buttons that cannot work could be hidden rather than failing quietly.
3. **Publish group membership.** Map `sinkNodeIDs` onto the other configured speakers and
   report them as group members. Read-only, and it makes a grouped mesh legible in Home
   Assistant.
4. **Expose `trackURI` as the media content id**, so cards and other integrations can link
   straight to the track.
5. **Fetch the settings in one call** with `get_all_properties` rather than one request per
   EQ value.
6. **Expose the gain offset**, and the two auto-switch flags as diagnostic controls.
7. **A firmware sensor per speaker.** The mesh list already carries every member's version,
   so a speaker left behind, as one of ours was, would be visible without opening the app.
8. **Stop creating entities that cannot work.** Skip the light when the downlight property is
   `null`, and the audio output delay when port 80 is closed. Both are breaking changes and
   belong in a major version.

## Ports and discovery

A port scan of a Flex and of the Wedge found exactly two open ports: **7000** for AirPlay and
**42425** for this API. Port 80, which the Zeppelin uses for the StreamSDK settings API, is
closed on Formation, which is why the audio output delay entity cannot work there.

Over mDNS the speakers advertise `_airplay._tcp`, `_raop._tcp`, `_spotify-connect._tcp` and
`_eva-mesh._tcp`. The last one is the interesting one: its TXT record carries the mesh id,
the node id and the room name, and it is what this integration's zeroconf discovery matches.
