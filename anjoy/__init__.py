"""python-anjoy: a pure-stdlib client for stock Anjoy (安佳威视) IP cameras.

Anjoy cameras (Sigmastar Infinity6/Mercury6 modules) expose a web **SOAP-over-HTTP
API** authenticated with a fixed-key DES header, plus a **binary AJ protocol**
(``XML_ANJVISION`` / ``XML_TOPSEE``) on ``comm_server``. This package speaks the
SOAP API by default (:class:`AnjoyClient` / :class:`AsyncAnjoyClient`), finds
devices on the LAN (:func:`discover`), and builds RTSP URLs for live video.
"""

from __future__ import annotations

from . import const
from .exceptions import AnjoyError, AnjoyAPIError, LoginError
from .des import des_hex, des_ecb_encrypt, WEBLOGIN_KEY
from .soap import AnjoySOAPTransport, extract, check_response
from .client import AnjoyClient
from . import discovery
from .discovery import discover
from . import rtsp
from .rtsp import build_rtsp_url

__version__ = "0.1.0"

try:  # async client is optional
    from .aio import AsyncAnjoyClient  # noqa: F401
except Exception:  # noqa: BLE001
    AsyncAnjoyClient = None  # type: ignore

__all__ = [
    "AnjoyClient",
    "AsyncAnjoyClient",
    "AnjoySOAPTransport",
    "discover",
    "discovery",
    "build_rtsp_url",
    "rtsp",
    "des_hex",
    "des_ecb_encrypt",
    "WEBLOGIN_KEY",
    "extract",
    "check_response",
    "AnjoyError",
    "AnjoyAPIError",
    "LoginError",
    "const",
    "__version__",
]
