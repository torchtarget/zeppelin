from __future__ import annotations

import json
import logging
import ssl
import uuid

import aiohttp

from .const import (
    DEFAULT_PORT,
    PROPERTY_AUDIOTILE,
    PROPERTY_AUDIOTILE_ARTWORK,
    PROPERTY_DEVICE_INFO,
    PROPERTY_LIGHT_STATE,
    SETTING_AUDIO_OUTPUT_DELAY,
    STREAMSDK_PORT,
    STATED_CHANNEL,
)

_LOGGER = logging.getLogger(__name__)


class BwZeppelinApiError(Exception):
    pass


REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class BwZeppelinApiClient:

    def __init__(self, session: aiohttp.ClientSession, host: str, node_id: str | None = None) -> None:
        self._session = session
        self._host = host
        self._node_id = node_id
        self._base_url = f"https://{host}:{DEFAULT_PORT}"
        # The StreamSDK HTTP API (getData/setData) lives on plain HTTP port 80,
        # separate from the StateD API on https port 42425. It exposes the
        # undocumented audioOutputDelay setting used to fix AirPlay 2 sync.
        self._streamsdk_url = f"http://{host}:{STREAMSDK_PORT}"
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

    @property
    def node_id(self) -> str | None:
        return self._node_id

    @node_id.setter
    def node_id(self, value: str) -> None:
        self._node_id = value

    async def _get(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(url, ssl=self._ssl_context, timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as err:
            raise BwZeppelinApiError(f"Request to {path} failed: {err}") from err

    async def _post_stated(self, method: str, parameters: dict) -> dict:
        if self._node_id is None:
            raise BwZeppelinApiError("Node ID not set")
        url = (
            f"{self._base_url}/mesh/node/{self._node_id}"
            f"/channel/{STATED_CHANNEL}/message"
        )
        payload = {
            "type": "query",
            "method": {
                "name": method,
                "parameters": parameters,
            },
        }
        headers = {"X-Request-Id": str(uuid.uuid4())}
        try:
            async with self._session.post(
                url, json=payload, headers=headers, ssl=self._ssl_context, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                if not text:
                    return {}
                data = await resp.json(content_type=None)
                if "error" in data:
                    raise BwZeppelinApiError(data["error"].get("message", "Unknown error"))
                return data
        except aiohttp.ClientError as err:
            raise BwZeppelinApiError(f"StateD request '{method}' failed: {err}") from err

    async def _streamsdk_get(self, path: str, roles: str = "value") -> dict:
        """GET on the port-80 StreamSDK API: /api/getData?path=...&roles=..."""
        url = f"{self._streamsdk_url}/api/getData"
        params = {"path": path, "roles": roles}
        try:
            async with self._session.get(url, params=params, timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise BwZeppelinApiError(f"StreamSDK getData '{path}' failed: {err}") from err

    @staticmethod
    def _streamsdk_check_error(data: object, path: str) -> None:
        """Raise if the StreamSDK API returned an error envelope."""
        if isinstance(data, dict) and "error" in data:
            raise BwZeppelinApiError(
                f"StreamSDK '{path}' error: {data['error'].get('message', 'unknown error')}"
            )

    async def _streamsdk_set(self, path: str, role: str, value: dict) -> dict:
        """Write on the port-80 StreamSDK API: /api/setData?path=...&role=...&value=...

        The speaker accepts this as a GET with URL-encoded query params (matching
        `curl -G ... --data-urlencode`); we use GET for parity with the known-good
        invocation.
        """
        url = f"{self._streamsdk_url}/api/setData"
        params = {"path": path, "role": role, "value": json.dumps(value)}
        try:
            async with self._session.get(url, params=params, timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                text = await resp.text()
                if not text:
                    return {}
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise BwZeppelinApiError(f"StreamSDK setData '{path}' failed: {err}") from err

    async def get_audio_output_delay(self) -> int:
        """Read audioOutputDelay in microseconds (may be negative)."""
        data = await self._streamsdk_get(SETTING_AUDIO_OUTPUT_DELAY, roles="value")
        self._streamsdk_check_error(data, SETTING_AUDIO_OUTPUT_DELAY)
        # Observed response shape with roles=value: a list wrapping the typed value:
        #   [{"type": "i64_", "i64_": 0}]
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "i64_" in item:
                    return int(item["i64_"])
            return 0
        if isinstance(data, dict):
            value = data.get("value", {})
            if isinstance(value, dict):
                return int(value.get("i64_", 0))
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    async def set_audio_output_delay(self, microseconds: int) -> None:
        """Write audioOutputDelay in microseconds (negative pulls the speaker back into sync)."""
        data = await self._streamsdk_set(
            SETTING_AUDIO_OUTPUT_DELAY,
            role="value",
            value={"type": "i64_", "i64_": int(microseconds)},
        )
        self._streamsdk_check_error(data, SETTING_AUDIO_OUTPUT_DELAY)

    async def get_version(self) -> str:
        data = await self._get("/software/version")
        return data.get("version", "unknown")

    async def get_local_node_id(self) -> str | None:
        """Node id of the speaker at this host.

        `get_nodes()` lists every member of the mesh, so it cannot identify which
        one answered; this endpoint names the speaker we are actually talking to.
        """
        data = await self._get("/mesh/node")
        return data.get("node_id")

    async def get_nodes(self) -> list[dict]:
        data = await self._get("/1/mesh/nodes")
        return data.get("nodes", [])

    async def get_light_state(self) -> dict:
        data = await self._post_stated("get_property", {"property": PROPERTY_LIGHT_STATE})
        return data.get("value", {})

    async def request_artwork(self) -> None:
        await self._post_stated("get_property", {"property": PROPERTY_AUDIOTILE_ARTWORK})

    async def request_audiotile(self) -> None:
        await self._post_stated("get_property", {"property": PROPERTY_AUDIOTILE})

    async def get_device_info(self) -> dict:
        data = await self._post_stated("get_property", {"property": PROPERTY_DEVICE_INFO})
        return data.get("value", {})

    async def check_software_update(self) -> dict:
        return await self._post_stated("check_software_update", {})

    async def fetch_image(self, url: str) -> tuple[bytes, str]:
        try:
            async with self._session.get(url, ssl=self._ssl_context, timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                content_type = resp.content_type or "image/jpeg"
                return await resp.read(), content_type
        except aiohttp.ClientError as err:
            raise BwZeppelinApiError(f"Failed to fetch image: {err}") from err

    async def get_eq(self, property_name: str) -> int:
        data = await self._post_stated("get_property", {"property": property_name})
        return data.get("value", 0)

    async def set_eq(self, property_name: str, value: int) -> None:
        await self._post_stated(
            "set_property",
            {"property": property_name, "value": value},
        )

    async def request_volume(self) -> None:
        await self._post_stated("get_volume", {"source": ""})

    async def set_volume(self, value: int, muted: bool) -> None:
        await self._post_stated(
            "set_volume",
            {"value": max(0, min(value, 100)), "source": "", "muted": muted},
        )

    async def start_software_update(self) -> None:
        await self._post_stated("start_software_update", {})

    async def send_command(self, command: str) -> None:
        await self._post_stated(
            "send_command",
            {"command": command},
        )

    async def set_light_state(
        self,
        enabled: bool,
        brightness: float,
        rgb: tuple[int, int, int],
    ) -> None:
        await self._post_stated(
            "set_property",
            {
                "property": PROPERTY_LIGHT_STATE,
                "value": {
                    "enabled": enabled,
                    "brightness": brightness,
                    "rgb": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]},
                    "supported": True,
                },
            },
        )
