"""Tests for ccm_protocol module.

Test data based on 2026-02-18 field capture from ArSprout sensor nodes.
"""

import pytest
from uecs_ccm_mcp.ccm_protocol import (
    CcmPacket,
    build_ccm_xml,
    parse_ccm_xml,
    strip_ccm_suffix,
)


# --- strip_ccm_suffix ---


class TestStripCcmSuffix:
    def test_strip_mC(self):
        assert strip_ccm_suffix("InAirTemp.mC") == "InAirTemp"

    def test_strip_cMC(self):
        assert strip_ccm_suffix("WRainfallAmt.cMC") == "WRainfallAmt"

    def test_strip_MC(self):
        assert strip_ccm_suffix("SomeType.MC") == "SomeType"

    def test_no_suffix(self):
        assert strip_ccm_suffix("IrrircA") == "IrrircA"

    def test_dot_in_middle(self):
        assert strip_ccm_suffix("cnd.cMC") == "cnd"

    def test_empty_string(self):
        assert strip_ccm_suffix("") == ""

    def test_only_suffix(self):
        assert strip_ccm_suffix(".mC") == ""


# --- parse_ccm_xml ---


class TestParseCcmXml:
    """Test XML parsing with real ArSprout field data."""

    # Real sensor data packet (2026-02-18 capture: InAirTemp from 192.168.1.70)
    SENSOR_XML = (
        b'<UECS ver="1.00-E10">'
        b'<DATA type="InAirTemp.mC" room="1" region="1" order="1" '
        b'priority="29" lv="S" cast="uni">1.8</DATA>'
        b"</UECS>"
    )

    # Multi-DATA packet (synthetic, based on real data patterns)
    MULTI_XML = (
        b'<UECS ver="1.00-E10">'
        b'<DATA type="InAirTemp.mC" room="1" region="1" order="1" '
        b'priority="29" lv="S" cast="uni">1.8</DATA>'
        b'<DATA type="InAirHumid.mC" room="1" region="1" order="1" '
        b'priority="29" lv="S" cast="uni">75.0</DATA>'
        b"</UECS>"
    )

    # Weather station data (2026-02-18 capture pattern)
    WEATHER_XML = (
        b'<UECS ver="1.00-E10">'
        b'<DATA type="WAirTemp.mC" room="1" region="41" order="1" '
        b'priority="29" lv="S" cast="uni">-9.2</DATA>'
        b"</UECS>"
    )

    # Test node data (殿自作テストノード 192.168.1.74)
    TEST_NODE_XML = (
        b'<UECS ver="1.00-E10">'
        b'<DATA type="testFLOW.cMC" room="1" region="1" order="1" '
        b'priority="29" lv="S" cast="uni">42</DATA>'
        b"</UECS>"
    )

    # Control packet (what we send)
    CONTROL_XML = (
        b'<?xml version="1.0"?>\n'
        b'<UECS ver="1.00-E10">\n'
        b'  <DATA type="IrrircA" room="1" region="1" order="1" '
        b'priority="10" lv="A" cast="uni">1</DATA>\n'
        b"  <IP>192.168.1.100</IP>\n"
        b"</UECS>\n"
    )

    def test_parse_sensor_data(self):
        packets = parse_ccm_xml(self.SENSOR_XML, "192.168.1.70")
        assert len(packets) == 1
        pkt = packets[0]
        assert pkt.ccm_type == "InAirTemp"
        assert pkt.raw_type == "InAirTemp.mC"
        assert pkt.value == 1.8
        assert pkt.room == 1
        assert pkt.region == 1
        assert pkt.priority == 29
        assert pkt.level == "S"
        assert pkt.cast == "uni"
        assert pkt.source_ip == "192.168.1.70"

    def test_parse_multi_data(self):
        packets = parse_ccm_xml(self.MULTI_XML, "192.168.1.70")
        assert len(packets) == 2
        assert packets[0].ccm_type == "InAirTemp"
        assert packets[0].value == 1.8
        assert packets[1].ccm_type == "InAirHumid"
        assert packets[1].value == 75.0

    def test_parse_negative_temperature(self):
        packets = parse_ccm_xml(self.WEATHER_XML, "192.168.1.71")
        assert len(packets) == 1
        assert packets[0].value == -9.2
        assert packets[0].region == 41

    def test_parse_test_node(self):
        packets = parse_ccm_xml(self.TEST_NODE_XML, "192.168.1.74")
        assert len(packets) == 1
        assert packets[0].ccm_type == "testFLOW"
        assert packets[0].raw_type == "testFLOW.cMC"
        assert packets[0].value == 42.0

    def test_parse_control_packet(self):
        packets = parse_ccm_xml(self.CONTROL_XML, "192.168.1.100")
        assert len(packets) == 1
        assert packets[0].ccm_type == "IrrircA"
        assert packets[0].value == 1.0
        assert packets[0].priority == 10
        assert packets[0].level == "A"

    def test_parse_invalid_xml(self):
        assert parse_ccm_xml(b"not xml at all", "") == []

    def test_parse_empty_bytes(self):
        assert parse_ccm_xml(b"", "") == []

    def test_parse_no_data_elements(self):
        xml = b'<UECS ver="1.00-E10"></UECS>'
        assert parse_ccm_xml(xml, "") == []

    def test_parse_non_numeric_value(self):
        xml = (
            b'<UECS ver="1.00-E10">'
            b'<DATA type="StatusMsg" room="1" region="1" order="1" '
            b'priority="29" lv="S" cast="uni">OK</DATA>'
            b"</UECS>"
        )
        packets = parse_ccm_xml(xml, "")
        assert len(packets) == 1
        assert packets[0].value == "OK"

    def test_timestamp_is_set(self):
        packets = parse_ccm_xml(self.SENSOR_XML, "")
        assert packets[0].timestamp is not None


# --- build_ccm_xml ---


class TestBuildCcmXml:
    def test_build_irrigation_on(self):
        xml_bytes = build_ccm_xml("IrrircA", 1, local_ip="192.168.1.100")
        text = xml_bytes.decode("utf-8")
        assert 'type="IrrircA"' in text
        assert ">1<" in text
        assert "<IP>192.168.1.100</IP>" in text
        assert 'priority="10"' in text
        assert 'lv="A"' in text

    def test_build_roundtrip(self):
        """Build a packet, then parse it back."""
        xml_bytes = build_ccm_xml(
            "VenFanrcA", 1,
            room=2, region=3, order=1, priority=5,
            local_ip="10.0.0.1",
        )
        packets = parse_ccm_xml(xml_bytes, "10.0.0.1")
        assert len(packets) == 1
        pkt = packets[0]
        assert pkt.ccm_type == "VenFanrcA"
        assert pkt.value == 1.0
        assert pkt.room == 2
        assert pkt.region == 3
        assert pkt.priority == 5
        assert pkt.level == "A"

    def test_build_custom_priority(self):
        xml_bytes = build_ccm_xml("IrrircA", 0, priority=1, local_ip="1.2.3.4")
        text = xml_bytes.decode("utf-8")
        assert 'priority="1"' in text

    def test_build_includes_xml_declaration(self):
        xml_bytes = build_ccm_xml("IrrircA", 1, local_ip="1.2.3.4")
        text = xml_bytes.decode("utf-8")
        assert text.startswith('<?xml version="1.0"?>')

    def test_build_includes_uecs_version(self):
        xml_bytes = build_ccm_xml("IrrircA", 1, local_ip="1.2.3.4")
        text = xml_bytes.decode("utf-8")
        assert 'ver="1.00-E10"' in text
