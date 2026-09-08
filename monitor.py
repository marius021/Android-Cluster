#!/usr/bin/env python3

import subprocess
import time
import re
import csv
import os
from datetime import datetime

POLL_INTERVAL = 30
CSV_FILE = "monitor.csv"

previous_traffic = {}


# ============================================================
# COMMAND EXECUTION
# ============================================================

def run_cmd(command, timeout=10):
    try:
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def adb(serial, command):
    return run_cmd(f'adb -s "{serial}" shell {command}')


# ============================================================
# ADB DEVICES
# ============================================================

def get_devices():
    output = run_cmd("adb devices")

    devices = []

    for line in output.splitlines():
        if "\tdevice" in line:
            serial = line.split()[0]
            devices.append(serial)

    return devices


# ============================================================
# BASIC DEVICE INFORMATION
# ============================================================

def get_prop(serial, prop):
    value = adb(serial, f"getprop {prop}")

    return value if value else "N/A"


def get_model(serial):
    return get_prop(serial, "ro.product.model")


def get_android_version(serial):
    return get_prop(serial, "ro.build.version.release")


def get_operator(serial):

    value = get_prop(
        serial,
        "gsm.operator.alpha"
    ).strip().strip(",")

    if "," in value:

        parts = [
            x.strip()
            for x in value.split(",")
            if x.strip()
        ]

        if parts:
            return parts[0]

    return value if value else "N/A"


def get_network_type(serial):

    value = get_prop(
        serial,
        "gsm.network.type"
    )

    return value if value else "N/A"


# ============================================================
# PRIVATE IP
# ============================================================

def get_private_ip(serial):

    output = adb(
        serial,
        "ip addr show rmnet0"
    )

    match = re.search(
        r"inet\s+(\d+\.\d+\.\d+\.\d+)",
        output
    )

    if match:
        return match.group(1)

    output = adb(
        serial,
        "ifconfig rmnet0"
    )

    match = re.search(
        r"inet addr:(\d+\.\d+\.\d+\.\d+)",
        output
    )

    if match:
        return match.group(1)

    match = re.search(
        r"inet\s+(\d+\.\d+\.\d+\.\d+)",
        output
    )

    if match:
        return match.group(1)

    return "N/A"


# ============================================================
# BATTERY
# ============================================================

def get_battery_info(serial):

    output = adb(
        serial,
        "dumpsys battery"
    )

    level = "N/A"
    temp = "N/A"
    status = "N/A"

    level_match = re.search(
        r"level:\s*(\d+)",
        output
    )

    temp_match = re.search(
        r"temperature:\s*(\d+)",
        output
    )

    status_match = re.search(
        r"status:\s*(\d+)",
        output
    )

    if level_match:
        level = int(level_match.group(1))

    if temp_match:
        temp = int(temp_match.group(1)) / 10.0

    if status_match:

        code = int(status_match.group(1))

        battery_states = {
            1: "UNKNOWN",
            2: "CHARGING",
            3: "DISCHARGING",
            4: "NOT CHARGING",
            5: "FULL"
        }

        status = battery_states.get(
            code,
            str(code)
        )

    return level, temp, status


# ============================================================
# TELEPHONY
# ============================================================

def get_telephony_output(serial):

    return adb(
        serial,
        "dumpsys telephony.registry"
    )


# ============================================================
# MODERN ANDROID / LTE
# ============================================================

def parse_modern_lte(output):

    result = {
        "rsrp": None,
        "rsrq": None,
        "rssnr": None,
        "cqi": None,
        "band": None,
        "earfcn": None,
        "pci": None,
        "ca": None
    }

    signal_match = re.search(
        r"CellSignalStrengthLte:\s*"
        r"rssi=(-?\d+)\s+"
        r"rsrp=(-?\d+)\s+"
        r"rsrq=(-?\d+)\s+"
        r"rssnr=(-?\d+).*?"
        r"cqi(?:TableIndex=\S+\s+)?"
        r"cqi=(-?\d+)",
        output,
        re.DOTALL
    )

    if not signal_match:

        signal_match = re.search(
            r"CellSignalStrengthLte:\s*"
            r"rssi=(-?\d+).*?"
            r"rsrp=(-?\d+).*?"
            r"rsrq=(-?\d+).*?"
            r"rssnr=(-?\d+).*?"
            r"\bcqi=(-?\d+)",
            output,
            re.DOTALL
        )

    if signal_match:

        result["rsrp"] = int(
            signal_match.group(2)
        )

        result["rsrq"] = int(
            signal_match.group(3)
        )

        result["rssnr"] = int(
            signal_match.group(4)
        )

        result["cqi"] = int(
            signal_match.group(5)
        )

    # Active LTE registration
    reg_match = re.search(
        r"NetworkRegistrationInfo\{"
        r"[^}]*?"
        r"transportType=WWAN.*?"
        r"accessNetworkTechnology=LTE.*?"
        r"CellIdentityLte:\{(.*?)\}",
        output,
        re.DOTALL
    )

    search_area = (
        reg_match.group(1)
        if reg_match
        else output
    )

    band_match = re.search(
        r"mBands=\[(\d+)",
        search_area
    )

    earfcn_match = re.search(
        r"mEarfcn=(\d+)",
        search_area
    )

    pci_match = re.search(
        r"mPci=(\d+)",
        search_area
    )

    if band_match:
        result["band"] = int(
            band_match.group(1)
        )

    if earfcn_match:
        result["earfcn"] = int(
            earfcn_match.group(1)
        )

    if pci_match:
        result["pci"] = int(
            pci_match.group(1)
        )

    if "isUsingCarrierAggregation=true" in output:
        result["ca"] = True

    elif "isUsingCarrierAggregation=false" in output:
        result["ca"] = False

    return result


# ============================================================
# OLD SAMSUNG / ANDROID
# ============================================================

def parse_old_samsung_lte(output):

    result = {
        "rsrp": None,
        "rsrq": None,
        "rssnr": None,
        "cqi": None,
        "band": None,
        "earfcn": None,
        "pci": None,
        "ca": None
    }

    match = re.search(
        r"mSignalStrength=SignalStrength:\s+"
        r"(.+?)(?:\n|$)",
        output
    )

    if match:

        raw = match.group(1)

        numbers = re.findall(
            r"-?\d+",
            raw
        )

        try:

            if len(numbers) >= 10:

                rsrp = int(numbers[8])
                rsrq = int(numbers[9])

                if -160 <= rsrp <= -40:
                    result["rsrp"] = rsrp

                if -40 <= rsrq <= 0:
                    result["rsrq"] = rsrq

        except (ValueError, IndexError):
            pass

    return result


# ============================================================
# CARRIER AGGREGATION
# ============================================================

def get_carrier_aggregation_prop(serial):

    output = adb(
        serial,
        "getprop net.lte_ca_enabled"
    ).strip().lower()

    if output == "true":
        return True

    if output == "false":
        return False

    return None


def get_cellular_info(serial, android_version):

    output = get_telephony_output(serial)

    try:
        major = int(
            android_version.split(".")[0]
        )

    except Exception:
        major = 0

    if major >= 8:

        info = parse_modern_lte(
            output
        )

    else:

        info = parse_old_samsung_lte(
            output
        )

    if info["ca"] is None:

        ca_prop = get_carrier_aggregation_prop(
            serial
        )

        if ca_prop is not None:
            info["ca"] = ca_prop

    return info


# ============================================================
# TRAFFIC - SYSFS
# ============================================================

def get_sysfs_traffic(serial):

    rx = adb(
        serial,
        "cat /sys/class/net/rmnet0/statistics/rx_bytes"
    )

    tx = adb(
        serial,
        "cat /sys/class/net/rmnet0/statistics/tx_bytes"
    )

    try:

        return (
            int(rx.strip()),
            int(tx.strip())
        )

    except Exception:

        return None, None


# ============================================================
# TRAFFIC - NETSTATS
# ============================================================

def get_netstats_traffic(serial):

    output = adb(
        serial,
        "dumpsys netstats detail"
    )

    lines = output.splitlines()

    candidates = []

    for line in lines:

        line = line.strip()

        match = re.match(
            r"^\d+\s+rmnet0\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)$",
            line
        )

        if match:

            rx = int(
                match.group(1)
            )

            tx = int(
                match.group(3)
            )

            candidates.append(
                (rx, tx)
            )

    if not candidates:
        return None, None

    return max(
        candidates,
        key=lambda x: x[0] + x[1]
    )


# ============================================================
# TRAFFIC
# ============================================================

def get_traffic(serial):

    rx, tx = get_sysfs_traffic(
        serial
    )

    if rx is not None and tx is not None:

        return (
            rx,
            tx,
            "sysfs"
        )

    rx, tx = get_netstats_traffic(
        serial
    )

    if rx is not None and tx is not None:

        return (
            rx,
            tx,
            "netstats"
        )

    return (
        None,
        None,
        "unavailable"
    )


# ============================================================
# TRAFFIC DELTA
# ============================================================

def get_traffic_delta(
    serial,
    rx,
    tx
):

    if rx is None or tx is None:

        return None, None

    previous = previous_traffic.get(
        serial
    )

    previous_traffic[serial] = {
        "rx": rx,
        "tx": tx
    }

    if previous is None:

        return None, None

    delta_rx = (
        rx -
        previous["rx"]
    )

    delta_tx = (
        tx -
        previous["tx"]
    )

    if delta_rx < 0:
        delta_rx = None

    if delta_tx < 0:
        delta_tx = None

    return (
        delta_rx,
        delta_tx
    )


# ============================================================
# FORMATTING
# ============================================================

def format_bytes(value):

    if value is None:
        return "N/A"

    units = [
        "B",
        "KiB",
        "MiB",
        "GiB",
        "TiB"
    ]

    number = float(value)

    for unit in units:

        if abs(number) < 1024.0:

            if unit == "B":

                return f"{number:.0f} {unit}"

            return f"{number:.2f} {unit}"

        number /= 1024.0

    return f"{number:.2f} PiB"


def csv_value(value):

    if value is None:
        return ""

    if isinstance(value, bool):

        return (
            "True"
            if value
            else "False"
        )

    return value


def display_value(
    value,
    suffix=""
):

    if value is None:
        return "N/A"

    return f"{value}{suffix}"


# ============================================================
# CSV INITIALIZATION
# ============================================================

CSV_FIELDS = [

    "timestamp",

    "serial",
    "model",
    "android",

    "operator",
    "network",
    "private_ip",

    "rsrp_dbm",
    "rsrq_db",
    "rssnr_db",
    "cqi",

    "band",
    "earfcn",
    "pci",
    "carrier_aggregation",

    "battery_percent",
    "temperature_c",
    "power_status",

    "traffic_source",

    "rx_total_bytes",
    "tx_total_bytes",

    "rx_interval_bytes",
    "tx_interval_bytes",

    "total_interval_bytes"
]


def initialize_csv():

    if not os.path.exists(
        CSV_FILE
    ):

        with open(
            CSV_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=CSV_FIELDS
            )

            writer.writeheader()

        print(
            f"Created CSV file: {CSV_FILE}"
        )


# ============================================================
# CSV LOGGING
# ============================================================

def log_to_csv(data):

    initialize_csv()

    with open(
        CSV_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS
        )

        writer.writerow(
            {
                field: csv_value(
                    data.get(field)
                )
                for field in CSV_FIELDS
            }
        )


# ============================================================
# DEVICE
# ============================================================

def collect_device_data(serial):

    model = get_model(serial)

    android_version = get_android_version(
        serial
    )

    operator = get_operator(
        serial
    )

    network = get_network_type(
        serial
    )

    private_ip = get_private_ip(
        serial
    )

    cell = get_cellular_info(
        serial,
        android_version
    )

    battery, temperature, power = (
        get_battery_info(serial)
    )

    rx, tx, traffic_source = (
        get_traffic(serial)
    )

    delta_rx, delta_tx = (
        get_traffic_delta(
            serial,
            rx,
            tx
        )
    )

    total_interval = None

    if (
        delta_rx is not None
        and
        delta_tx is not None
    ):

        total_interval = (
            delta_rx +
            delta_tx
        )

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    data = {

        "timestamp": timestamp,

        "serial": serial,
        "model": model,
        "android": android_version,

        "operator": operator,
        "network": network,
        "private_ip": private_ip,

        "rsrp_dbm": cell["rsrp"],
        "rsrq_db": cell["rsrq"],
        "rssnr_db": cell["rssnr"],
        "cqi": cell["cqi"],

        "band": cell["band"],
        "earfcn": cell["earfcn"],
        "pci": cell["pci"],
        "carrier_aggregation": cell["ca"],

        "battery_percent": battery,
        "temperature_c": temperature,
        "power_status": power,

        "traffic_source": traffic_source,

        "rx_total_bytes": rx,
        "tx_total_bytes": tx,

        "rx_interval_bytes": delta_rx,
        "tx_interval_bytes": delta_tx,

        "total_interval_bytes": total_interval
    }

    return data


# ============================================================
# TERMINAL DISPLAY
# ============================================================

def print_device_data(data):

    print("=" * 70)

    print(
        f"{data['model']} "
        f"({data['serial']})"
    )

    print("-" * 70)

    print(
        f"Android      : "
        f"{data['android']}"
    )

    print(
        f"Operator     : "
        f"{data['operator']}"
    )

    print(
        f"Network      : "
        f"{data['network']}"
    )

    print(
        f"Private IP   : "
        f"{data['private_ip']}"
    )

    print("-" * 70)

    print(
        f"RSRP         : "
        f"{display_value(data['rsrp_dbm'], ' dBm')}"
    )

    print(
        f"RSRQ         : "
        f"{display_value(data['rsrq_db'], ' dB')}"
    )

    print(
        f"RSSNR        : "
        f"{display_value(data['rssnr_db'], ' dB')}"
    )

    print(
        f"CQI          : "
        f"{display_value(data['cqi'])}"
    )

    print(
        f"Band         : "
        f"{display_value(data['band'])}"
    )

    print(
        f"EARFCN       : "
        f"{display_value(data['earfcn'])}"
    )

    print(
        f"PCI          : "
        f"{display_value(data['pci'])}"
    )

    print(
        f"Carrier Agg. : "
        f"{display_value(data['carrier_aggregation'])}"
    )

    print("-" * 70)

    print(
        f"Battery      : "
        f"{display_value(data['battery_percent'], '%')}"
    )

    print(
        f"Temperature  : "
        f"{display_value(data['temperature_c'], ' °C')}"
    )

    print(
        f"Power        : "
        f"{data['power_status']}"
    )

    print("-" * 70)

    print(
        f"Traffic src  : "
        f"{data['traffic_source']}"
    )

    print(
        f"RX total     : "
        f"{format_bytes(data['rx_total_bytes'])}"
    )

    print(
        f"TX total     : "
        f"{format_bytes(data['tx_total_bytes'])}"
    )

    if data["rx_interval_bytes"] is None:

        print(
            "RX interval  : baseline"
        )

    else:

        print(
            f"RX interval  : +"
            f"{format_bytes(data['rx_interval_bytes'])}"
        )

    if data["tx_interval_bytes"] is None:

        print(
            "TX interval  : baseline"
        )

    else:

        print(
            f"TX interval  : +"
            f"{format_bytes(data['tx_interval_bytes'])}"
        )

    if data["total_interval_bytes"] is not None:

        print(
            f"Total int.   : +"
            f"{format_bytes(data['total_interval_bytes'])}"
        )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "Android Cluster Monitor"
    )

    print(
        f"CSV logging: {CSV_FILE}"
    )

    print(
        f"Poll interval: {POLL_INTERVAL} seconds"
    )

    print(
        "Press Ctrl+C to stop"
    )

    print()

    initialize_csv()

    while True:

        try:

            devices = get_devices()

            print()

            print(
                "#" * 70
            )

            print(
                "Timestamp:",
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            print(
                f"ADB devices: {len(devices)}"
            )

            print(
                "#" * 70
            )

            print()

            if not devices:

                print(
                    "No ADB devices detected."
                )

            for serial in devices:

                try:

                    data = collect_device_data(
                        serial
                    )

                    print_device_data(
                        data
                    )

                    log_to_csv(
                        data
                    )

                except Exception as error:

                    print(
                        "=" * 70
                    )

                    print(
                        f"Device: {serial}"
                    )

                    print(
                        f"ERROR: {error}"
                    )

                    print(
                        "=" * 70
                    )

                print()

            time.sleep(
                POLL_INTERVAL
            )

        except KeyboardInterrupt:

            print()

            print(
                "Monitor stopped."
            )

            break


if __name__ == "__main__":

    main()