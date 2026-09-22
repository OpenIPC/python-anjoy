"""Asynchronous client for stock Anjoy cameras — an asyncio mirror of the sync
:class:`anjoy.client.AnjoyClient`.

Shares the wire contract with the sync path: the SOAP envelope, DES auth
(:mod:`anjoy.des`), response unwrapping (:func:`anjoy.soap.extract`) and error
check (:func:`anjoy.soap.check_response`) are imported, not reimplemented, so the
two surfaces cannot drift. Only the HTTP transport differs (a minimal HTTP/1.1
POST over ``asyncio`` streams — no third-party HTTP dependency).
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import const
from .client import _xml
from .des import des_hex
from .exceptions import AnjoyError
from .soap import check_response, extract, _ENVELOPE


class AsyncAnjoyClient:
    """Async manager for a stock Anjoy camera over its web SOAP API."""

    def __init__(self, host: str, user: str = const.DEFAULT_USER,
                 password: str = const.DEFAULT_PASSWORD,
                 port: int = const.WEB_PORT, timeout: float = const.DEFAULT_TIMEOUT,
                 content_type: str = "text/xml; charset=UTF-8"):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.content_type = content_type
        self.user = user
        self._userid = des_hex(user)
        self._passwd = des_hex(password)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def _envelope(self, body: str = "") -> str:
        return _ENVELOPE.format(userid=self._userid, passwd=self._passwd, body=body)

    async def request(self, endpoint: str, body: str = "") -> str:
        """POST one SOAP call over asyncio streams; return the raw response text."""
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        payload = self._envelope(body).encode("utf-8")
        req = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Content-Type: {self.content_type}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8") + payload
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise AnjoyError(f"connection failed for {endpoint}: {e}") from e
        try:
            writer.write(req)
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), self.timeout)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        head, _, body_bytes = raw.partition(b"\r\n\r\n")
        return body_bytes.decode("utf-8", "replace")

    async def call(self, endpoint: str, body: str = "") -> Any:
        text = await self.request(endpoint, body)
        check_response(text, endpoint)
        return extract(text)

    # -- mirror of the sync method surface ---------------------------------
    async def get_version(self):
        return await self.call(const.GET_VERSION)

    async def get_chip_uuid(self):
        return await self.call(const.GET_CHIP_UUID)

    async def get_app_type(self):
        return await self.call(const.GET_APP_TYPE)

    async def info(self) -> dict:
        out: dict[str, Any] = {}
        for key, coro in (("version", self.get_version()),
                          ("chip_uuid", self.get_chip_uuid()),
                          ("app_type", self.get_app_type())):
            try:
                out[key] = await coro
            except Exception as e:  # noqa: BLE001
                out[key] = {"error": str(e)}
        return out

    async def get_config(self, endpoint: str):
        return await self.call(endpoint)

    async def set_config(self, endpoint: str, body_xml: str):
        return await self.call(endpoint, body_xml)

    async def get_users(self):
        return await self.call(const.GET_USER_CONFIG)

    async def ptz_move(self, direction: str, speed: int = 4):
        if direction not in const.PTZ_DIRECTIONS:
            raise ValueError(f"unknown PTZ direction {direction!r}")
        return await self.call(const.PTZ_CMD,
                               _xml(direction, panspeed=speed, tiltspeed=speed))

    async def ptz_stop(self):
        return await self.call(const.PTZ_CMD, _xml(const.PTZ_STOP))

    async def ptz_lens(self, action: str):
        verb = const.PTZ_LENS.get(action)
        if verb is None:
            raise ValueError(f"unknown lens action {action!r}")
        return await self.call(const.PTZ_CMD, _xml(verb))

    async def get_presets(self):
        return await self.call(const.GET_PRESET_LIST)

    async def preset_set(self, number: int):
        return await self.call(const.PRESET_LIST,
                               _xml(const.PRESET_SET, preset=number, flag=1))

    async def preset_goto(self, number: int):
        return await self.call(const.PRESET_LIST, _xml(const.PRESET_GOTO, preset=number))

    async def preset_del(self, number: int):
        return await self.call(const.PRESET_LIST, _xml(const.PRESET_DEL, preset=number))

    async def get_ptz_config(self):
        return await self.call(const.GET_PTZ_CONFIG)

    async def reboot(self):
        return await self.call(const.GET_SYSTEM_CONTROL, _xml("reboot"))
