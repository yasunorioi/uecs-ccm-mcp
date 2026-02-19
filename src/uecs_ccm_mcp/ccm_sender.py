"""Safe CCM actuator command sender with guardrails.

Sends UECS-CCM control packets via UDP multicast with safety limits:
- Allowlisted actuator types only
- Minimum send interval (rate limiting)
- Maximum irrigation duration
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field

from .ccm_protocol import MULTICAST_ADDR, MULTICAST_PORT, build_ccm_xml

logger = logging.getLogger(__name__)


@dataclass
class SafetyLimits:
    """Safety guardrails for actuator control."""

    allowed_actuators: set[str] = field(
        default_factory=lambda: {
            # ON/OFF switch actuators
            "Irri",          # Irrigation valve (灌水)
            "VenFan",        # Ventilation fan (換気扇)
            "CirHoriFan",    # Circulation fan (攪拌扇)
            "AirHeatBurn",   # Burner heater (暖房バーナー)
            "AirHeatHP",     # Heat pump (暖房ヒートポンプ)
            "CO2Burn",       # CO2 generator (CO2発生器)
            # Position-controlled actuators (0-100%)
            "VenRfWin",      # Roof window (天窓)
            "VenSdWin",      # Side window (側窓)
            "ThCrtn",        # Thermal curtain (保温カーテン)
            "LsCrtn",        # Light-shading curtain (遮光カーテン)
            "AirCoolHP",     # Cooling heat pump (冷房ヒートポンプ)
            "AirHumFog",     # Humidifying fog (加湿フォグ)
        }
    )
    min_send_interval_seconds: float = 1.0
    max_irrigation_duration_seconds: int = 3600


class CcmSender:
    """Send CCM control packets with safety guardrails."""

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        self.limits = limits or SafetyLimits()
        self._last_send_time: float = 0.0
        self._off_timers: dict[str, asyncio.Task] = {}

    def send(
        self,
        ccm_type: str,
        value: int | float,
        *,
        room: int = 1,
        region: int = 1,
        order: int = 1,
        priority: int = 10,
    ) -> str:
        """Send a control CCM packet.

        Args:
            ccm_type: Actuator CCM type (e.g., "IrrircA").
            value: Control value (typically 0 or 1).
            room: House/room number.
            region: Region number.
            order: Order number.
            priority: CCM priority (1=highest).

        Returns:
            Status message.

        Raises:
            ValueError: If actuator not allowed or rate-limited.
        """
        # Safety check: allowed actuator
        if ccm_type not in self.limits.allowed_actuators:
            raise ValueError(
                f"Actuator '{ccm_type}' not in allowed list: "
                f"{sorted(self.limits.allowed_actuators)}"
            )

        # Rate limiting
        now = time.monotonic()
        elapsed = now - self._last_send_time
        if elapsed < self.limits.min_send_interval_seconds:
            raise ValueError(
                f"Rate limited: {elapsed:.1f}s since last send, "
                f"minimum is {self.limits.min_send_interval_seconds}s"
            )

        xml_bytes = build_ccm_xml(
            ccm_type, value,
            room=room, region=region, order=order, priority=priority,
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.sendto(xml_bytes, (MULTICAST_ADDR, MULTICAST_PORT))
        sock.close()

        self._last_send_time = time.monotonic()

        state_str = "ON" if value else "OFF"
        msg = f"Sent {ccm_type}={state_str} (priority={priority}, room={room})"
        logger.info(msg)
        return msg

    async def send_with_duration(
        self,
        ccm_type: str,
        value: int | float,
        duration_seconds: int,
        *,
        room: int = 1,
        region: int = 1,
        order: int = 1,
        priority: int = 10,
    ) -> str:
        """Send ON command and schedule auto-OFF after duration.

        Args:
            duration_seconds: Auto-OFF timer in seconds.

        Returns:
            Status message.

        Raises:
            ValueError: If duration exceeds max for irrigation.
        """
        # Irrigation duration guard
        if "Irri" in ccm_type and duration_seconds > self.limits.max_irrigation_duration_seconds:
            raise ValueError(
                f"Irrigation duration {duration_seconds}s exceeds max "
                f"{self.limits.max_irrigation_duration_seconds}s"
            )

        # Cancel existing timer for this actuator
        timer_key = f"{room}:{ccm_type}"
        if timer_key in self._off_timers:
            self._off_timers[timer_key].cancel()

        # Send ON
        msg = self.send(
            ccm_type, value,
            room=room, region=region, order=order, priority=priority,
        )

        # Schedule OFF
        if value:
            self._off_timers[timer_key] = asyncio.create_task(
                self._auto_off(
                    ccm_type, duration_seconds,
                    room=room, region=region, order=order, priority=priority,
                )
            )
            msg += f" (auto-OFF in {duration_seconds}s)"

        return msg

    async def _auto_off(
        self,
        ccm_type: str,
        delay: int,
        **kwargs,
    ) -> None:
        """Auto-OFF timer coroutine."""
        try:
            await asyncio.sleep(delay)
            self.send(ccm_type, 0, **kwargs)
            logger.info("Auto-OFF: %s after %ds", ccm_type, delay)
        except asyncio.CancelledError:
            logger.info("Auto-OFF cancelled: %s", ccm_type)
