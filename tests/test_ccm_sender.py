"""Tests for CcmSender safety guardrails."""

import time

import pytest

from uecs_ccm_mcp.ccm_sender import CcmSender, SafetyLimits


class TestSafetyLimits:
    def test_default_allowed_actuators(self):
        limits = SafetyLimits()
        assert "Irri" in limits.allowed_actuators
        assert "VenFan" in limits.allowed_actuators
        assert "VenRfWin" in limits.allowed_actuators
        assert "ThCrtn" in limits.allowed_actuators

    def test_default_min_interval(self):
        limits = SafetyLimits()
        assert limits.min_send_interval_seconds == 1.0

    def test_default_max_irrigation(self):
        limits = SafetyLimits()
        assert limits.max_irrigation_duration_seconds == 3600


class TestCcmSender:
    def test_send_allowed_actuator(self):
        sender = CcmSender()
        msg = sender.send("Irri", 1)
        assert "Irri" in msg
        assert "ON" in msg

    def test_send_disallowed_actuator(self):
        sender = CcmSender()
        with pytest.raises(ValueError, match="not in allowed list"):
            sender.send("UnknownActuator", 1)

    def test_rate_limiting(self):
        sender = CcmSender(SafetyLimits(min_send_interval_seconds=10.0))
        sender.send("Irri", 1)
        with pytest.raises(ValueError, match="Rate limited"):
            sender.send("Irri", 0)

    def test_rate_limiting_passes_after_interval(self):
        sender = CcmSender(SafetyLimits(min_send_interval_seconds=0.0))
        msg1 = sender.send("Irri", 1)
        msg2 = sender.send("Irri", 0)
        assert "ON" in msg1
        assert "OFF" in msg2

    @pytest.mark.asyncio
    async def test_irrigation_duration_limit(self):
        sender = CcmSender(
            SafetyLimits(max_irrigation_duration_seconds=100)
        )
        with pytest.raises(ValueError, match="exceeds max"):
            await sender.send_with_duration("Irri", 1, 200)

    @pytest.mark.asyncio
    async def test_send_with_duration_ok(self):
        sender = CcmSender(SafetyLimits(min_send_interval_seconds=0.0))
        msg = await sender.send_with_duration("VenFan", 1, 60)
        assert "VenFan" in msg
        assert "auto-OFF in 60s" in msg
        # Clean up timer
        for task in sender._off_timers.values():
            task.cancel()

    def test_custom_allowed_list(self):
        limits = SafetyLimits(allowed_actuators={"CustomA"})
        sender = CcmSender(limits)
        msg = sender.send("CustomA", 1)
        assert "CustomA" in msg

        with pytest.raises(ValueError):
            sender.send("Irri", 1)
