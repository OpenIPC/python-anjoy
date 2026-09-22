"""High-level synchronous client for stock Anjoy cameras.

``AnjoyClient`` wraps the web SOAP API (:mod:`anjoy.soap`) with one thin method
per operation, mirroring python-dhip's ``DahuaClient`` style. The binary AJ
protocol (:mod:`anjoy.comm`) and firmware upload are separate, capture-gated
surfaces bolted on once their wire details are confirmed on hardware.
"""

from __future__ import annotations

from typing import Any

from . import const
from .soap import AnjoySOAPTransport


def _xml(cmd: str, **fields: Any) -> str:
    """Build an ``<xml><cmd>..</cmd>..</xml>`` PTZ/preset body."""
    inner = f"<cmd>{cmd}</cmd>"
    for k, v in fields.items():
        inner += f"<{k}>{v}</{k}>"
    return f"<xml>{inner}</xml>"


class AnjoyClient:
    """Manage a stock Anjoy camera over its web SOAP API.

    >>> cam = AnjoyClient("192.168.0.123", "admin", "123456")
    >>> cam.get_version()          # doctest: +SKIP
    >>> cam.ptz_move("right"); cam.ptz_stop()   # doctest: +SKIP
    """

    def __init__(self, host: str, user: str = const.DEFAULT_USER,
                 password: str = const.DEFAULT_PASSWORD, **kwargs):
        self.soap = AnjoySOAPTransport(host, user, password, **kwargs)

    # -- context manager (SOAP is stateless, but keep parity with dhip) ------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    # -- raw escape hatch ---------------------------------------------------
    def call(self, endpoint: str, body: str = "") -> Any:
        return self.soap.call(endpoint, body)

    def request(self, endpoint: str, body: str = "") -> str:
        return self.soap.request(endpoint, body)

    # -- device / system info ----------------------------------------------
    def get_version(self) -> Any:
        return self.call(const.GET_VERSION)

    def get_chip_uuid(self) -> Any:
        return self.call(const.GET_CHIP_UUID)

    def get_app_type(self) -> Any:
        return self.call(const.GET_APP_TYPE)

    def get_all_config(self) -> Any:
        return self.call(const.GET_ALL_CONFIG)

    def get_boot_status(self) -> Any:
        return self.call(const.GET_SYS_BOOT_STATUS)

    def info(self) -> dict:
        """Best-effort device summary (version + chip + app type)."""
        out: dict[str, Any] = {}
        for key, fn in (("version", self.get_version),
                        ("chip_uuid", self.get_chip_uuid),
                        ("app_type", self.get_app_type)):
            try:
                out[key] = fn()
            except Exception as e:  # noqa: BLE001
                out[key] = {"error": str(e)}
        return out

    # -- generic config -----------------------------------------------------
    def get_config(self, endpoint: str) -> Any:
        """Call a ``/get*Config`` endpoint by full path."""
        return self.call(endpoint)

    def set_config(self, endpoint: str, body_xml: str) -> Any:
        """Call a ``/set*`` endpoint with a raw config XML body."""
        return self.call(endpoint, body_xml)

    def get_network(self) -> Any:
        return self.call(const.GET_NETWORK_CONFIG)

    def get_time(self) -> Any:
        return self.call(const.GET_TIME_CONFIG)

    def get_users(self) -> Any:
        return self.call(const.GET_USER_CONFIG)

    # -- PTZ ----------------------------------------------------------------
    def ptz_move(self, direction: str, speed: int = 4) -> Any:
        """Start a pan/tilt move (press-and-hold; call :meth:`ptz_stop`)."""
        if direction not in const.PTZ_DIRECTIONS:
            raise ValueError(f"unknown PTZ direction {direction!r}; "
                             f"expected one of {const.PTZ_DIRECTIONS}")
        return self.call(const.PTZ_CMD,
                         _xml(direction, panspeed=speed, tiltspeed=speed))

    def ptz_stop(self) -> Any:
        return self.call(const.PTZ_CMD, _xml(const.PTZ_STOP))

    def ptz_lens(self, action: str) -> Any:
        """Drive the zoom/focus/iris lens. *action* is a key of
        :data:`const.PTZ_LENS` (e.g. ``"zoom_tele"``)."""
        verb = const.PTZ_LENS.get(action)
        if verb is None:
            raise ValueError(f"unknown lens action {action!r}; "
                             f"expected one of {sorted(const.PTZ_LENS)}")
        return self.call(const.PTZ_CMD, _xml(verb))

    def ptz_zoom(self, direction: str) -> Any:
        return self.ptz_lens("zoom_tele" if direction == "tele" else "zoom_wide")

    def ptz_focus(self, direction: str) -> Any:
        return self.ptz_lens("focus_far" if direction == "far" else "focus_near")

    def ptz_iris(self, direction: str) -> Any:
        return self.ptz_lens("iris_open" if direction == "open" else "iris_close")

    # presets
    def get_presets(self) -> Any:
        return self.call(const.GET_PRESET_LIST)

    def preset_set(self, number: int) -> Any:
        return self.call(const.PRESET_LIST,
                         _xml(const.PRESET_SET, preset=number, flag=1))

    def preset_goto(self, number: int) -> Any:
        return self.call(const.PRESET_LIST, _xml(const.PRESET_GOTO, preset=number))

    def preset_del(self, number: int) -> Any:
        return self.call(const.PRESET_LIST, _xml(const.PRESET_DEL, preset=number))

    def get_ptz_config(self) -> Any:
        return self.call(const.GET_PTZ_CONFIG)

    # -- day/night, LED, encode --------------------------------------------
    def set_ircut(self, mode: str) -> Any:
        return self.call(const.SET_IRCUT_DAYNIGHT, _xml("mode", value=mode))

    def set_led(self, on: bool) -> Any:
        return self.call(const.SET_LED_KEEP_ON, _xml("led", value=1 if on else 0))

    def force_idr(self) -> Any:
        return self.call(const.FORCE_IDR)

    # -- maintenance --------------------------------------------------------
    def reboot(self) -> Any:
        return self.call(const.GET_SYSTEM_CONTROL, _xml("reboot"))
