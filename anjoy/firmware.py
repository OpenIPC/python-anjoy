"""Firmware helpers: ANJOY888 container + /setFirmwareUpgrade — CAPTURE-GATED (WIP).

Anjoy firmware images begin with the ASCII magic ``ANJOY888`` followed by a
checksum and a little-endian length. :func:`parse_header` reads that much
(usable offline); the *upload* flow (``/setFirmwareUpgrade`` +
``/getFirmwareUploadProgress`` and whatever multipart/body form it wants, plus
any signature check) needs confirmation on a live unit before it is wired into
the client. Until then this only inspects an image; it never flashes anything.
"""

from __future__ import annotations

import struct
from typing import NamedTuple

ANJOY_MAGIC = b"ANJOY888"


class AnjoyImageHeader(NamedTuple):
    magic: bytes
    checksum: int      # 4 bytes after the magic (interpretation TBC)
    length: int        # little-endian payload length


def parse_header(data: bytes) -> AnjoyImageHeader:
    """Parse the 16-byte ANJOY888 image header. Raises on a bad magic."""
    if len(data) < 16 or data[:8] != ANJOY_MAGIC:
        raise ValueError("not an ANJOY888 image (bad magic)")
    checksum = struct.unpack_from("<I", data, 8)[0]
    length = struct.unpack_from("<I", data, 12)[0]
    return AnjoyImageHeader(ANJOY_MAGIC, checksum, length)


def upload(*args, **kwargs):
    """Placeholder for the /setFirmwareUpgrade upload flow (not yet implemented)."""
    raise NotImplementedError(
        "Firmware upload is capture-gated: confirm the /setFirmwareUpgrade wire "
        "form and any signature check on a live unit first.")
