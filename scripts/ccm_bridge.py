#!/usr/bin/env python3
"""UECS-CCM HTTP Bridge Server for Raspberry Pi.

Single-file HTTP bridge: receives CCM multicast, caches data, serves via HTTP API.
Python 3.9 compatible, no external dependencies (stdlib only).
Designed to be scp'd to RPi and run directly.

Usage:
    python3 ccm_bridge.py                    # Default port 8520
    python3 ccm_bridge.py --port 8520        # Explicit port
    python3 ccm_bridge.py --bind 0.0.0.0     # Bind address

API Endpoints:
    GET  /sensors?house=h1     Sensor data (InAirTemp, InAirHumid, etc.)
    GET  /actuators?house=h1   Actuator states (Irri, VenFan, etc.)
    GET  /weather?house=h1     Weather data (WAirTemp, WWindSpeed, etc.)
    GET  /nodes                Detected UECS nodes
    GET  /health               Health check (uptime, cache size)
    POST /actuator             Control actuator (safety-guarded)
"""

import argparse
import json
import re
import socket
import struct
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# ── CCM Protocol Constants ──────────────────────────────────────────────

MULTICAST_ADDR = "224.0.0.1"
MULTICAST_PORT = 16520
BUFFER_SIZE = 4096

_CCM_SUFFIX_RE = re.compile(r"\.(mC|cMC|MC)$")

# ── CCM Type Classification ─────────────────────────────────────────────

SENSOR_TYPES = {
    "InAirTemp", "InAirHumid", "InAirCO2", "SoilTemp",
    "InRadiation", "SoilEC", "SoilWC", "Pulse",
    "InAirHD", "InAirAbsHumid", "InAirDP", "IntgRadiation",
}

ACTUATOR_TYPES = {
    "Irri", "VenFan", "CirHoriFan", "AirHeatBurn", "AirHeatHP",
    "CO2Burn", "VenRfWin", "VenSdWin", "ThCrtn", "LsCrtn",
    "AirCoolHP", "AirHumFog",
}

WEATHER_TYPES = {
    "WAirTemp", "WAirHumid", "WWindSpeed", "WWindDir16",
    "WRainfall", "WRainfallAmt", "WLUX",
}

ALLOWED_ACTUATORS = {
    "Irri", "VenFan", "CirHoriFan", "AirHeatBurn", "AirHeatHP",
    "CO2Burn", "VenRfWin", "VenSdWin", "ThCrtn", "LsCrtn",
    "AirCoolHP", "AirHumFog",
}


def strip_ccm_suffix(ccm_type):
    # type: (str) -> str
    return _CCM_SUFFIX_RE.sub("", ccm_type)


def classify_ccm_type(ccm_type):
    # type: (str) -> str
    if ccm_type in SENSOR_TYPES:
        return "sensor"
    if ccm_type in ACTUATOR_TYPES:
        return "actuator"
    if ccm_type in WEATHER_TYPES:
        return "weather"
    return "other"


def parse_ccm_xml(xml_bytes, source_ip=""):
    # type: (bytes, str) -> list
    """Parse UECS XML payload into list of dicts."""
    packets = []
    try:
        text = xml_bytes.decode("utf-8", errors="ignore")
        root = ET.fromstring(text)
    except (ET.ParseError, UnicodeDecodeError):
        return packets

    now = datetime.now(timezone.utc).isoformat()

    for elem in root.findall("DATA"):
        raw_type = elem.get("type", "")
        raw_value = (elem.text or "").strip()
        try:
            value = float(raw_value)
        except ValueError:
            value = raw_value

        def _int(attr, default):
            try:
                return int(elem.get(attr, default))
            except (ValueError, TypeError):
                return default

        packets.append({
            "ccm_type": strip_ccm_suffix(raw_type),
            "raw_type": raw_type,
            "value": value,
            "room": _int("room", 1),
            "region": _int("region", 1),
            "order": _int("order", 1),
            "priority": _int("priority", 29),
            "level": elem.get("lv", "S"),
            "cast": elem.get("cast", "uni"),
            "source_ip": source_ip,
            "timestamp": now,
        })
    return packets


# ── Thread-Safe Cache ────────────────────────────────────────────────────

class SensorCache:
    """Thread-safe cache for CCM sensor/actuator/weather data."""

    def __init__(self):
        self._lock = threading.Lock()
        # key: (house_id, ccm_type) -> dict with packet data + updated_at
        self._data = {}
        # key: ip -> {"ip": str, "ccm_types": set, "last_seen": str}
        self._nodes = {}

    def update(self, packet):
        # type: (dict) -> None
        house_id = "h%d" % packet["room"]
        key = (house_id, packet["ccm_type"])
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._data[key] = {
                "packet": packet,
                "updated_at": now,
            }
            source_ip = packet.get("source_ip", "")
            if source_ip:
                if source_ip not in self._nodes:
                    self._nodes[source_ip] = {
                        "ip": source_ip,
                        "ccm_types": set(),
                        "last_seen": now,
                    }
                node = self._nodes[source_ip]
                node["ccm_types"].add(packet["ccm_type"])
                node["last_seen"] = now

    def get_by_category(self, house_id, category):
        # type: (str, str) -> dict
        result = {}
        with self._lock:
            for (hid, ctype), entry in self._data.items():
                if hid == house_id and classify_ccm_type(ctype) == category:
                    result[ctype] = entry
        return result

    def get_sensors(self, house_id):
        return self.get_by_category(house_id, "sensor")

    def get_actuators(self, house_id):
        return self.get_by_category(house_id, "actuator")

    def get_weather(self, house_id):
        return self.get_by_category(house_id, "weather")

    def list_nodes(self, active_only=True, timeout_seconds=300):
        # type: (bool, int) -> list
        now = datetime.now(timezone.utc)
        with self._lock:
            nodes = []
            for node in self._nodes.values():
                node_copy = {
                    "ip": node["ip"],
                    "ccm_types": sorted(node["ccm_types"]),
                    "last_seen": node["last_seen"],
                }
                nodes.append(node_copy)

        if active_only:
            filtered = []
            for n in nodes:
                last = datetime.fromisoformat(n["last_seen"])
                if (now - last).total_seconds() < timeout_seconds:
                    filtered.append(n)
            nodes = filtered
        return nodes

    def size(self):
        # type: () -> int
        with self._lock:
            return len(self._data)


# ── CCM Multicast Receiver Thread ────────────────────────────────────────

class CcmReceiverThread(threading.Thread):
    """Background thread that receives CCM multicast and updates cache."""

    def __init__(self, cache):
        # type: (SensorCache) -> None
        super().__init__(daemon=True)
        self.cache = cache
        self._running = True

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", MULTICAST_PORT))
        mreq = struct.pack(
            "4sL", socket.inet_aton(MULTICAST_ADDR), socket.INADDR_ANY
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(2.0)

        log("[CCM Receiver] Listening on %s:%d" % (MULTICAST_ADDR, MULTICAST_PORT))

        while self._running:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError as e:
                log("[CCM Receiver] Socket error: %s" % e)
                time.sleep(1.0)
                continue

            packets = parse_ccm_xml(data, source_ip=addr[0])
            for pkt in packets:
                self.cache.update(pkt)

        sock.close()
        log("[CCM Receiver] Stopped")

    def stop(self):
        self._running = False


# ── Actuator Sender (Safety-Guarded) ─────────────────────────────────────

class ActuatorSender:
    """Send CCM control packets with safety guards."""

    def __init__(self):
        self._last_send_time = 0.0
        self._min_interval = 1.0  # seconds
        self._max_irri_duration = 3600  # seconds

    def send(self, ccm_type, value, room=1, region=1, order=1, priority=10):
        # type: (str, int, int, int, int, int) -> str
        if ccm_type not in ALLOWED_ACTUATORS:
            raise ValueError(
                "Actuator '%s' not in allowed list: %s"
                % (ccm_type, sorted(ALLOWED_ACTUATORS))
            )

        now = time.monotonic()
        elapsed = now - self._last_send_time
        if elapsed < self._min_interval:
            raise ValueError(
                "Rate limited: %.1fs since last send, minimum is %.1fs"
                % (elapsed, self._min_interval)
            )

        local_ip = _detect_local_ip()
        xml = (
            '<?xml version="1.0"?>\n'
            '<UECS ver="1.00-E10">\n'
            '  <DATA type="%s" room="%d" region="%d" '
            'order="%d" priority="%d" '
            'lv="A" cast="uni">%s</DATA>\n'
            '  <IP>%s</IP>\n'
            '</UECS>\n'
        ) % (ccm_type, room, region, order, priority, value, local_ip)
        xml_bytes = xml.encode("utf-8")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.sendto(xml_bytes, (MULTICAST_ADDR, MULTICAST_PORT))
        sock.close()

        self._last_send_time = time.monotonic()

        state_str = "ON" if value else "OFF"
        msg = "Sent %s=%s (priority=%d, room=%d)" % (ccm_type, state_str, priority, room)
        log("[Actuator] %s" % msg)
        return msg


def _detect_local_ip():
    # type: () -> str
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((MULTICAST_ADDR, MULTICAST_PORT))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


# ── HTTP API Handler ─────────────────────────────────────────────────────

# Global references set in main()
_cache = None       # type: SensorCache
_sender = None      # type: ActuatorSender
_start_time = None  # type: float


def _entry_to_dict(ccm_type, entry):
    # type: (str, dict) -> dict
    pkt = entry["packet"]
    updated_at = entry["updated_at"]
    now = datetime.now(timezone.utc)
    last = datetime.fromisoformat(updated_at)
    age = (now - last).total_seconds()
    return {
        "ccm_type": ccm_type,
        "value": pkt["value"],
        "room": pkt["room"],
        "region": pkt["region"],
        "priority": pkt["priority"],
        "level": pkt["level"],
        "source_ip": pkt.get("source_ip", ""),
        "updated_at": updated_at,
        "data_age_seconds": round(age, 1),
    }


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the CCM bridge API."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/health":
            self._handle_health()
        elif path == "/sensors":
            house = params.get("house", ["h1"])[0]
            self._handle_sensors(house)
        elif path == "/actuators":
            house = params.get("house", ["h1"])[0]
            self._handle_actuators(house)
        elif path == "/weather":
            house = params.get("house", ["h1"])[0]
            self._handle_weather(house)
        elif path == "/nodes":
            self._handle_nodes()
        else:
            self._json_response(404, {"error": "Not found", "path": self.path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/actuator":
            self._handle_set_actuator()
        else:
            self._json_response(404, {"error": "Not found", "path": self.path})

    def _handle_health(self):
        uptime = time.monotonic() - _start_time
        result = {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "cache_size": _cache.size(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._json_response(200, result)

    def _handle_sensors(self, house_id):
        sensors = _cache.get_sensors(house_id)
        result = {
            "house_id": house_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sensors": {k: _entry_to_dict(k, v) for k, v in sensors.items()},
            "count": len(sensors),
        }
        self._json_response(200, result)

    def _handle_actuators(self, house_id):
        actuators = _cache.get_actuators(house_id)
        result = {
            "house_id": house_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actuators": {k: _entry_to_dict(k, v) for k, v in actuators.items()},
            "count": len(actuators),
        }
        self._json_response(200, result)

    def _handle_weather(self, house_id):
        weather = _cache.get_weather(house_id)
        result = {
            "house_id": house_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "weather": {k: _entry_to_dict(k, v) for k, v in weather.items()},
            "count": len(weather),
        }
        self._json_response(200, result)

    def _handle_nodes(self):
        nodes = _cache.list_nodes(active_only=True)
        # Classify each node
        for node in nodes:
            categories = {classify_ccm_type(t) for t in node["ccm_types"]}
            if "actuator" in categories:
                node["node_type"] = "actuator"
            elif "weather" in categories:
                node["node_type"] = "weather"
            elif "sensor" in categories:
                node["node_type"] = "sensor"
            else:
                node["node_type"] = "other"

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nodes": nodes,
            "count": len(nodes),
        }
        self._json_response(200, result)

    def _handle_set_actuator(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError) as e:
            self._json_response(400, {"error": "Invalid JSON: %s" % e})
            return

        actuator = data.get("actuator", "")
        state = data.get("state")
        priority = data.get("priority", 10)
        room = data.get("room", 1)

        if not actuator:
            self._json_response(400, {"error": "Missing 'actuator' field"})
            return
        if state is None:
            self._json_response(400, {"error": "Missing 'state' field"})
            return

        value = 1 if state else 0

        try:
            msg = _sender.send(actuator, value, room=room, priority=priority)
        except ValueError as e:
            self._json_response(400, {"error": str(e)})
            return

        result = {
            "status": "ok",
            "message": msg,
            "actuator": actuator,
            "state": bool(state),
            "priority": priority,
        }
        self._json_response(200, result)

    def _json_response(self, status_code, data):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Override to use our log format
        log("[HTTP] %s %s" % (self.address_string(), format % args))


# ── Logging ──────────────────────────────────────────────────────────────

def log(msg):
    # type: (str) -> None
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] %s" % (ts, msg))
    sys.stdout.flush()


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    global _cache, _sender, _start_time

    parser = argparse.ArgumentParser(
        description="UECS-CCM HTTP Bridge Server",
        epilog="Listens for CCM multicast and serves data via HTTP API.",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=8520,
        help="HTTP server port (default: 8520)"
    )
    parser.add_argument(
        "--bind", "-b", type=str, default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)"
    )
    args = parser.parse_args()

    _cache = SensorCache()
    _sender = ActuatorSender()
    _start_time = time.monotonic()

    # Start CCM receiver thread
    receiver = CcmReceiverThread(_cache)
    receiver.start()

    # Start HTTP server
    server = HTTPServer((args.bind, args.port), BridgeHandler)
    log("=== UECS-CCM HTTP Bridge ===")
    log("HTTP API: http://%s:%d" % (args.bind, args.port))
    log("Endpoints: /sensors /actuators /weather /nodes /health")
    log("           POST /actuator")
    log("CCM Multicast: %s:%d" % (MULTICAST_ADDR, MULTICAST_PORT))
    log("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
        receiver.stop()
        server.shutdown()
        log("Bye!")


if __name__ == "__main__":
    main()
