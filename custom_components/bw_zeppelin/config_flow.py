from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api_client import BwZeppelinApiClient, BwZeppelinApiError
from .const import CONF_FW_VERSION, CONF_HOST, CONF_LED_KEEPALIVE, CONF_MODEL, CONF_NODE_ID, CONF_SPACE_NAME, DEFAULT_LED_KEEPALIVE, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class BwZeppelinOptionsFlow(config_entries.OptionsFlow):

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_LED_KEEPALIVE, DEFAULT_LED_KEEPALIVE)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_LED_KEEPALIVE, default=current): vol.All(
                    int, vol.Range(min=0, max=30)
                ),
            }),
        )


class BwZeppelinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict | None = None

    @staticmethod
    def async_get_options_flow(config_entry):
        return BwZeppelinOptionsFlow()

    async def _validate_and_create(self, host: str) -> dict | None:
        session = async_get_clientsession(self.hass, verify_ssl=False)
        client = BwZeppelinApiClient(session=session, host=host)
        try:
            fw_version = await client.get_version()
            local_node_id = await client.get_local_node_id()
            nodes = await client.get_nodes()
        except BwZeppelinApiError:
            return None
        except Exception:  # noqa: BLE001 - report as cannot_connect, not "Unknown error"
            _LOGGER.exception("Unexpected error validating speaker at %s", host)
            return None
        if not nodes:
            return None
        # Formation speakers form a mesh and every member lists all the others, so
        # nodes[0] is whichever node happens to come first, not the one we asked.
        node = next((n for n in nodes if n.get("nodeID") == local_node_id), None)
        if node is None:
            if len(nodes) > 1:
                _LOGGER.warning(
                    "Node %s not found in the mesh list from %s; using the first entry",
                    local_node_id,
                    host,
                )
            node = nodes[0]
        return {
            CONF_HOST: host,
            CONF_NODE_ID: node["nodeID"],
            CONF_SPACE_NAME: node.get("space-name", "Zeppelin"),
            CONF_MODEL: node.get("type", "unknown"),
            CONF_FW_VERSION: fw_version,
        }

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            data = await self._validate_and_create(host)
            if data is None:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(data[CONF_NODE_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data[CONF_SPACE_NAME], data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        host = str(discovery_info.host)
        data = await self._validate_and_create(host)
        if data is None:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(data[CONF_NODE_ID])
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered = data
        self.context["title_placeholders"] = {"name": data[CONF_SPACE_NAME]}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered[CONF_SPACE_NAME],
                data=self._discovered,
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._discovered[CONF_SPACE_NAME]},
        )
