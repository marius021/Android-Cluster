#!/usr/bin/env python3

import subprocess
import time
import re
from datetime import datetime


ADB = "/usr/bin/adb"
INTERVAL = 30


def adb(device, *args):
    cmd = [ADB, "-s", device, *args]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        return result.stdout.strip()

    except Exception:
        return ""


def get_devices():

    result = subprocess.run(
        [ADB, "devices"],
        capture_output=True,
        text=True
    )

    devices = []

    for line in result.stdout.splitlines():

        if "\tdevice" in line:

            serial = line.split("\t")[0]

            devices.append(serial)

    return devices


def prop(device, name):

    return adb(
        device,
        "shell",
        "getprop",
        name
    )


def get_model(device):

    return prop(
        device,
        "ro.product.model"
    )


def get_android(device):

    return prop(
        device,
        "ro.build.version.release"
    )


def get_operator(device):

    value = prop(
        device,
        "gsm.operator.alpha"
    )

    return value.strip(" ,")


def get_network(device):

    value = prop(
        device,
        "gsm.network.type"
    )

    return value


def get_battery(device):

    output = adb(
        device,
        "shell",
        "dumpsys",
        "battery"
    )

    level = None
    temperature = None
    status = "UNKNOWN"

    match = re.search(
        r"level:\s*(\d+)",
        output
    )

    if match:
        level = int(match.group(1))

    match = re.search(
        r"temperature:\s*(\d+)",
        output
    )

    if match:
        temperature = int(match.group(1)) / 10

    match = re.search(
        r"status:\s*(\d+)",
        output
    )

    if match:

        status_codes = {
            1: "UNKNOWN",
            2: "CHARGING",
            3: "DISCHARGING",
            4: "NOT_CHARGING",
            5: "FULL"
        }

        status = status_codes.get(
            int(match.group(1)),
            "UNKNOWN"
        )

    return level, temperature, status


def get_private_ip(device):

    output = adb(
        device,
        "shell",
        "ip",
        "addr",
        "show",
        "dev",
        "rmnet0"
    )

    match = re.search(
        r"inet\s+(\d+\.\d+\.\d+\.\d+)",
        output
    )

    if match:
        return match.group(1)

    return "N/A"


def get_traffic(device):

    rx_path = "/sys/class/net/rmnet0/statistics/rx_bytes"
    tx_path = "/sys/class/net/rmnet0/statistics/tx_bytes"

    rx = adb(
        device,
        "shell",
        "cat",
        rx_path
    )

    tx = adb(
        device,
        "shell",
        "cat",
        tx_path
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


def get_telephony(device):

    output = adb(
        device,
        "shell",
        "dumpsys",
        "telephony.registry"
    )

    return output


def parse_cellular(output):

    data = {
        "rsrp": None,
        "rsrq": None,
        "rssnr": None,
        "cqi": None,
        "band": None,
        "earfcn": None,
        "pci": None,
        "ca": None
    }

    # SignalStrengthLte

    match = re.search(
        r"CellSignalStrengthLte:\s*"
        r"rssi=(-?\d+)\s+"
        r"rsrp=(-?\d+)\s+"
        r"rsrq=(-?\d+)\s+"
        r"rssnr=(-?\d+).*?"
        r"cqi=(-?\d+)",
        output
    )

    if match:

        data["rsrp"] = int(match.group(2))
        data["rsrq"] = int(match.group(3))
        data["rssnr"] = int(match.group(4))
        data["cqi"] = int(match.group(5))

    # EARFCN

    match = re.search(
        r"mEarfcn=(\d+)",
        output
    )

    if match:
        data["earfcn"] = int(match.group(1))

    # Band

    match = re.search(
        r"mBands=\[([^\]]+)\]",
        output
    )

    if match:

        bands = match.group(1)

        data["band"] = bands

    # PCI

    match = re.search(
        r"mPci=(\d+)",
        output
    )

    if match:
        data["pci"] = int(match.group(1))

    # Carrier aggregation

    if "isUsingCarrierAggregation=true" in output:
        data["ca"] = True

    elif "isUsingCarrierAggregation=false" in output:
        data["ca"] = False

    return data


def format_bytes(value):

    if value is None:
        return "N/A"

    value = float(value)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB"
    ]

    for unit in units:

        if value < 1024:
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} PB"


def collect(device):

    battery, temperature, charging = get_battery(device)

    traffic_rx, traffic_tx = get_traffic(device)

    telephony = get_telephony(device)

    cellular = parse_cellular(
        telephony
    )

    return {

        "serial": device,

        "model": get_model(device),

        "android": get_android(device),

        "operator": get_operator(device),

        "network": get_network(device),

        "private_ip": get_private_ip(device),

        "battery": battery,

        "temperature": temperature,

        "charging": charging,

        "rx": traffic_rx,

        "tx": traffic_tx,

        "rsrp": cellular["rsrp"],

        "rsrq": cellular["rsrq"],

        "rssnr": cellular["rssnr"],

        "cqi": cellular["cqi"],

        "band": cellular["band"],

        "earfcn": cellular["earfcn"],

        "pci": cellular["pci"],

        "ca": cellular["ca"]

    }


def print_device(data):

    print()
    print("=" * 70)

    print(
        f"{data['model']} "
        f"({data['serial']})"
    )

    print("-" * 70)

    print(
        f"Android      : {data['android']}"
    )

    print(
        f"Operator     : {data['operator']}"
    )

    print(
        f"Network      : {data['network']}"
    )

    print(
        f"Private IP   : {data['private_ip']}"
    )

    print("-" * 70)

    print(
        f"RSRP         : "
        f"{data['rsrp']} dBm"
    )

    print(
        f"RSRQ         : "
        f"{data['rsrq']} dB"
    )

    print(
        f"RSSNR        : "
        f"{data['rssnr']} dB"
    )

    print(
        f"CQI          : "
        f"{data['cqi']}"
    )

    print(
        f"Band         : "
        f"{data['band']}"
    )

    print(
        f"EARFCN       : "
        f"{data['earfcn']}"
    )

    print(
        f"PCI          : "
        f"{data['pci']}"
    )

    print(
        f"Carrier Agg. : "
        f"{data['ca']}"
    )

    print("-" * 70)

    print(
        f"Battery      : "
        f"{data['battery']}%"
    )

    print(
        f"Temperature  : "
        f"{data['temperature']} °C"
    )

    print(
        f"Power        : "
        f"{data['charging']}"
    )

    print("-" * 70)

    print(
        f"RX           : "
        f"{format_bytes(data['rx'])}"
    )

    print(
        f"TX           : "
        f"{format_bytes(data['tx'])}"
    )

    print("=" * 70)


def main():

    print(
        "Android Cluster Monitor"
    )

    print(
        "======================="
    )

    print(
        f"Sampling interval: "
        f"{INTERVAL} seconds"
    )

    print(
        "Press Ctrl+C to stop."
    )

    while True:

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        devices = get_devices()

        print()
        print("#" * 70)

        print(
            f"Timestamp: {timestamp}"
        )

        print(
            f"ADB devices: {len(devices)}"
        )

        print("#" * 70)

        if not devices:

            print(
                "No Android devices connected."
            )

        for device in devices:

            try:

                data = collect(device)

                print_device(data)

            except Exception as e:

                print(
                    f"ERROR: {device}: {e}"
                )

        time.sleep(INTERVAL)


if __name__ == "__main__":

    main()