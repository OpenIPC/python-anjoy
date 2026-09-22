"""LAN discovery of Anjoy devices via the AJ UDP broadcast probe.

Anjoy cameras answer a ``SYSTEM_SEARCHIPC_MESSAGE`` broadcast: bind local source
port 36584, send the ``XML_ANJVISION`` probe to ``255.255.255.255:3001``. Devices
reply regardless of the host's subnet, so this finds a unit even when it is not
on ``192.168.0.0/24``. Ported from the project's proven ``tools/aj_udpsearch.py``.

Broadcast + ``SO_BINDTODEVICE`` require ``CAP_NET_RAW`` (run as root) when an
interface is pinned.
"""

from __future__ import annotations

import socket
import xml.etree.ElementTree as ET

from . import const

_PROBE = (
    '<?xml version="1.0" encoding="GB2312" ?>'
    "<XML_ANJVISION>"
    f'<MESSAGE_HEADER Msg_type="{const.DISCOVERY_MSG_TYPE}" '
    'Msg_code="1" Msg_flag="0" SOURCE="AJTOOLS" />'
    "<MESSAGE_BODY></MESSAGE_BODY>"
    "</XML_ANJVISION>"
)


def _decode_osd(osd: str | None) -> str | None:
    """The DEVICE_TYPE ``OSD`` attr is hex-encoded ASCII (the device name)."""
    if not osd:
        return None
    try:
        return bytes.fromhex(osd).decode("ascii", "replace")
    except ValueError:
        return osd


def _parse(data: bytes) -> dict | None:
    try:
        root = ET.fromstring(data.decode("utf-8", "replace"))
    except ET.ParseError:
        return None
    lan = root.find(".//LANConfig")
    dev = root.find(".//DEVICE_TYPE")
    sn = root.find(".//IPC_SERIALNUMBER")
    if lan is None:
        return None
    info = {
        "ip": lan.get("IPAddress"),
        "mac": lan.get("MacAddress"),
        "netmask": lan.get("Netmask"),
        "gateway": lan.get("Gateway"),
        "hostname": lan.get("hostname"),
        "mtu": lan.get("MTU"),
    }
    if dev is not None:
        info["name"] = _decode_osd(dev.get("OSD"))
        info["device_type"] = dev.get("DeviceType")
        info["device_module"] = dev.get("DeviceModule")
    if sn is not None:
        info["serial"] = sn.get("SerialNumber")
        info["uuid"] = sn.get("UUID")
    return info


def discover(timeout: float = 3.0, interface: str | None = None) -> list[dict]:
    """Broadcast the AJ probe and collect device replies for *timeout* seconds.

    Returns a list of device-info dicts (ip, mac, name, serial, ...), deduped by
    MAC. Pin a NIC with *interface* (e.g. ``"eth0"``) on multi-homed hosts.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if interface:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                        (interface + "\0").encode())
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", const.DISCOVERY_SRC_PORT))
    sock.settimeout(timeout)

    sock.sendto(_PROBE.encode("utf-8"),
                ("255.255.255.255", const.DISCOVERY_DST_PORT))

    found: list[dict] = []
    seen: set = set()
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65536)
            except socket.timeout:
                break
            info = _parse(data)
            if not info:
                continue
            key = info.get("mac") or info.get("ip") or addr[0]
            if key in seen:
                continue
            seen.add(key)
            info.setdefault("ip", addr[0])
            found.append(info)
    finally:
        sock.close()
    return found
