"""UECS-CCM protocol parser and builder.

UECS (Ubiquitous Environment Control System) uses UDP multicast (224.0.0.1:16520)
with XML payloads for greenhouse sensor data and actuator control.

XML format:
    <UECS ver="1.00-E10">
      <DATA type="InAirTemp.mC" room="1" region="1" order="1"
            priority="29" lv="S" cast="uni">1.8</DATA>
    </UECS>
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

MULTICAST_ADDR = "224.0.0.1"
MULTICAST_PORT = 16520

# Suffixes appended by ArSprout to indicate measurement/control modes
_CCM_SUFFIX_RE = re.compile(r"\.(mC|cMC|MC)$")


def strip_ccm_suffix(ccm_type: str) -> str:
    """Remove .mC / .cMC / .MC suffix from CCM type string.

    >>> strip_ccm_suffix("InAirTemp.mC")
    'InAirTemp'
    >>> strip_ccm_suffix("WRainfallAmt.cMC")
    'WRainfallAmt'
    >>> strip_ccm_suffix("IrrircA")
    'IrrircA'
    """
    return _CCM_SUFFIX_RE.sub("", ccm_type)


@dataclass
class CcmPacket:
    """Parsed UECS-CCM data packet."""

    ccm_type: str  # Suffix-stripped type (e.g., "InAirTemp")
    raw_type: str  # Original type including suffix (e.g., "InAirTemp.mC")
    value: float | str  # Numeric value or raw string
    room: int = 1
    region: int = 1
    order: int = 1
    priority: int = 29
    level: str = "S"  # S=sensor, A=actuator
    cast: str = "uni"
    source_ip: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def parse_ccm_xml(xml_bytes: bytes, source_ip: str = "") -> list[CcmPacket]:
    """Parse UECS XML payload into CcmPacket list.

    Args:
        xml_bytes: Raw UDP payload (UTF-8 XML).
        source_ip: IP address of the sender.

    Returns:
        List of parsed CcmPacket objects. Empty list on parse failure.
    """
    packets: list[CcmPacket] = []
    try:
        text = xml_bytes.decode("utf-8", errors="ignore")
        root = ET.fromstring(text)
    except (ET.ParseError, UnicodeDecodeError):
        return packets

    now = datetime.now(timezone.utc)

    for data_elem in root.findall("DATA"):
        raw_type = data_elem.get("type", "")
        raw_value = (data_elem.text or "").strip()

        # Try numeric conversion
        try:
            value: float | str = float(raw_value)
        except ValueError:
            value = raw_value

        def _int(attr: str, default: int) -> int:
            try:
                return int(data_elem.get(attr, default))
            except (ValueError, TypeError):
                return default

        packets.append(
            CcmPacket(
                ccm_type=strip_ccm_suffix(raw_type),
                raw_type=raw_type,
                value=value,
                room=_int("room", 1),
                region=_int("region", 1),
                order=_int("order", 1),
                priority=_int("priority", 29),
                level=data_elem.get("lv", "S"),
                cast=data_elem.get("cast", "uni"),
                source_ip=source_ip,
                timestamp=now,
            )
        )

    return packets


def build_ccm_xml(
    ccm_type: str,
    value: float | int | str,
    *,
    room: int = 1,
    region: int = 1,
    order: int = 1,
    priority: int = 10,
    level: str = "A",
    cast: str = "uni",
    local_ip: Optional[str] = None,
) -> bytes:
    """Build a UECS-CCM control XML packet.

    Args:
        ccm_type: CCM type string (e.g., "IrrircA").
        value: Control value (typically 0 or 1).
        room: House/room number.
        region: Region number.
        order: Order number.
        priority: Priority (1=highest, 30=lowest). Default 10 for normal control.
        level: Level string ("A" for actuator control).
        cast: Cast direction ("uni" or "bi").
        local_ip: Sender IP. Auto-detected if None.

    Returns:
        UTF-8 encoded XML bytes.
    """
    if local_ip is None:
        local_ip = _detect_local_ip()

    xml = (
        f'<?xml version="1.0"?>\n'
        f'<UECS ver="1.00-E10">\n'
        f'  <DATA type="{ccm_type}" room="{room}" region="{region}" '
        f'order="{order}" priority="{priority}" '
        f'lv="{level}" cast="{cast}">{value}</DATA>\n'
        f"  <IP>{local_ip}</IP>\n"
        f"</UECS>\n"
    )
    return xml.encode("utf-8")


def _detect_local_ip() -> str:
    """Detect local IP address by connecting to multicast target."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((MULTICAST_ADDR, MULTICAST_PORT))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"
