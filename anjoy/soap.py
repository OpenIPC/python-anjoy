"""Anjoy web SOAP-over-HTTP transport (the primary control surface).

Every request is an HTTP POST to an endpoint path (e.g. ``/getPtzConfig``) whose
body is a tiny SOAP envelope. Auth is a DES(``"WebLogin"``)-hex ``userid`` /
``passwd`` pair in the SOAP header — a *fixed* key, so there is no challenge or
session to manage (see :mod:`anjoy.des`).

Mirrors python-dhip's transport contract:

* :meth:`request` posts and returns the raw response text — never raises on an
  API-level error, only on HTTP/transport failure;
* :meth:`call` posts, raises :class:`AnjoyAPIError` on an error envelope, and
  returns the unwrapped payload via :func:`extract`.

.. note::
   Anjoy responses are a mix of XML fragments (config getters) and JSON (action
   calls). The exact ``Content-Type`` the device wants and the precise response
   shapes are confirmed against a live unit; the parsing here is deliberately
   tolerant and falls back to the raw text it cannot classify.
"""

from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from . import const
from .des import des_hex
from .exceptions import AnjoyAPIError, AnjoyError

_ENVELOPE = (
    '<?xml version="1.0"?>'
    '<soap:Envelope xmlns:soap="http://www.w3.org/2001/12/soap-envelope">'
    "<soap:Header><userid>{userid}</userid><passwd>{passwd}</passwd></soap:Header>"
    "<soap:Body>{body}</soap:Body>"
    "</soap:Envelope>"
)


def _element_to_dict(el: ET.Element) -> dict:
    """Convert an XML element into a JSON-serializable dict."""
    node: dict[str, Any] = dict(el.attrib)
    text = (el.text or "").strip()
    if text:
        node["_text"] = text
    for child in el:
        node.setdefault(child.tag, [])
        node[child.tag].append(_element_to_dict(child))
    return node


def extract(text: str) -> Any:
    """Normalize a (successful) Anjoy response into a Python value.

    JSON bodies decode to their object; XML fragments become a dict keyed by
    top-level tag. Unclassifiable bodies are returned as the stripped string.
    """
    s = (text or "").strip()
    if not s:
        return None
    if s[0] in "{[":
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    # XML: wrap possibly-multiple sibling fragments in a synthetic root.
    try:
        root = ET.fromstring(f"<_anjoy_root>{s}</_anjoy_root>")
    except ET.ParseError:
        return s
    out: dict[str, Any] = {}
    for child in root:
        out.setdefault(child.tag, [])
        out[child.tag].append(_element_to_dict(child))
    # Collapse single-child lists for ergonomics.
    return {k: (v[0] if len(v) == 1 else v) for k, v in out.items()}



def check_response(text: str, endpoint: str) -> None:
    """Raise :class:`AnjoyAPIError` if *text* is an error envelope; else return.

    Shared by the sync and async transports so the wire contract can't drift.
    """
    s = (text or "").strip()
    if not s:
        return
    if s[0] in "{[":
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return
        err = obj.get("error") if isinstance(obj, dict) else None
        if err:
            code = err.get("code") if isinstance(err, dict) else None
            raise AnjoyAPIError(const.error_message(code, "call failed"),
                                code=code, endpoint=endpoint)
        if isinstance(obj, dict) and obj.get("result") is False:
            raise AnjoyAPIError("call returned result=false", endpoint=endpoint)
        return
    try:
        root = ET.fromstring(f"<_anjoy_root>{s}</_anjoy_root>")
    except ET.ParseError:
        return
    err_el = root.find(".//error") or root.find(".//Error")
    if err_el is not None:
        code = err_el.get("code")
        code_i = int(code) if code and code.lstrip("-").isdigit() else None
        raise AnjoyAPIError(const.error_message(code_i, err_el.get("message")
                            or "call failed"), code=code_i, endpoint=endpoint)


class AnjoySOAPTransport:
    """One camera's web SOAP endpoint. Cheap to construct; stateless per call."""

    def __init__(self, host: str, user: str = const.DEFAULT_USER,
                 password: str = const.DEFAULT_PASSWORD,
                 port: int = const.WEB_PORT, timeout: float = const.DEFAULT_TIMEOUT,
                 scheme: str = "http", content_type: str = "text/xml; charset=UTF-8"):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.scheme = scheme
        self.content_type = content_type
        self.user = user
        self._userid = des_hex(user)
        self._passwd = des_hex(password)

    # -- envelope -----------------------------------------------------------
    def build_envelope(self, body: str = "") -> str:
        return _ENVELOPE.format(userid=self._userid, passwd=self._passwd, body=body)

    def _url(self, endpoint: str) -> str:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{self.scheme}://{self.host}:{self.port}{endpoint}"

    # -- rpc ----------------------------------------------------------------
    def request(self, endpoint: str, body: str = "") -> str:
        """POST one SOAP call; return the raw response text. Never raises on an
        API-level error (only on HTTP/transport failure)."""
        data = self.build_envelope(body).encode("utf-8")
        req = urllib.request.Request(
            self._url(endpoint), data=data, method="POST",
            headers={"Content-Type": self.content_type})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:  # noqa: PERF203
            # Some endpoints answer with a non-2xx yet a meaningful XML/JSON body.
            body_text = e.read().decode("utf-8", "replace") if e.fp else ""
            if body_text:
                return body_text
            raise AnjoyError(f"HTTP {e.code} for {endpoint}") from e
        except urllib.error.URLError as e:
            raise AnjoyError(f"connection failed for {endpoint}: {e.reason}") from e

    def call(self, endpoint: str, body: str = "") -> Any:
        """POST one SOAP call, raise on an error envelope, return unwrapped payload."""
        text = self.request(endpoint, body)
        self._check(text, endpoint)
        return extract(text)

    @staticmethod
    def _check(text: str, endpoint: str) -> None:
        check_response(text, endpoint)
