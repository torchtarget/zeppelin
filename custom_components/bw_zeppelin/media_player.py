from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_client import BwZeppelinApiClient, BwZeppelinApiError
from .const import CMD_NEXT, CMD_PLAY_PAUSE, CMD_PREVIOUS, CONF_FW_VERSION, CONF_MODEL, CONF_NODE_ID, CONF_SPACE_NAME, DOMAIN
from .light import DEVICE_TYPE_NAMES
from .ws_client import BwZeppelinWebSocket

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BwZeppelinMediaPlayer(data["api"], data["ws_client"], entry)])


class BwZeppelinMediaPlayer(MediaPlayerEntity):

    _attr_has_entity_name = True
    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
    )

    def __init__(
        self,
        api: BwZeppelinApiClient,
        ws_client: BwZeppelinWebSocket,
        entry: ConfigEntry,
    ) -> None:
        self._api = api
        self._ws_client = ws_client
        self._unsub_ws_tile: Any = None
        self._unsub_ws_art: Any = None
        self._unsub_ws_vol: Any = None

        self._tile: dict = {}
        self._last_track_key: str = ""
        self._artwork_url: str | None = None
        self._artwork_bytes: bytes | None = None
        self._artwork_content_type: str = "image/jpeg"
        self._volume: int = 0
        self._muted: bool = False

        node_id = entry.data[CONF_NODE_ID]
        self._attr_unique_id = f"{node_id}_media_player"
        model_type = entry.data.get(CONF_MODEL, "unknown")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, node_id)},
            "name": entry.data.get(CONF_SPACE_NAME, "Zeppelin"),
            "manufacturer": "Bowers & Wilkins",
            "model": DEVICE_TYPE_NAMES.get(model_type, model_type),
            "sw_version": entry.data.get(CONF_FW_VERSION),
        }

    async def async_added_to_hass(self) -> None:
        self._unsub_ws_tile = self._ws_client.register_audiotile_callback(self._on_audiotile)
        self._unsub_ws_art = self._ws_client.register_artwork_callback(self._on_artwork)
        self._unsub_ws_vol = self._ws_client.register_volume_callback(self._on_volume)
        self.hass.async_create_task(self._request_initial_state())

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_ws_tile:
            self._unsub_ws_tile()
        if self._unsub_ws_art:
            self._unsub_ws_art()
        if self._unsub_ws_vol:
            self._unsub_ws_vol()

    async def _request_initial_state(self) -> None:
        # Both replies arrive on the WebSocket, so they are lost if it is not up yet.
        try:
            await asyncio.wait_for(self._ws_client.connected.wait(), timeout=20)
        except asyncio.TimeoutError:
            _LOGGER.debug("WebSocket still connecting; requesting initial state anyway")
        try:
            await self._api.request_volume()
            await self._api.request_audiotile()
        except BwZeppelinApiError:
            _LOGGER.debug("Failed to request initial state")

    def _track_key(self, tile: dict) -> str:
        return f"{tile.get('title', '')}|{tile.get('artist', '')}|{tile.get('album', '')}"

    @callback
    def _on_audiotile(self, tile: dict) -> None:
        if not tile:  # nothing playing on this speaker
            self._tile = {}
            self._last_track_key = ""
            self._artwork_bytes = None
            self._artwork_url = None
            self.async_write_ha_state()
            return
        track_key = self._track_key(tile)
        track_changed = track_key != self._last_track_key
        self._tile = tile
        self._last_track_key = track_key
        self._attr_media_position_updated_at = dt.datetime.now(dt.timezone.utc)

        if track_changed:
            self._artwork_bytes = None
            self._artwork_url = None
            self.hass.async_create_task(self._request_artwork())

        self.async_write_ha_state()

    @callback
    def _on_artwork(self, artwork: dict) -> None:
        uri = artwork.get("artworkURI", "")
        if uri:
            self._artwork_url = uri
            self.hass.async_create_task(self._fetch_artwork(uri))

    @callback
    def _on_volume(self, data: dict) -> None:
        if "source" in data and data["source"]:
            return
        self._volume = data.get("value", self._volume)
        self._muted = data.get("muted", self._muted)
        self.async_write_ha_state()

    async def _request_artwork(self) -> None:
        try:
            await self._api.request_artwork()
        except BwZeppelinApiError:
            _LOGGER.debug("Failed to request artwork")

    async def _fetch_artwork(self, url: str) -> None:
        try:
            self._artwork_bytes, self._artwork_content_type = await self._api.fetch_image(url)
            self.async_write_ha_state()
        except BwZeppelinApiError:
            _LOGGER.debug("Failed to fetch artwork from %s", url)

    @property
    def media_image_hash(self) -> str | None:
        if self._artwork_bytes:
            return hashlib.md5(self._artwork_bytes).hexdigest()
        return None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        if self._artwork_bytes:
            return self._artwork_bytes, self._artwork_content_type
        return None, None

    @property
    def state(self) -> MediaPlayerState:
        if not self._tile:
            return MediaPlayerState.IDLE
        if self._tile.get("state") == 1:
            return MediaPlayerState.PLAYING
        if self._tile.get("title"):
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    @property
    def media_title(self) -> str | None:
        return self._tile.get("title") or None

    @property
    def media_artist(self) -> str | None:
        return self._tile.get("artist") or None

    @property
    def media_album_name(self) -> str | None:
        return self._tile.get("album") or None

    @property
    def media_duration(self) -> float | None:
        duration = self._tile.get("duration")
        if duration is not None:
            return duration / 1000.0
        return None

    @property
    def media_position(self) -> float | None:
        elapsed = self._tile.get("elapsedTime")
        if elapsed is not None:
            return elapsed / 1000.0
        return None

    @property
    def volume_level(self) -> float:
        return self._volume / 100.0

    @property
    def is_volume_muted(self) -> bool:
        return self._muted

    @property
    def source(self) -> str | None:
        return self._tile.get("serviceName") or None

    async def async_set_volume_level(self, volume: float) -> None:
        value = int(volume * 100)
        try:
            await self._api.set_volume(value, self._muted)
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to set volume")

    async def async_mute_volume(self, mute: bool) -> None:
        try:
            await self._api.set_volume(self._volume, mute)
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to mute volume")

    async def async_media_play(self) -> None:
        try:
            await self._api.send_command(CMD_PLAY_PAUSE)
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to send play command")

    async def async_media_pause(self) -> None:
        try:
            await self._api.send_command(CMD_PLAY_PAUSE)
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to send pause command")

    async def async_media_next_track(self) -> None:
        try:
            await self._api.send_command(CMD_NEXT)
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to send next track command")

    async def async_media_previous_track(self) -> None:
        try:
            await self._api.send_command(CMD_PREVIOUS)
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to send previous track command")
