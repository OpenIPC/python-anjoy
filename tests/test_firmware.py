"""ANJOY888 image-header parsing (offline)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import firmware

# First 16 bytes of the vendor firmware_clean_all_cust.bin:
#   41 4e 4a 4f 59 38 38 38  3e f2 9e 32  52 07 00 00
SAMPLE = bytes.fromhex("414e4a4f593838383ef29e3252070000")


class TestFirmwareHeader(unittest.TestCase):
    def test_parse_known_header(self):
        h = firmware.parse_header(SAMPLE)
        self.assertEqual(h.magic, b"ANJOY888")
        self.assertEqual(h.checksum, 0x329EF23E)
        self.assertEqual(h.length, 0x752)   # 1874 bytes

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            firmware.parse_header(b"NOTANJOY01234567")

    def test_upload_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            firmware.upload()


if __name__ == "__main__":
    unittest.main()
