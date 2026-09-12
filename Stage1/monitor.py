#!/usr/bin/env python3

import subprocess
import time
import re
import sqlite3
import csv
import os
from datetime import datetime, timezone


# ============================================================
# CONFIGURATION
# ============================================================

POLL_INTERVAL = 30

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DB_FILE = os.path.join(
    BASE_DIR,
    "cluster.db"
)

EVENTS_CSV = os.path.join(
    BASE_DIR,
    "events.csv"
)

EXPORT_DIR = os.path.join(
    BASE_DIR,
    "exports"
)


# ============================================================
# RUNTIME STATE
# ============================================================

previous_traffic = {}
previous_devices = set()
previous_device_state = {}


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

    return run_cmd(
        f'adb -s "{serial}" shell {command}'
    )


# ============================================================
# DATABASE
# ============================================================

def init_database():

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            serial TEXT UNIQUE NOT NULL,

            model TEXT,
            android TEXT,

            first_seen TEXT,
            last_seen TEXT,

            status TEXT,

            enabled INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS measurements (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            timestamp TEXT NOT NULL,

            serial TEXT NOT NULL,

            model TEXT,
            android TEXT,

            operator TEXT,
            network TEXT,
            private_ip TEXT,

            rsrp_dbm REAL,
            rsrq_db REAL,
            rssnr_db REAL,
            cqi INTEGER,

            band INTEGER,
            earfcn INTEGER,
            pci INTEGER,
            carrier_aggregation INTEGER,

            battery_percent INTEGER,
            temperature_c REAL,
            power_status TEXT,

            traffic_source TEXT,

            rx_total_bytes INTEGER,
            tx_total_bytes INTEGER,

            rx_interval_bytes INTEGER,
            tx_interval_bytes INTEGER,

            total_interval_bytes INTEGER,

            rx_rate_bps REAL,
            tx_rate_bps REAL,
            total_rate_bps REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            timestamp TEXT NOT NULL,

            serial TEXT,

            event_type TEXT NOT NULL,

            old_value TEXT,
            new_value TEXT,

            details TEXT
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurements_timestamp
        ON measurements(timestamp)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurements_serial
        ON measurements(serial)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_timestamp
        ON events(timestamp)
    """)

    conn.commit()

    conn.close()


# ============================================================
# DEVICE DATABASE RECORD
# ============================================================

def update_device_record(
    serial,
    model,
    android,
    status,
    timestamp
):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO devices
        (
            serial,
            model,
            android,
            first_seen,
            last_seen,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(serial)
        DO UPDATE SET

            model = excluded.model,
            android = excluded.android,
            last_seen = excluded.last_seen,
            status = excluded.status
        """,
        (
            serial,
            model,
            android,
            timestamp,
            timestamp,
            status
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# EVENT LOGGING
# ============================================================

def log_event(
    serial,
    event_type,
    old_value=None,
    new_value=None,
    details=None
):

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO events
        (
            timestamp,
            serial,
            event_type,
            old_value,
            new_value,
            details
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            serial,
            event_type,
            old_value,
            new_value,
            details
        )
    )

    conn.commit()

    conn.close()

    write_event_csv(
        timestamp,
        serial,
        event_type,
        old_value,
        new_value,
        details
    )


# ============================================================
# EVENTS CSV
# ============================================================

EVENT_FIELDS = [
    "timestamp",
    "serial",
    "event_type",
    "old_value",
    "new_value",
    "details"
]


def init_events_csv():

    if not os.path.exists(EVENTS_CSV):

        with open(
            EVENTS_CSV,
            "w",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=EVENT_FIELDS
            )

            writer.writeheader()


def write_event_csv(
    timestamp,
    serial,
    event_type,
    old_value,
    new_value,
    details
):

    init_events_csv()

    with open(
        EVENTS_CSV,
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=EVENT_FIELDS
        )

        writer.writerow({
            "timestamp": timestamp,
            "serial": serial,
            "event_type": event_type,
            "old_value": old_value,
            "new_value": new_value,
            "details": details
        })


# ============================================================
# ADB DEVICES
# ============================================================

def get_device_states():

    output = run_cmd(
        "adb devices"
    )

    devices = {}

    for line in output.splitlines():

        line = line.strip()

        if not line or line.startswith("List of devices"):
            continue

        parts = line.split()

        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1]

        devices[serial] = state

    return devices


def get_connected_devices():

    states = get_device_states()

    return [
        serial
        for serial, state in states.items()
        if state == "device"
    ]


# ============================================================
# BASIC DEVICE INFORMATION
# ============================================================

def get_prop(serial, prop):

    value = adb(
        serial,
        f"getprop {prop}"
    )

    return value if value else "N/A"


def get_model(serial):

    return get_prop(
        serial,
        "ro.product.model"
    )


def get_android_version(serial):

    return get_prop(
        serial,
        "ro.build.version.release"
    )


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

    level = None
    temp = None
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
        temp = int(
            temp_match.group(1)
        ) / 10.0

    if status_match:

        code = int(
            status_match.group(1)
        )

        states = {
            1: "UNKNOWN",
            2: "CHARGING",
            3: "DISCHARGING",
            4: "NOT CHARGING",
            5: "FULL"
        }

        status = states.get(
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
# MODERN LTE
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

    match = re.search(
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

    if not match:

        match = re.search(
            r"CellSignalStrengthLte:\s*"
            r"rssi=(-?\d+).*?"
            r"rsrp=(-?\d+).*?"
            r"rsrq=(-?\d+).*?"
            r"rssnr=(-?\d+).*?"
            r"\bcqi=(-?\d+)",
            output,
            re.DOTALL
        )

    if match:

        result["rsrp"] = int(match.group(2))
        result["rsrq"] = int(match.group(3))
        result["rssnr"] = int(match.group(4))
        result["cqi"] = int(match.group(5))

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
# OLD SAMSUNG LTE
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

        numbers = re.findall(
            r"-?\d+",
            match.group(1)
        )

        try:

            if len(numbers) >= 10:

                rsrp = int(numbers[8])
                rsrq = int(numbers[9])

                if -160 <= rsrp <= -40:
                    result["rsrp"] = rsrp

                if -40 <= rsrq <= 0:
                    result["rsrq"] = rsrq

        except Exception:
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


def get_cellular_info(
    serial,
    android_version
):

    output = get_telephony_output(
        serial
    )

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

        info["ca"] = (
            get_carrier_aggregation_prop(
                serial
            )
        )

    return info


# ============================================================
# TRAFFIC
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


def get_netstats_traffic(serial):

    output = adb(
        serial,
        "dumpsys netstats detail"
    )

    candidates = []

    for line in output.splitlines():

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

            candidates.append(
                (rx, tx)
            )

    if not candidates:

        return None, None

    return max(
        candidates,
        key=lambda x: x[0] + x[1]
    )


def get_traffic(serial):

    rx, tx = get_sysfs_traffic(
        serial
    )

    if rx is not None and tx is not None:

        return rx, tx, "sysfs"

    rx, tx = get_netstats_traffic(
        serial
    )

    if rx is not None and tx is not None:

        return rx, tx, "netstats"

    return None, None, "unavailable"


# ============================================================
# TRAFFIC DELTA + RATE
# ============================================================

def get_traffic_delta(
    serial,
    rx,
    tx,
    now
):

    if rx is None or tx is None:

        return None, None, None

    previous = previous_traffic.get(
        serial
    )

    previous_traffic[serial] = {
        "rx": rx,
        "tx": tx,
        "time": now
    }

    if previous is None:

        return None, None, None

    delta_rx = (
        rx -
        previous["rx"]
    )

    delta_tx = (
        tx -
        previous["tx"]
    )

    elapsed = (
        now -
        previous["time"]
    )

    if delta_rx < 0:
        delta_rx = None

    if delta_tx < 0:
        delta_tx = None

    if elapsed <= 0:
        elapsed = None

    return (
        delta_rx,
        delta_tx,
        elapsed
    )


# ============================================================
# DATA COLLECTION
# ============================================================

def collect_device_data(serial):

    now_dt = datetime.now()

    now_ts = now_dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    now_epoch = time.time()

    model = get_model(serial)

    android = get_android_version(
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

    cellular = get_cellular_info(
        serial,
        android
    )

    battery, temperature, power = (
        get_battery_info(serial)
    )

    rx, tx, traffic_source = (
        get_traffic(serial)
    )

    delta_rx, delta_tx, elapsed = (
        get_traffic_delta(
            serial,
            rx,
            tx,
            now_epoch
        )
    )

    rx_rate = None
    tx_rate = None
    total_rate = None

    if (
        elapsed
        and
        delta_rx is not None
    ):

        rx_rate = (
            delta_rx * 8 /
            elapsed
        )

    if (
        elapsed
        and
        delta_tx is not None
    ):

        tx_rate = (
            delta_tx * 8 /
            elapsed
        )

    if (
        rx_rate is not None
        and
        tx_rate is not None
    ):

        total_rate = (
            rx_rate +
            tx_rate
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

    return {

        "timestamp": now_ts,

        "serial": serial,
        "model": model,
        "android": android,

        "operator": operator,
        "network": network,
        "private_ip": private_ip,

        "rsrp_dbm": cellular["rsrp"],
        "rsrq_db": cellular["rsrq"],
        "rssnr_db": cellular["rssnr"],
        "cqi": cellular["cqi"],

        "band": cellular["band"],
        "earfcn": cellular["earfcn"],
        "pci": cellular["pci"],
        "carrier_aggregation":
            cellular["ca"],

        "battery_percent": battery,
        "temperature_c": temperature,
        "power_status": power,

        "traffic_source":
            traffic_source,

        "rx_total_bytes": rx,
        "tx_total_bytes": tx,

        "rx_interval_bytes":
            delta_rx,

        "tx_interval_bytes":
            delta_tx,

        "total_interval_bytes":
            total_interval,

        "rx_rate_bps":
            rx_rate,

        "tx_rate_bps":
            tx_rate,

        "total_rate_bps":
            total_rate
    }


# ============================================================
# STORE MEASUREMENT
# ============================================================

def store_measurement(data):

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO measurements (

            timestamp,

            serial,
            model,
            android,

            operator,
            network,
            private_ip,

            rsrp_dbm,
            rsrq_db,
            rssnr_db,
            cqi,

            band,
            earfcn,
            pci,
            carrier_aggregation,

            battery_percent,
            temperature_c,
            power_status,

            traffic_source,

            rx_total_bytes,
            tx_total_bytes,

            rx_interval_bytes,
            tx_interval_bytes,

            total_interval_bytes,

            rx_rate_bps,
            tx_rate_bps,
            total_rate_bps
        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """,

        (
            data["timestamp"],

            data["serial"],
            data["model"],
            data["android"],

            data["operator"],
            data["network"],
            data["private_ip"],

            data["rsrp_dbm"],
            data["rsrq_db"],
            data["rssnr_db"],
            data["cqi"],

            data["band"],
            data["earfcn"],
            data["pci"],

            (
                int(data["carrier_aggregation"])
                if data["carrier_aggregation"]
                is not None
                else None
            ),

            data["battery_percent"],
            data["temperature_c"],
            data["power_status"],

            data["traffic_source"],

            data["rx_total_bytes"],
            data["tx_total_bytes"],

            data["rx_interval_bytes"],
            data["tx_interval_bytes"],

            data["total_interval_bytes"],

            data["rx_rate_bps"],
            data["tx_rate_bps"],
            data["total_rate_bps"]
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# EVENT DETECTION
# ============================================================

def detect_state_changes(data):

    serial = data["serial"]

    current_state = {
        "private_ip":
            data["private_ip"],

        "network":
            data["network"],

        "band":
            data["band"],

        "earfcn":
            data["earfcn"],

        "pci":
            data["pci"],

        "carrier_aggregation":
            data["carrier_aggregation"]
    }

    previous = previous_device_state.get(
        serial
    )

    if previous is None:

        previous_device_state[
            serial
        ] = current_state

        return

    fields = [
        ("private_ip", "IP_CHANGED"),
        ("network", "NETWORK_CHANGED"),
        ("band", "BAND_CHANGED"),
        ("earfcn", "EARFCN_CHANGED"),
        ("pci", "PCI_CHANGED"),
        (
            "carrier_aggregation",
            "CA_CHANGED"
        )
    ]

    for field, event_type in fields:

        old = previous.get(field)
        new = current_state.get(field)

        if (
            old is not None
            and
            new is not None
            and
            old != new
        ):

            log_event(
                serial,
                event_type,
                str(old),
                str(new)
            )

    previous_device_state[
        serial
    ] = current_state


# ============================================================
# DISPLAY HELPERS
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

        if abs(number) < 1024:

            if unit == "B":
                return f"{number:.0f} {unit}"

            return f"{number:.2f} {unit}"

        number /= 1024

    return f"{number:.2f} PiB"


def format_rate(value):

    if value is None:
        return "N/A"

    return (
        format_bytes(
            value / 8
        )
        + "/s"
    )


def display_value(
    value,
    suffix=""
):

    if value is None:
        return "N/A"

    return f"{value}{suffix}"


# ============================================================
# TERMINAL DISPLAY
# ============================================================

def print_device(data):

    print("=" * 76)

    print(
        f"{data['model']} "
        f"({data['serial']})"
    )

    print("-" * 76)

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

    print("-" * 76)

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

    print("-" * 76)

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

    print("-" * 76)

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

    print(
        f"RX interval  : "
        f"{format_bytes(data['rx_interval_bytes'])}"
    )

    print(
        f"TX interval  : "
        f"{format_bytes(data['tx_interval_bytes'])}"
    )

    print(
        f"Total int.   : "
        f"{format_bytes(data['total_interval_bytes'])}"
    )

    print(
        f"RX rate      : "
        f"{format_rate(data['rx_rate_bps'])}"
    )

    print(
        f"TX rate      : "
        f"{format_rate(data['tx_rate_bps'])}"
    )

    print(
        f"Total rate   : "
        f"{format_rate(data['total_rate_bps'])}"
    )

    print("=" * 76)


# ============================================================
# CONNECTION EVENTS
# ============================================================

def process_connection_events(
    current_states
):

    global previous_devices

    current_connected = {
        serial
        for serial, state
        in current_states.items()
        if state == "device"
    }

    # Newly connected
    for serial in (
        current_connected -
        previous_devices
    ):

        log_event(
            serial,
            "ADB_CONNECTED"
        )

    # Disconnected
    for serial in (
        previous_devices -
        current_connected
    ):

        log_event(
            serial,
            "ADB_DISCONNECTED"
        )

    # Unauthorized
    for serial, state in current_states.items():

        if state == "unauthorized":

            if (
                serial not in previous_device_state
                or
                previous_device_state.get(
                    serial,
                    {}
                ).get(
                    "_adb_state"
                ) != "unauthorized"
            ):

                log_event(
                    serial,
                    "ADB_UNAUTHORIZED"
                )

    previous_devices = current_connected


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 76)
    print("ANDROID CLUSTER MONITOR — STAGE 1")
    print("=" * 76)
    print()
    print(
        f"Database : {DB_FILE}"
    )
    print(
        f"Events   : {EVENTS_CSV}"
    )
    print(
        f"Interval : {POLL_INTERVAL} seconds"
    )
    print()
    print(
        "Press Ctrl+C to stop."
    )
    print()

    os.makedirs(
        EXPORT_DIR,
        exist_ok=True
    )

    init_database()
    init_events_csv()

    while True:

        try:

            timestamp = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            print()
            print("#" * 76)
            print(
                "Timestamp:",
                timestamp
            )

            states = get_device_states()

            process_connection_events(
                states
            )

            connected = [
                serial
                for serial, state
                in states.items()
                if state == "device"
            ]

            unauthorized = [
                serial
                for serial, state
                in states.items()
                if state == "unauthorized"
            ]

            print(
                f"ADB devices: "
                f"{len(connected)} connected"
            )

            if unauthorized:

                print(
                    "Unauthorized:",
                    ", ".join(unauthorized)
                )

            print("#" * 76)

            if not connected:

                print()
                print(
                    "No authorized ADB devices."
                )

            for serial in connected:

                try:

                    data = collect_device_data(
                        serial
                    )

                    update_device_record(
                        serial,
                        data["model"],
                        data["android"],
                        "ONLINE",
                        data["timestamp"]
                    )

                    detect_state_changes(
                        data
                    )

                    store_measurement(
                        data
                    )

                    print()

                    print_device(
                        data
                    )

                except Exception as error:

                    print()
                    print(
                        "=" * 76
                    )

                    print(
                        f"Device {serial} ERROR:"
                    )

                    print(error)

                    print(
                        "=" * 76
                    )

                    log_event(
                        serial,
                        "COLLECTION_ERROR",
                        details=str(error)
                    )

            time.sleep(
                POLL_INTERVAL
            )

        except KeyboardInterrupt:

            print()
            print("=" * 76)
            print(
                "Monitor stopped."
            )
            print("=" * 76)
            break

        except Exception as error:

            print()
            print(
                "MAIN LOOP ERROR:",
                error
            )

            time.sleep(
                POLL_INTERVAL
            )


if __name__ == "__main__":
    main()