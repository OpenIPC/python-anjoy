#!/usr/bin/env python3
"""Nudge the camera right, stop, then zoom in briefly."""
import sys
import time
from anjoy import AnjoyClient

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.123"
    cam = AnjoyClient(host, "admin", "123456")
    print(cam.info())
    cam.ptz_move("right", speed=5)
    time.sleep(0.6)
    cam.ptz_stop()
    cam.ptz_zoom("tele")
    time.sleep(0.6)
    cam.ptz_stop()
