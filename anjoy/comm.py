"""Binary AJ protocol client (``comm_server``, TCP 8091) — CAPTURE-GATED (WIP).

Anjoy's richer control surface speaks ``XML_ANJVISION`` / ``XML_TOPSEE`` GB2312
XML envelopes over TCP 8091 and carries, among others, ``USER_AUTH_MESSAGE``,
``SYSTEM_CONFIG_GET/SET``, ``PTZ_CONTROL_MESSAGE``, ``ALARM_REPORT_MESSAGE`` and
— notably — ``EXECUTE_USER_CMD`` (arbitrary shell on the device).

The message envelope and the endpoint semantics are known (see the anjoy research
repo ``docs/aj-protocol.md``), but the TCP framing (length prefix?) and the
``USER_AUTH`` / ``EncryptPwd`` handshake must be read off a packet capture of the
vendor tools driving a live unit before this can be implemented. Until then, use
the SOAP client (:class:`anjoy.client.AnjoyClient`).

Live probe (MTF45-4G_AF, port 8091): the port accepts a TCP connection, sends
no banner, and RESETS the connection when fed guessed framing (raw XML envelope,
LE/BE length-prefixed XML all RST). So the framing + USER_AUTH handshake must be
captured from AjDevTools/CameraTestTool driving a unit before this can be
implemented. Do NOT brute-force 8091 against hardware (reboot risk).

For standard device/PTZ/stream control on current-generation Anjoy, use ONVIF
(port 80) + RTSP (see docs/devices.md) — this module is only for the
vendor-specific channel ONVIF does not cover (EXECUTE_USER_CMD, factory config).

Envelope shape (from the SDK), for reference::

    <?xml version="1.0" encoding="GB2312" ?>
    <XML_ANJVISION>
      <MESSAGE_HEADER Msg_type="..." Msg_code="N" Msg_flag="0" SOURCE="AJTOOLS" />
      <MESSAGE_BODY> ... </MESSAGE_BODY>
    </XML_ANJVISION>
"""

from __future__ import annotations

from . import const  # noqa: F401


class AnjoyCommClient:
    """Placeholder for the binary AJ protocol client (not yet implemented)."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "The binary AJ protocol (comm_server, port 8091) is capture-gated; "
            "pin the TCP framing + USER_AUTH handshake from a live capture first. "
            "Use anjoy.client.AnjoyClient (SOAP API) in the meantime.")
