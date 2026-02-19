"""UECS-CCM MCP Server.

Provides 5 tools for greenhouse monitoring and control via UECS-CCM protocol:
- get_sensor_data: Read indoor sensor values
- get_actuator_status: Read actuator states
- set_actuator: Control actuators (irrigation, ventilation, etc.)
- get_weather_summary: Read outdoor weather station data
- list_nodes: List active UECS nodes on the network

Runs a background UDP multicast receiver to cache incoming CCM data.
Uses stdio transport for Claude Desktop/Code integration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from .cache import SensorCache, classify_ccm_type
from .ccm_receiver import CcmReceiver
from .ccm_sender import CcmSender, SafetyLimits

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared state
_cache = SensorCache()
_receiver = CcmReceiver(_cache)
_sender = CcmSender()


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Start/stop the CCM receiver as a background task."""
    await _receiver.start()
    logger.info("UECS-CCM MCP server started")
    try:
        yield {"cache": _cache, "sender": _sender}
    finally:
        await _receiver.stop()
        logger.info("UECS-CCM MCP server stopped")


mcp = FastMCP(
    "uecs-ccm-mcp",
    instructions="MCP server for UECS-CCM greenhouse monitoring and control",
    lifespan=lifespan,
)


def _entry_to_dict(ccm_type: str, entry) -> dict:
    """Convert a CacheEntry to a JSON-serializable dict."""
    pkt = entry.packet
    age = (datetime.now(timezone.utc) - entry.updated_at).total_seconds()
    return {
        "ccm_type": ccm_type,
        "value": pkt.value,
        "room": pkt.room,
        "region": pkt.region,
        "priority": pkt.priority,
        "level": pkt.level,
        "source_ip": pkt.source_ip,
        "updated_at": entry.updated_at.isoformat(),
        "data_age_seconds": round(age, 1),
    }


@mcp.tool()
def get_sensor_data(house_id: str = "h1", sensor_types: list[str] | None = None) -> str:
    """Get latest greenhouse sensor data (temperature, humidity, CO2, etc.).

    Args:
        house_id: House identifier (e.g., "h1"). Derived from CCM room number.
        sensor_types: Filter by sensor types. None or ["all"] returns all sensors.
    """
    sensors = _cache.get_sensors(house_id)

    if sensor_types and "all" not in sensor_types:
        # Map friendly names to CCM types
        name_map = {
            "temperature": "InAirTemp",
            "humidity": "InAirHumid",
            "co2": "InAirCO2",
            "soil_temp": "SoilTemp",
            "solar_radiation": "InRadiation",
        }
        wanted = {name_map.get(t, t) for t in sensor_types}
        sensors = {k: v for k, v in sensors.items() if k in wanted}

    result = {
        "house_id": house_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sensors": {k: _entry_to_dict(k, v) for k, v in sensors.items()},
        "count": len(sensors),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def get_actuator_status(house_id: str = "h1") -> str:
    """Get current actuator states (irrigation, ventilation, curtain, etc.).

    Args:
        house_id: House identifier (e.g., "h1").
    """
    actuators = _cache.get_actuators(house_id)

    result = {
        "house_id": house_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actuators": {k: _entry_to_dict(k, v) for k, v in actuators.items()},
        "count": len(actuators),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def set_actuator(
    actuator: str,
    state: bool,
    priority: int = 10,
    duration_seconds: int | None = None,
    house_id: str = "h1",
) -> str:
    """Control a greenhouse actuator via CCM packet.

    SAFETY: Only allowed actuator types can be controlled. Irrigation has a
    maximum duration of 3600 seconds. Minimum 1 second between commands.

    Args:
        actuator: CCM actuator type (e.g., "Irri", "VenFan", "VenRfWin", "ThCrtn").
        state: True=ON/OPEN, False=OFF/CLOSE.
        priority: CCM priority (1=emergency, 10=normal, 30=low).
        duration_seconds: Auto-OFF timer in seconds. If set, actuator turns OFF
                         automatically after this duration.
        house_id: House identifier (e.g., "h1").
    """
    room = int(house_id.replace("h", "")) if house_id.startswith("h") else 1
    value = 1 if state else 0

    try:
        if duration_seconds is not None:
            # Use asyncio for duration-based control
            loop = asyncio.get_event_loop()
            msg = loop.run_until_complete(
                _sender.send_with_duration(
                    actuator, value, duration_seconds,
                    room=room, priority=priority,
                )
            )
        else:
            msg = _sender.send(actuator, value, room=room, priority=priority)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps(
        {
            "status": "ok",
            "message": msg,
            "actuator": actuator,
            "state": state,
            "priority": priority,
            "duration_seconds": duration_seconds,
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def get_weather_summary(house_id: str = "h1") -> str:
    """Get outdoor weather station data (temperature, humidity, wind, rainfall).

    Data comes from ArSprout external weather sensors (e.g., Misol WH65)
    via UECS-CCM multicast.

    Args:
        house_id: House identifier (e.g., "h1").
    """
    weather = _cache.get_weather(house_id)

    # Map CCM types to friendly names
    friendly = {
        "WAirTemp": "outside_temperature_c",
        "WAirHumid": "outside_humidity_pct",
        "WWindSpeed": "wind_speed_ms",
        "WWindDir16": "wind_direction_16",
        "WRainfall": "rainfall_flag",
        "WRainfallAmt": "rainfall_amount_mm",
    }

    data = {}
    for ccm_type, entry in weather.items():
        name = friendly.get(ccm_type, ccm_type)
        data[name] = {
            "value": entry.packet.value,
            "data_age_seconds": round(
                (datetime.now(timezone.utc) - entry.updated_at).total_seconds(), 1
            ),
        }

    result = {
        "house_id": house_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "weather": data,
        "count": len(data),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def list_nodes(active_only: bool = True) -> str:
    """List UECS nodes detected on the network.

    Nodes are tracked by their source IP address and the CCM data types
    they broadcast.

    Args:
        active_only: If True, only show nodes seen in the last 5 minutes.
    """
    nodes = _cache.list_nodes(active_only=active_only)

    node_list = []
    for node in nodes:
        # Determine node type from CCM types
        categories = {classify_ccm_type(t) for t in node.ccm_types}
        if "actuator" in categories:
            node_type = "actuator"
        elif "weather" in categories:
            node_type = "weather"
        elif "sensor" in categories:
            node_type = "sensor"
        else:
            node_type = "other"

        node_list.append({
            "ip": node.ip,
            "last_seen": node.last_seen.isoformat(),
            "ccm_types": sorted(node.ccm_types),
            "node_type": node_type,
        })

    result = {
        "active_only": active_only,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nodes": node_list,
        "count": len(node_list),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


def run() -> None:
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
