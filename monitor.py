#!/usr/bin/env python3

import subprocess
import time
import re
from datetime import datetime

POLL_INTERVAL = 30

# Stores previous traffic counters so we can calculate interval deltas
previous_traffic = {}


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


def get_devices():
    output = run_cmd("adb devices")
    devices = []

    for line in output.splitlines():
        if "\tdevice" in line:
            serial = line.split()[0]
            devices.append(serial)

    return devices


def get_prop(serial, prop):
    value = adb(serial, f"getprop {prop}")
    return value if value else "N/A"


def get_model(serial):
    return get_prop(serial, "ro.product.model")


def get_android_version(serial):
    return get_prop(serial, "ro.build.version.release")


def get_operator(serial):
    value = get_prop(serial, "gsm.operator.alpha")

    # Samsung sometimes returns:
    # orange,
    # or duplicated values
    value = value.strip().strip(",")

    if "," in value:
        parts = [x.strip() for x in value.split(",") if x.strip()]
        if parts:
            return parts[0]

    return value if value else "N/A"


def get_network_type(serial):
    value = get_prop(serial, "gsm.network.type")
    return value if value else "N/A"


def get_private_ip(serial):
    output = adb(serial, "ip addr show rmnet0")

    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", output)

    if match:
        return match.group(1)

    # fallback for older Android
    output = adb(serial, "ifconfig rmnet0")

    match = re.search(r"inet addr:(\d+\.\d+\.\d+\.\d+)", output)

    if match:
        return match.group(1)

    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", output)

    if match:
        return match.group(1)

    return "N/A"


def get_battery_info(serial):
    output = adb(serial, "dumpsys battery")

    level = "N/A"
    temp = "N/A"
    status = "N/A"

    level_match = re.search(r"level:\s*(\d+)", output)
    temp_match = re.search(r"temperature:\s*(\d+)", output)
    status_match = re.search(r"status:\s*(\d+)", output)

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

        status = battery_states.get(code, str(code))

    return level, temp, status


def get_telephony_output(serial):
    return adb(serial, "dumpsys telephony.registry")


def parse_modern_lte(output):
    """
    Android 10+ style:
    CellSignalStrengthLte:
        rssi=-102
        rsrp=-102
        rsrq=-10
        rssnr=2
        cqi=5
    """

    result = {
        "rsrp": None,
        "rsrq": None,
        "rssnr": None,
        "cqi": None,
        "band": None,
        "earfcn": None,
        "pci": None,
        "ca": None,
    }

    signal_match = re.search(
        r"CellSignalStrengthLte:\s*"
        r"rssi=(-?\d+)\s+"
        r"rsrp=(-?\d+)\s+"
        r"rsrq=(-?\d+)\s+"
        r"rssnr=(-?\d+).*?"
        r"cqi(?:TableIndex=\S+\s+)?cqi=(-?\d+)",
        output,
        re.DOTALL
    )

    if not signal_match:
        # fallback for Samsung output where cqiTableIndex appears
        # between RSSNR and CQI
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
        result["rsrp"] = int(signal_match.group(2))
        result["rsrq"] = int(signal_match.group(3))
        result["rssnr"] = int(signal_match.group(4))
        result["cqi"] = int(signal_match.group(5))

    # Prefer active LTE registration block
    reg_match = re.search(
        r"NetworkRegistrationInfo\{[^}]*?"
        r"transportType=WWAN.*?"
        r"accessNetworkTechnology=LTE.*?"
        r"CellIdentityLte:\{(.*?)\}",
        output,
        re.DOTALL
    )

    search_area = reg_match.group(1) if reg_match else output

    band_match = re.search(r"mBands=\[(\d+)", search_area)
    earfcn_match = re.search(r"mEarfcn=(\d+)", search_area)
    pci_match = re.search(r"mPci=(\d+)", search_area)

    if band_match:
        result["band"] = int(band_match.group(1))

    if earfcn_match:
        result["earfcn"] = int(earfcn_match.group(1))

    if pci_match:
        result["pci"] = int(pci_match.group(1))

    if "isUsingCarrierAggregation=true" in output:
        result["ca"] = True
    elif "isUsingCarrierAggregation=false" in output:
        result["ca"] = False

    return result


def parse_old_samsung_lte(output):
    """
    Old Samsung / Android 6 format.

    Example:
    SignalStrength:
    3 99 -3 -200 -3 -200 -1 5 -106 -14 -10 2 ...

    On this device, positions:
    index 8 = LTE RSRP
    index 9 = LTE RSRQ

    Other numeric fields are intentionally not labelled because
    the vendor layout is ambiguous.
    """

    result = {
        "rsrp": None,
        "rsrq": None,
        "rssnr": None,
        "cqi": None,
        "band": None,
        "earfcn": None,
        "pci": None,
        "ca": None,
    }

    match = re.search(
        r"mSignalStrength=SignalStrength:\s+(.+?)(?:\n|$)",
        output
    )

    if match:
        raw = match.group(1)

        numbers = re.findall(r"-?\d+", raw)

        try:
            if len(numbers) >= 10:
                rsrp = int(numbers[8])
                rsrq = int(numbers[9])

                # Basic sanity checking
                if -160 <= rsrp <= -40:
                    result["rsrp"] = rsrp

                if -40 <= rsrq <= 0:
                    result["rsrq"] = rsrq

        except (ValueError, IndexError):
            pass

    return result


def get_carrier_aggregation_prop(serial):
    output = adb(serial, "getprop net.lte_ca_enabled").strip().lower()

    if output == "true":
        return True

    if output == "false":
        return False

    return None


def get_cellular_info(serial, android_version):
    output = get_telephony_output(serial)

    try:
        major = int(android_version.split(".")[0])
    except Exception:
        major = 0

    if major >= 8:
        info = parse_modern_lte(output)
    else:
        info = parse_old_samsung_lte(output)

    # On old Samsung phones CA is available via getprop
    if info["ca"] is None:
        ca_prop = get_carrier_aggregation_prop(serial)

        if ca_prop is not None:
            info["ca"] = ca_prop

    return info


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
        rx_value = int(rx.strip())
        tx_value = int(tx.strip())

        return rx_value, tx_value

    except Exception:
        return None, None


def get_netstats_traffic(serial):
    """
    Android newer versions.

    We look for the interface summary table:

    ifaceIndex ifaceName rxBytes rxPackets txBytes txPackets
    14 rmnet0 1630220949 2460867 1495228883 2777892
    """

    output = adb(serial, "dumpsys netstats detail")

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
            rx = int(match.group(1))
            tx = int(match.group(3))

            candidates.append((rx, tx))

    if not candidates:
        return None, None

    # The summary interface row should normally have the largest totals.
    return max(
        candidates,
        key=lambda x: x[0] + x[1]
    )


def get_traffic(serial):
    # First try old/direct sysfs counters
    rx, tx = get_sysfs_traffic(serial)

    if rx is not None and tx is not None:
        return rx, tx, "sysfs"

    # Fall back to Android NetworkStats
    rx, tx = get_netstats_traffic(serial)

    if rx is not None and tx is not None:
        return rx, tx, "netstats"

    return None, None, "unavailable"


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


def get_traffic_delta(serial, rx, tx):
    if rx is None or tx is None:
        return None, None

    previous = previous_traffic.get(serial)

    previous_traffic[serial] = {
        "rx": rx,
        "tx": tx
    }

    if previous is None:
        return None, None

    delta_rx = rx - previous["rx"]
    delta_tx = tx - previous["tx"]

    # Counter reset / reboot / NetworkStats reset
    if delta_rx < 0:
        delta_rx = None

    if delta_tx < 0:
        delta_tx = None

    return delta_rx, delta_tx


def display_value(value, suffix=""):
    if value is None:
        return "N/A"

    return f"{value}{suffix}"


def print_device(serial):
    model = get_model(serial)
    android_version = get_android_version(serial)
    operator = get_operator(serial)
    network = get_network_type(serial)
    private_ip = get_private_ip(serial)

    cell = get_cellular_info(
        serial,
        android_version
    )

    battery, temperature, power = get_battery_info(serial)

    rx, tx, traffic_source = get_traffic(serial)

    delta_rx, delta_tx = get_traffic_delta(
        serial,
        rx,
        tx
    )

    print("=" * 70)

    print(f"{model} ({serial})")

    print("-" * 70)

    print(f"Android      : {android_version}")
    print(f"Operator     : {operator}")
    print(f"Network      : {network}")
    print(f"Private IP   : {private_ip}")

    print("-" * 70)

    print(
        f"RSRP         : "
        f"{display_value(cell['rsrp'], ' dBm')}"
    )

    print(
        f"RSRQ         : "
        f"{display_value(cell['rsrq'], ' dB')}"
    )

    print(
        f"RSSNR        : "
        f"{display_value(cell['rssnr'], ' dB')}"
    )

    print(
        f"CQI          : "
        f"{display_value(cell['cqi'])}"
    )

    print(
        f"Band         : "
        f"{display_value(cell['band'])}"
    )

    print(
        f"EARFCN       : "
        f"{display_value(cell['earfcn'])}"
    )

    print(
        f"PCI          : "
        f"{display_value(cell['pci'])}"
    )

    print(
        f"Carrier Agg. : "
        f"{display_value(cell['ca'])}"
    )

    print("-" * 70)

    print(
        f"Battery      : "
        f"{display_value(battery, '%')}"
    )

    print(
        f"Temperature  : "
        f"{display_value(temperature, ' °C')}"
    )

    print(f"Power        : {power}")

    print("-" * 70)

    print(f"Traffic src  : {traffic_source}")
    print(f"RX total     : {format_bytes(rx)}")
    print(f"TX total     : {format_bytes(tx)}")

    if delta_rx is None:
        print("RX interval  : baseline")
    else:
        print(f"RX interval  : +{format_bytes(delta_rx)}")

    if delta_tx is None:
        print("TX interval  : baseline")
    else:
        print(f"TX interval  : +{format_bytes(delta_tx)}")

    if delta_rx is not None and delta_tx is not None:
        total_delta = delta_rx + delta_tx

        print(
            f"Total int.   : +{format_bytes(total_delta)}"
        )

    print("=" * 70)


def main():
    print("Android Cluster Monitor")
    print("Press Ctrl+C to stop")
    print()

    while True:
        try:
            devices = get_devices()

            print()
            print("#" * 70)
            print(
                "Timestamp:",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            print(f"ADB devices: {len(devices)}")
            print("#" * 70)
            print()

            if not devices:
                print("No ADB devices detected.")

            for serial in devices:
                try:
                    print_device(serial)

                except Exception as error:
                    print("=" * 70)
                    print(f"Device: {serial}")
                    print(f"ERROR: {error}")
                    print("=" * 70)

                print()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print()
            print("Monitor stopped.")
            break


if __name__ == "__main__":
    main()