#!/usr/bin/env python3

import subprocess
import time 
import re
from datetime import datetime


ADB = "/usr/bin/adb"
INTERVAL = 30

def adb(device, *args):
    """Run an ADB command for a specific device."""
    cmd = [ADB, "-s", device, *args]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def get_devices():
    """Return connected ADB devices."""
    output = subprocess.run(
        [ADB, "devices"],
        capture_output=True,
        text=True
    ).stdout

    devices = []

    for line in output.splitlines():
        if "\tdevice" in line:
            serial = line.split("\t")[0]
            devices.append(serial)

    return devices


def get_prop(device, prop):
    return adb(device, "shell", "getprop", prop)