#!/usr/bin/env python3
"""Find Anjoy cameras on the LAN."""
import json
from anjoy import discover

if __name__ == "__main__":
    for dev in discover(timeout=3.0):
        print(json.dumps(dev, ensure_ascii=False))
