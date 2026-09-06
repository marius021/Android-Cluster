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


def get_battery(device):
    output = adb(device, "shell", "dumpsys", "battery")

    level = re.search(r"level:\s*(\d+)", output)
    temp = re.search(r"temperature:\s*(\d+)", output)
    status = re.search(r"status:\s*(\d+)", output)

    level = int(level.group(1)) if level else None

    # Android reports battery temperature in tenths of °C
    temp = int(temp.group(1)) / 10 if temp else None

    status_codes = {
        1: "UNKNOWN",
        2: "CHARGING",
        3: "DISCHARGING",
        4: "NOT_CHARGING",
        5: "FULL"
    }

    status = status_codes.get(
        int(status.group(1)) if status else 0,
        "UNKNOWN"
    )

    return level, temp, status

def get_interface_data(device, interface="rmnet0"):
    """Get RX/TX byte counters for cellular interface."""

    rx = adb(
        device,
        "shell",
        "cat",
        f"/sys/class/net/{interface}/statistics/rx_bytes"
    )

    tx = adb(
        device,
        "shell",
        "cat",
        f"/sys/class/net/{interface}/statistics/tx_bytes"
    )

    try:
        rx = int(rx)
    except ValueError:
        rx = None

    try:
        tx = int(tx)
    except ValueError:
        tx = None

    return rx, tx

def get_private_ip(device, interface="rmnet0"):
    output = adb(
        device,
        "shell",
        "ip",
        "-4",
        "addr",
        "show",
        "dev",
        interface
    )

    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", output)

    return match.group(1) if match else "N/A"


def get_public_ip(device):
    """Get public IP using the phone's own mobile connection."""

    output = adb(
        device,
        "shell",
        "curl",
        "-4",
        "-s",
        "--max-time",
        "5",
        "https://api.ipify.org"
    )

    if re.match(r"^\d+\.\d+\.\d+\.\d+$", output):
        return output

    return "N/A"

def get_network_type(device):
    return get_prop(device, "gsm.network.type")

def get_operator(device):
    return get_prop(device, "gsm.operator.alpha")

def get_android_version(device):
    return get_prop(device, "ro.build.version.release")

def get_model(device):
    return get_prop(device, "ro.product.model")

def get_cell_info(device):
    """
     Capture the raw cellular registry information.

    We keep the raw output for now because older Samsung/Android
    versions expose different fields.
    """

    return adb(
        device,
        "shell",
        "dumpsys",
        "telephony.registry"
    )

def format_bytes(value):
    if value is None:
        return "N/A"

    units = ["B", "KB", "MB", "GB", "TB"]

    value = float(value)

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} PB"

def collect(device):
    model = get_model(device)
    android = get_android_version(device)
    operator = get_operator(device)
    network = get_network_type(device)

    battery, temperature, charging = get_battery(device)

    private_ip = get_private_ip(device)
    public_ip = get_public_ip(device)

    rx, tx = get_interface_data(device)

    return {
        "serial": device,
        "model": model,
        "android": android,
        "operator": operator,
        "network": network,
        "battery": battery,
        "temperature": temperature,
        "charging": charging,
        "private_ip": private_ip,
        "public_ip": public_ip,
        "rx": rx,
        "tx": tx
    }

def print_device(data):
    print()
    print("=" * 65)

    print(f"Device       : {data['model']}")
    print(f"Serial       : {data['serial']}")
    print(f"Android      : {data['android']}")

    print("-" * 65)

    print(f"Operator     : {data['operator']}")
    print(f"Network      : {data['network']}")
    print(f"Private IP   : {data['private_ip']}")
    print(f"Public IP    : {data['public_ip']}")

    print("-" * 65)

    print(f"Battery      : {data['battery']}%")
    print(f"Temperature  : {data['temperature']} °C")
    print(f"Power        : {data['charging']}")

    print("-" * 65)

    print(f"RX           : {format_bytes(data['rx'])}")
    print(f"TX           : {format_bytes(data['tx'])}")

    print("=" * 65)


def main():

    print("Android Cluster Monitor")
    print("=======================")
    print(f"Sampling interval: {INTERVAL} seconds")
    print("Press Ctrl+C to stop.\n")

    while True:

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        devices = get_devices()

        print("\n" + "#" * 65)
        print(f"Timestamp: {timestamp}")
        print(f"ADB devices: {len(devices)}")
        print("#" * 65)

        if not devices:
            print("No Android devices connected.")

        for device in devices:

            try:
                data = collect(device)
                print_device(data)

            except Exception as e:
                print(f"\nERROR collecting {device}: {e}")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
