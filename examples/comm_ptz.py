#!/usr/bin/env python3
"""Drive PTZ over the vendor binary AJ protocol (comm_server, TCP 8091)."""
import sys
import time
from anjoy import AnjoyCommClient

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.123"
    with AnjoyCommClient(host, "admin", "123456") as cam:   # connects + logs in
        print("session:", cam.sessionid, "group:", cam.group)
        cam.ptz("zoomtele"); time.sleep(0.6); cam.ptz_stop()
        cam.ptz("PtzRestore")
