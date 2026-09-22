"""Discovery probe/parse round-trip against a loopback UDP responder."""

import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import discovery

REPLY = (
    '<?xml version="1.0" encoding="GB2312" ?>'
    "<XML_ANJVISION>"
    '<MESSAGE_HEADER Msg_type="SYSTEM_SEARCHIPC_MESSAGE" />'
    "<MESSAGE_BODY>"
    '<DEVICE_TYPE DeviceType="IPC" DeviceModule="MC-J40H" OSD="4d432d4a343048" />'
    '<IPC_SERIALNUMBER SerialNumber="SN123" UUID="U1" />'
    '<LANConfig MacAddress="AA:BB:CC:DD:EE:FF" IPAddress="192.168.0.123" '
    'Netmask="255.255.255.0" Gateway="192.168.0.1" hostname="cam" MTU="1500" />'
    "</MESSAGE_BODY></XML_ANJVISION>"
)


class TestDiscoveryParse(unittest.TestCase):
    def test_parse_reply(self):
        info = discovery._parse(REPLY.encode("utf-8"))
        self.assertEqual(info["ip"], "192.168.0.123")
        self.assertEqual(info["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(info["serial"], "SN123")
        self.assertEqual(info["device_module"], "MC-J40H")
        # OSD "4d432d4a343048" is hex ASCII for "MC-J40H"
        self.assertEqual(info["name"], "MC-J40H")

    def test_decode_osd(self):
        self.assertEqual(discovery._decode_osd("4d432d4a343048"), "MC-J40H")
        self.assertIsNone(discovery._decode_osd(None))

    def test_parse_garbage(self):
        self.assertIsNone(discovery._parse(b"not xml"))


if __name__ == "__main__":
    unittest.main()
