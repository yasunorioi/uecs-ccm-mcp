"""Thread-safe sensor data cache for UECS-CCM packets.

Stores latest values keyed by (house_id, ccm_type).
Provides categorized access: sensors, actuators, weather, nodes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .ccm_protocol import CcmPacket

# CCM type classification
SENSOR_TYPES = {
    "InAirTemp", "InAirHumid", "InAirCO2", "SoilTemp",
    "InRadiation", "SoilEC", "SoilMoisture", "Pulse",
}

ACTUATOR_TYPES = {
    "IrrircA", "Irriopr", "VenFanrcA", "CurtainrcA",
    "MistrcA", "CO2rcA", "SideWinrcA", "HeatrcA",
    "CircFanrcA", "VentrcA",
}

WEATHER_TYPES = {
    "WAirTemp", "WAirHumid", "WWindSpeed", "WWindDir16",
    "WRainfall", "WRainfallAmt",
}


def classify_ccm_type(ccm_type: str) -> str:
    """Classify a CCM type into sensor/actuator/weather/other."""
    if ccm_type in SENSOR_TYPES:
        return "sensor"
    if ccm_type in ACTUATOR_TYPES:
        return "actuator"
    if ccm_type in WEATHER_TYPES:
        return "weather"
    return "other"


@dataclass
class CacheEntry:
    """Single cached value with metadata."""
    packet: CcmPacket
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class NodeInfo:
    """Tracked UECS node."""
    ip: str
    ccm_types: set[str] = field(default_factory=set)
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SensorCache:
    """Thread-safe cache for latest CCM sensor/actuator/weather data.

    Key: (house_id, ccm_type) tuple.
    house_id is derived from room number (room N -> "hN").
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple[str, str], CacheEntry] = {}
        self._nodes: dict[str, NodeInfo] = {}  # keyed by IP

    def update(self, packet: CcmPacket) -> None:
        """Update cache with a new packet."""
        house_id = f"h{packet.room}"
        key = (house_id, packet.ccm_type)
        now = datetime.now(timezone.utc)

        with self._lock:
            self._data[key] = CacheEntry(packet=packet, updated_at=now)

            # Track node
            if packet.source_ip:
                if packet.source_ip not in self._nodes:
                    self._nodes[packet.source_ip] = NodeInfo(ip=packet.source_ip)
                node = self._nodes[packet.source_ip]
                node.ccm_types.add(packet.ccm_type)
                node.last_seen = now

    def get(self, house_id: str, ccm_type: str) -> Optional[CacheEntry]:
        """Get a single cached entry."""
        with self._lock:
            return self._data.get((house_id, ccm_type))

    def get_by_category(
        self, house_id: str, category: str
    ) -> dict[str, CacheEntry]:
        """Get all entries for a house matching a category.

        Args:
            house_id: House ID (e.g., "h1").
            category: "sensor", "actuator", "weather", or "other".

        Returns:
            Dict mapping ccm_type -> CacheEntry.
        """
        result: dict[str, CacheEntry] = {}
        with self._lock:
            for (hid, ctype), entry in self._data.items():
                if hid == house_id and classify_ccm_type(ctype) == category:
                    result[ctype] = entry
        return result

    def get_sensors(self, house_id: str) -> dict[str, CacheEntry]:
        return self.get_by_category(house_id, "sensor")

    def get_actuators(self, house_id: str) -> dict[str, CacheEntry]:
        return self.get_by_category(house_id, "actuator")

    def get_weather(self, house_id: str) -> dict[str, CacheEntry]:
        return self.get_by_category(house_id, "weather")

    def list_nodes(self, active_only: bool = True, timeout_seconds: int = 300) -> list[NodeInfo]:
        """List tracked UECS nodes.

        Args:
            active_only: If True, only return nodes seen within timeout_seconds.
            timeout_seconds: Activity timeout (default 5 minutes).
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            nodes = list(self._nodes.values())

        if active_only:
            nodes = [
                n for n in nodes
                if (now - n.last_seen).total_seconds() < timeout_seconds
            ]
        return nodes

    def all_entries(self) -> dict[tuple[str, str], CacheEntry]:
        """Return a snapshot of all cached entries."""
        with self._lock:
            return dict(self._data)
