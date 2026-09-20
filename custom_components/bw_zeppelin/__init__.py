from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import BwZeppelinApiClient, BwZeppelinApiError
from .const import CONF_HOST, CONF_NODE_ID, CONF_SPACE_NAME, DEFAULT_AUDIO_OUTPUT_DELAY_US, DOMAIN, PROPERTY_GAIN_BASS, PROPERTY_GAIN_TREBLE
from .ws_client import BwZeppelinWebSocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SENSOR, Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass, verify_ssl=False)
    host = entry.data[CONF_HOST]
    api = BwZeppelinApiClient(
        session=session,
        host=host,
        node_id=entry.data[CONF_NODE_ID],
    )

    try:
        initial_light_state = await api.get_light_state()
    except BwZeppelinApiError as err:
        raise ConfigEntryNotReady(f"Cannot reach speaker at {host}") from err

    try:
        device_info = await api.get_device_info()
    except BwZeppelinApiError:
        _LOGGER.warning("Failed to fetch device info for diagnostics")
        device_info = {}

    initial_eq = {}
    for prop in (PROPERTY_GAIN_BASS, PROPERTY_GAIN_TREBLE):
        try:
            initial_eq[prop] = await api.get_eq(prop)
        except BwZeppelinApiError:
            _LOGGER.warning("Failed to fetch initial EQ value for %s", prop)

    initial_audio_output_delay = DEFAULT_AUDIO_OUTPUT_DELAY_US
    try:
        initial_audio_output_delay = await api.get_audio_output_delay()
    except BwZeppelinApiError:
        _LOGGER.warning("Failed to fetch initial audio output delay")

    ws_client = BwZeppelinWebSocket(host, entry.data[CONF_NODE_ID])
    ws_client.start(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "ws_client": ws_client,
        "initial_light_state": initial_light_state,
        "device_info": device_info,
        "initial_eq": initial_eq,
        "initial_audio_output_delay": initial_audio_output_delay,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        data["ws_client"].stop()
    return unload_ok
