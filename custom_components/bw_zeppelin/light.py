from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .api_client import BwZeppelinApiClient, BwZeppelinApiError
from .const import CONF_FW_VERSION, CONF_LED_KEEPALIVE, CONF_MODEL, CONF_NODE_ID, CONF_SPACE_NAME, DEFAULT_LED_KEEPALIVE, DOMAIN

import datetime as dt

_LOGGER = logging.getLogger(__name__)

DEVICE_TYPE_NAMES = {
    "com.bowerswilkins.liberty.zpr": "Zeppelin Pro",
    "com.bowerswilkins.liberty.zep": "Zeppelin",
    "com.bowerswilkins.liberty.alb": "Panorama 3",
    "com.bowerswilkins.liberty.ps1": "Formation Wedge",
    "com.bowerswilkins.liberty.st1": "Formation Duo",
    "com.bowerswilkins.liberty.sb1": "Formation Bar",
    "com.bowerswilkins.liberty.sw1": "Formation Bass",
    "com.bowerswilkins.liberty.connect": "Formation Audio",
    "com.bowerswilkins.liberty.lcms": "Formation Flex",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BwZeppelinLight(data["api"], data["initial_light_state"], entry)])


class BwZeppelinLight(LightEntity):

    _attr_has_entity_name = True
    _attr_name = "LED"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, api: BwZeppelinApiClient, initial_state: dict, entry: ConfigEntry) -> None:
        self._api = api
        self._entry = entry
        self._is_on = initial_state.get("enabled", False)
        self._brightness_float = initial_state.get("brightness", 0.5)
        rgb = initial_state.get("rgb", {})
        self._rgb = (rgb.get("red", 255), rgb.get("green", 255), rgb.get("blue", 255))
        self._unsub_keepalive: CALLBACK_TYPE | None = None

        node_id = entry.data[CONF_NODE_ID]
        self._attr_unique_id = f"{node_id}_led"
        model_type = entry.data.get(CONF_MODEL, "unknown")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, node_id)},
            "name": entry.data.get(CONF_SPACE_NAME, "Zeppelin"),
            "manufacturer": "Bowers & Wilkins",
            "model": DEVICE_TYPE_NAMES.get(model_type, model_type),
            "sw_version": entry.data.get(CONF_FW_VERSION),
        }

    def _keepalive_minutes(self) -> int:
        return self._entry.options.get(CONF_LED_KEEPALIVE, DEFAULT_LED_KEEPALIVE)

    async def async_added_to_hass(self) -> None:
        self._entry.async_on_unload(
            self._entry.add_update_listener(self._options_updated)
        )
        self._start_keepalive()

    async def async_will_remove_from_hass(self) -> None:
        self._stop_keepalive()

    @staticmethod
    async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    def _start_keepalive(self) -> None:
        self._stop_keepalive()
        minutes = self._keepalive_minutes()
        if minutes <= 0 or not self._is_on:
            return
        self._unsub_keepalive = async_track_time_interval(
            self.hass, self._keepalive_tick, dt.timedelta(minutes=minutes)
        )

    def _stop_keepalive(self) -> None:
        if self._unsub_keepalive:
            self._unsub_keepalive()
            self._unsub_keepalive = None

    async def _keepalive_tick(self, _now) -> None:
        if not self._is_on:
            self._stop_keepalive()
            return
        try:
            await self._api.set_light_state(
                enabled=True, brightness=self._brightness_float, rgb=self._rgb
            )
        except BwZeppelinApiError:
            _LOGGER.debug("LED keep-alive failed")

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int | None:
        return max(1, min(255, math.ceil(self._brightness_float * 255)))

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return self._rgb

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness_float = kwargs[ATTR_BRIGHTNESS] / 255.0
        if ATTR_RGB_COLOR in kwargs:
            self._rgb = kwargs[ATTR_RGB_COLOR]

        try:
            await self._api.set_light_state(
                enabled=True, brightness=self._brightness_float, rgb=self._rgb
            )
            self._is_on = True
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to turn on LED")
            return
        self._start_keepalive()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._api.set_light_state(
                enabled=False, brightness=self._brightness_float, rgb=self._rgb
            )
            self._is_on = False
        except BwZeppelinApiError:
            _LOGGER.exception("Failed to turn off LED")
            return
        self._stop_keepalive()
        self.async_write_ha_state()
