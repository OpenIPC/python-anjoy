"""DES auth must reproduce the vendor des.js output byte-for-byte."""

import json
import os
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import des

VECTORS = os.path.join(os.path.dirname(__file__), "des_vectors.json")


class TestDES(unittest.TestCase):
    def setUp(self):
        with open(VECTORS) as f:
            self.data = json.load(f)

    def test_matches_vendor_vectors(self):
        key = self.data["key"]
        for text, expected in self.data["vectors"].items():
            self.assertEqual(des.des_hex(text, key), expected,
                             msg=f"mismatch for {text!r}")

    def test_empty_is_empty(self):
        self.assertEqual(des.des_hex(""), "")

    def test_ecb_first_block_stable(self):
        # ECB: a longer string shares its first block with the 8-char prefix.
        eight = des.des_hex("abcdefgh")
        nine = des.des_hex("abcdefghi")
        self.assertTrue(nine.startswith(eight))

    def test_bad_key_length(self):
        with self.assertRaises(ValueError):
            des.des_ecb_encrypt(b"data", key=b"short")


if __name__ == "__main__":
    unittest.main()
