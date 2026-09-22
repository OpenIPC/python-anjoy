"""RTSP URL building/escaping."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import rtsp


class TestRTSP(unittest.TestCase):
    def test_basic_url(self):
        url = rtsp.build_rtsp_url("10.0.0.5", "admin", "123456",
                                  channel=0, subtype=0, template="/live/{channel}_{subtype}")
        self.assertEqual(url, "rtsp://admin:123456@10.0.0.5:554/live/0_0")

    def test_password_escaping(self):
        url = rtsp.build_rtsp_url("10.0.0.5", "admin", "p@ss:w/rd")
        self.assertIn("p%40ss%3Aw%2Frd", url)

    def test_no_credentials(self):
        url = rtsp.build_rtsp_url("10.0.0.5", "", "", template="/x")
        self.assertEqual(url, "rtsp://10.0.0.5:554/x")

    def test_template_leading_slash_added(self):
        url = rtsp.build_rtsp_url("h", "u", "p", template="live/0_0")
        self.assertIn("/live/0_0", url)

    def test_anjoy_url_md5_password(self):
        url = rtsp.anjoy_rtsp_url("10.0.0.5", "admin", "123456", stream=0)
        # password is uppercase MD5 of "123456"
        self.assertEqual(
            url,
            "rtsp://10.0.0.5:554/stream0?username=admin&password=E10ADC3949BA59ABBE56E057F20F883E")

    def test_anjoy_url_substream(self):
        self.assertIn("/stream1", rtsp.anjoy_rtsp_url("h", "u", "p", stream=1))


if __name__ == "__main__":
    unittest.main()
