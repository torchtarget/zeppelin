from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Callable

import aiohttp

from homeassistant.core import HomeAssistant

from .const import DEFAULT_PORT, PROPERTY_AUDIOTILE, PROPERTY_AUDIOTILE_ARTWORK

_LOGGER = logging.getLogger(__name__)

RECONNECT_INTERVAL = 10
MAX_RECONNECT_INTERVAL = 120
CONNECT_TIMEOUT = aiohttp.ClientTimeout(total=10)


def select_own_tile(value: dict, node_id: str) -> dict | None:
    """Pick this speaker's audiotile out of a mesh-wide audiotile map.

    Keys are "<nodeID>+<service>", and a tile lists every node it is playing on in
    ``sinkNodeIDs``, so a speaker in a playback group matches on the sink list.
    """
    if not isinstance(value, dict):
        return None
    for key, tile in value.items():
        if not isinstance(tile, dict):
            continue
        if str(key).startswith(node_id) or node_id in (tile.get("sinkNodeIDs") or []):
            return tile
    return None


class BwZeppelinWebSocket:

    def __init__(self, host: str, node_id: str | None = None) -> None:
        self._host = host
        self._node_id = node_id
        self.connected = asyncio.Event()
        self._url = f"wss://{host}:{DEFAULT_PORT}/messages"
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._audiotile_callbacks: list[Callable[[dict], None]] = []
        self._artwork_callbacks: list[Callable[[dict], None]] = []
        self._volume_callbacks: list[Callable[[dict], None]] = []

    def register_audiotile_callback(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._audiotile_callbacks.append(callback)
        return lambda: self._audiotile_callbacks.remove(callback)

    def register_artwork_callback(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._artwork_callbacks.append(callback)
        return lambda: self._artwork_callbacks.remove(callback)

    def register_volume_callback(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._volume_callbacks.append(callback)
        return lambda: self._volume_callbacks.remove(callback)

    def start(self, hass: HomeAssistant) -> None:
        self._stop_event.clear()
        self._task = hass.async_create_background_task(self._run(), f"bw_zeppelin_ws_{self._host}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        backoff = RECONNECT_INTERVAL
        while not self._stop_event.is_set():
            try:
                await self._listen()
                backoff = RECONNECT_INTERVAL
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("WebSocket error, reconnecting in %ss", backoff, exc_info=True)

            if self._stop_event.is_set():
                return
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_RECONNECT_INTERVAL)

    async def _listen(self) -> None:
        session = aiohttp.ClientSession(timeout=CONNECT_TIMEOUT)
        try:
            ws = await session.ws_connect(
                self._url, ssl=self._ssl_context, heartbeat=30, autoclose=True
            )
            try:
                _LOGGER.debug("WebSocket connected to %s", self._host)
                self.connected.set()
                async for msg in ws:
                    if self._stop_event.is_set():
                        return
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                self.connected.clear()
                if not ws.closed:
                    await ws.close()
        finally:
            await session.close()

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        args = data.get("payload", {}).get("args", {})
        if not isinstance(args, dict):
            return
        method = args.get("message", {}).get("method", {})
        name = method.get("name")
        params = method.get("parameters", {})
        prop = params.get("property", "")
        # Every mesh member relays the other members' messages, so act only on ours.
        from_self = self._node_id is None or args.get("sendingNodeID") == self._node_id

        if prop == PROPERTY_AUDIOTILE and name in ("property_changed", "success"):
            value = params.get("value", {})
            if self._node_id is None:
                tile = next(iter(value.values()), None) if isinstance(value, dict) else None
            else:
                tile = select_own_tile(value, self._node_id)
                if tile is None and from_self and value == {}:
                    tile = {}  # our speaker reports nothing playing
            if tile is not None:
                for cb in self._audiotile_callbacks:
                    try:
                        cb(tile)
                    except Exception:
                        _LOGGER.exception("Error in audiotile callback")

        elif prop == PROPERTY_AUDIOTILE_ARTWORK and name in ("property_changed", "success"):
            value = params.get("value", {})
            artwork = None
            if isinstance(value, dict):
                own = [
                    v
                    for k, v in value.items()
                    if self._node_id and str(k).startswith(self._node_id)
                ]
                if own:
                    artwork = own[0]
                elif from_self:
                    artwork = next(iter(value.values()), None)
            if artwork:
                for cb in self._artwork_callbacks:
                    try:
                        cb(artwork)
                    except Exception:
                        _LOGGER.exception("Error in artwork callback")

        elif (
            name in ("volume_changed", "success")
            and "value" in params
            and "muted" in params
            and from_self
        ):
            for cb in self._volume_callbacks:
                try:
                    cb(params)
                except Exception:
                    _LOGGER.exception("Error in volume callback")
