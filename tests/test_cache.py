"""Tests for the SensorCache."""

from datetime import datetime, timedelta, timezone

from uecs_ccm_mcp.cache import SensorCache, classify_ccm_type
from uecs_ccm_mcp.ccm_protocol import CcmPacket


def _make_packet(
    ccm_type: str = "InAirTemp",
    value: float = 20.0,
    room: int = 1,
    source_ip: str = "192.168.1.70",
    **kwargs,
) -> CcmPacket:
    return CcmPacket(
        ccm_type=ccm_type,
        raw_type=kwargs.get("raw_type", ccm_type),
        value=value,
        room=room,
        source_ip=source_ip,
        timestamp=datetime.now(timezone.utc),
        **{k: v for k, v in kwargs.items() if k != "raw_type"},
    )


class TestClassifyCcmType:
    def test_sensor(self):
        assert classify_ccm_type("InAirTemp") == "sensor"
        assert classify_ccm_type("InAirHumid") == "sensor"
        assert classify_ccm_type("SoilTemp") == "sensor"

    def test_actuator(self):
        assert classify_ccm_type("IrrircA") == "actuator"
        assert classify_ccm_type("VenFanrcA") == "actuator"

    def test_weather(self):
        assert classify_ccm_type("WAirTemp") == "weather"
        assert classify_ccm_type("WWindSpeed") == "weather"

    def test_other(self):
        assert classify_ccm_type("testFLOW") == "other"
        assert classify_ccm_type("cnd") == "other"


class TestSensorCache:
    def test_update_and_get(self):
        cache = SensorCache()
        pkt = _make_packet("InAirTemp", 22.5)
        cache.update(pkt)

        entry = cache.get("h1", "InAirTemp")
        assert entry is not None
        assert entry.packet.value == 22.5

    def test_get_nonexistent(self):
        cache = SensorCache()
        assert cache.get("h1", "InAirTemp") is None

    def test_update_overwrites(self):
        cache = SensorCache()
        cache.update(_make_packet("InAirTemp", 20.0))
        cache.update(_make_packet("InAirTemp", 25.0))

        entry = cache.get("h1", "InAirTemp")
        assert entry is not None
        assert entry.packet.value == 25.0

    def test_different_rooms(self):
        cache = SensorCache()
        cache.update(_make_packet("InAirTemp", 20.0, room=1))
        cache.update(_make_packet("InAirTemp", 30.0, room=2))

        assert cache.get("h1", "InAirTemp").packet.value == 20.0
        assert cache.get("h2", "InAirTemp").packet.value == 30.0

    def test_get_sensors(self):
        cache = SensorCache()
        cache.update(_make_packet("InAirTemp", 22.0))
        cache.update(_make_packet("InAirHumid", 75.0))
        cache.update(_make_packet("WAirTemp", -5.0))  # weather, not sensor
        cache.update(_make_packet("IrrircA", 1.0))  # actuator

        sensors = cache.get_sensors("h1")
        assert len(sensors) == 2
        assert "InAirTemp" in sensors
        assert "InAirHumid" in sensors

    def test_get_actuators(self):
        cache = SensorCache()
        cache.update(_make_packet("IrrircA", 1.0))
        cache.update(_make_packet("InAirTemp", 22.0))

        actuators = cache.get_actuators("h1")
        assert len(actuators) == 1
        assert "IrrircA" in actuators

    def test_get_weather(self):
        cache = SensorCache()
        cache.update(_make_packet("WAirTemp", -5.0))
        cache.update(_make_packet("WWindSpeed", 3.0))
        cache.update(_make_packet("InAirTemp", 22.0))

        weather = cache.get_weather("h1")
        assert len(weather) == 2
        assert "WAirTemp" in weather
        assert "WWindSpeed" in weather

    def test_list_nodes(self):
        cache = SensorCache()
        cache.update(_make_packet("InAirTemp", 22.0, source_ip="192.168.1.70"))
        cache.update(_make_packet("SoilTemp", 15.0, source_ip="192.168.1.70"))
        cache.update(_make_packet("WAirTemp", -5.0, source_ip="192.168.1.71"))

        nodes = cache.list_nodes()
        assert len(nodes) == 2
        ips = {n.ip for n in nodes}
        assert "192.168.1.70" in ips
        assert "192.168.1.71" in ips

        # Check ccm_types tracked
        node_70 = next(n for n in nodes if n.ip == "192.168.1.70")
        assert "InAirTemp" in node_70.ccm_types
        assert "SoilTemp" in node_70.ccm_types

    def test_list_nodes_active_only(self):
        cache = SensorCache()
        cache.update(_make_packet("InAirTemp", 22.0, source_ip="192.168.1.70"))

        # Manually set an old timestamp
        cache._nodes["192.168.1.70"].last_seen = (
            datetime.now(timezone.utc) - timedelta(seconds=600)
        )

        # Active within 5 min: should be empty
        nodes = cache.list_nodes(active_only=True, timeout_seconds=300)
        assert len(nodes) == 0

        # All nodes
        nodes = cache.list_nodes(active_only=False)
        assert len(nodes) == 1

    def test_all_entries(self):
        cache = SensorCache()
        cache.update(_make_packet("InAirTemp", 22.0))
        cache.update(_make_packet("WAirTemp", -5.0))

        entries = cache.all_entries()
        assert len(entries) == 2
