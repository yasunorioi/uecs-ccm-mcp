"""Integration tests for the MCP server tools.

Tests the tool functions directly (without MCP transport layer).
"""

import json
from datetime import datetime, timezone

from uecs_ccm_mcp import server
from uecs_ccm_mcp.cache import SensorCache
from uecs_ccm_mcp.ccm_protocol import CcmPacket
from uecs_ccm_mcp.ccm_sender import CcmSender, SafetyLimits


def _populate_cache(cache: SensorCache) -> None:
    """Populate cache with realistic test data."""
    now = datetime.now(timezone.utc)

    test_packets = [
        CcmPacket("InAirTemp", "InAirTemp.mC", 22.5, room=1, source_ip="192.168.1.70", timestamp=now),
        CcmPacket("InAirHumid", "InAirHumid.mC", 75.0, room=1, source_ip="192.168.1.70", timestamp=now),
        CcmPacket("InAirCO2", "InAirCO2.mC", 450.0, room=1, source_ip="192.168.1.70", timestamp=now),
        CcmPacket("SoilTemp", "SoilTemp.mC", 15.0, room=1, source_ip="192.168.1.70", timestamp=now),
        CcmPacket("WAirTemp", "WAirTemp.mC", -5.2, room=1, region=41, source_ip="192.168.1.71", timestamp=now),
        CcmPacket("WAirHumid", "WAirHumid.mC", 60.0, room=1, region=41, source_ip="192.168.1.71", timestamp=now),
        CcmPacket("WWindSpeed", "WWindSpeed.mC", 3.5, room=1, region=41, source_ip="192.168.1.71", timestamp=now),
        CcmPacket("WRainfall", "WRainfall.mC", 0.0, room=1, region=41, source_ip="192.168.1.71", timestamp=now),
        CcmPacket("Irri", "Irri", 0.0, room=1, source_ip="192.168.1.72", priority=15, level="A", timestamp=now),
        CcmPacket("testFLOW", "testFLOW.cMC", 42.0, room=1, source_ip="192.168.1.74", timestamp=now),
    ]

    for pkt in test_packets:
        cache.update(pkt)


class TestGetSensorData:
    def setup_method(self):
        server._cache = SensorCache()
        _populate_cache(server._cache)

    def test_get_all_sensors(self):
        result = json.loads(server.get_sensor_data("h1"))
        assert result["house_id"] == "h1"
        assert result["count"] == 4
        assert "InAirTemp" in result["sensors"]
        assert result["sensors"]["InAirTemp"]["value"] == 22.5

    def test_get_filtered_sensors(self):
        result = json.loads(server.get_sensor_data("h1", ["temperature"]))
        assert result["count"] == 1
        assert "InAirTemp" in result["sensors"]

    def test_get_nonexistent_house(self):
        result = json.loads(server.get_sensor_data("h99"))
        assert result["count"] == 0

    def test_data_age_is_present(self):
        result = json.loads(server.get_sensor_data("h1"))
        for sensor in result["sensors"].values():
            assert "data_age_seconds" in sensor


class TestGetActuatorStatus:
    def setup_method(self):
        server._cache = SensorCache()
        _populate_cache(server._cache)

    def test_get_actuators(self):
        result = json.loads(server.get_actuator_status("h1"))
        assert result["house_id"] == "h1"
        assert "Irri" in result["actuators"]
        assert result["actuators"]["Irri"]["value"] == 0.0


class TestSetActuator:
    def setup_method(self):
        server._sender = CcmSender(SafetyLimits(min_send_interval_seconds=0.0))

    def test_set_allowed_actuator(self):
        result = json.loads(server.set_actuator("Irri", True))
        assert result["status"] == "ok"
        assert result["actuator"] == "Irri"
        assert result["state"] is True

    def test_set_disallowed_actuator(self):
        result = json.loads(server.set_actuator("HackerRelay", True))
        assert "error" in result

    def test_set_with_priority(self):
        result = json.loads(server.set_actuator("VenFan", True, priority=1))
        assert result["status"] == "ok"
        assert result["priority"] == 1


class TestGetWeatherSummary:
    def setup_method(self):
        server._cache = SensorCache()
        _populate_cache(server._cache)

    def test_get_weather(self):
        result = json.loads(server.get_weather_summary("h1"))
        assert result["house_id"] == "h1"
        assert result["count"] == 4
        assert "outside_temperature_c" in result["weather"]
        assert result["weather"]["outside_temperature_c"]["value"] == -5.2


class TestListNodes:
    def setup_method(self):
        server._cache = SensorCache()
        _populate_cache(server._cache)

    def test_list_all_nodes(self):
        result = json.loads(server.list_nodes(active_only=True))
        assert result["count"] >= 3  # At least sensor, weather, actuator nodes

    def test_node_types(self):
        result = json.loads(server.list_nodes(active_only=True))
        types = {n["node_type"] for n in result["nodes"]}
        assert "sensor" in types
        assert "weather" in types

    def test_node_has_ccm_types(self):
        result = json.loads(server.list_nodes(active_only=True))
        for node in result["nodes"]:
            assert len(node["ccm_types"]) > 0
            assert "ip" in node
            assert "last_seen" in node
