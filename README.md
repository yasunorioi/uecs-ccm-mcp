# uecs-ccm-mcp

MCP server for UECS-CCM greenhouse monitoring and control.

Directly participates in [UECS](https://uecs.jp/) (Ubiquitous Environment Control System) CCM multicast network to read sensor data and send actuator commands — no MQTT broker required.

## What is UECS-CCM?

UECS is the Japanese standard protocol for greenhouse environment control, using UDP multicast (224.0.0.1:16520) with XML payloads. CCM (Communication Control Module) packets carry sensor readings (temperature, humidity, CO2, etc.) and actuator commands (irrigation, ventilation, curtain, etc.).

## Features

- **5 MCP tools** for LLM-driven greenhouse control
- **Zero-config networking**: joins UDP multicast automatically
- **Safety guardrails**: actuator allowlist, rate limiting, max irrigation duration
- **No MQTT dependency**: talks CCM directly
- **Standalone test scripts**: scp to RPi and run with zero dependencies

## Quick Start

### Install

```bash
pip install uecs-ccm-mcp
```

### Run as MCP Server

```bash
uecs-ccm-mcp
```

### Claude Desktop / Claude Code

Add to your MCP config:

```json
{
  "mcpServers": {
    "greenhouse": {
      "command": "uecs-ccm-mcp"
    }
  }
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `get_sensor_data` | Indoor sensors (temp, humidity, CO2, soil temp, etc.) |
| `get_actuator_status` | Actuator states (irrigation, ventilation, curtain) |
| `set_actuator` | Control actuators with safety guardrails |
| `get_weather_summary` | Outdoor weather station data |
| `list_nodes` | Active UECS nodes on the network |

## Field Test Scripts

Standalone scripts (Python standard library only) for testing on RPi:

```bash
# Receive CCM packets
python3 scripts/ccm_receive_test.py
python3 scripts/ccm_receive_test.py --filter InAirTemp

# Send control packets (confirm before sending)
python3 scripts/ccm_send_test.py Irri 1      # Irrigation ON
python3 scripts/ccm_send_test.py Irri 0      # Irrigation OFF
python3 scripts/ccm_send_test.py VenRfWin 50 # Roof window 50%
```

## Supported Hardware

Tested with:
- **ArSprout** greenhouse controller (sensor/actuator nodes)
- **Raspberry Pi** as CCM participant
- Any UECS-compliant node

## Safety

- Only allowlisted actuator types can be controlled
- Minimum 1 second between commands (rate limiting)
- Maximum 3600 second irrigation duration
- Auto-OFF timer support

## License

MIT
