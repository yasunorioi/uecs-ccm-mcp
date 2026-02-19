#!/usr/bin/env python3
"""Standalone UECS-CCM send test.

No external dependencies - uses only Python standard library.
Designed to be scp'd to RPi and run directly.

Usage:
    python3 ccm_send_test.py Irri 1             # Irrigation ON
    python3 ccm_send_test.py Irri 0             # Irrigation OFF
    python3 ccm_send_test.py VenFan 1           # Ventilation fan ON
    python3 ccm_send_test.py VenRfWin 50        # Roof window 50%
    python3 ccm_send_test.py --priority 1 Irri 0  # Emergency stop

SAFETY:
    - Only sends to allowed actuator types
    - Default priority=10 (normal control)
    - Confirm before sending
"""

import argparse
import socket
import sys

MULTICAST_ADDR = "224.0.0.1"
MULTICAST_PORT = 16520

# Allowed actuator CCM types for safety
# Source: ArSprout DIY kit config XMLs
ALLOWED_ACTUATORS = {
    # ON/OFF switch actuators
    "Irri": "Irrigation valve (灌水)",
    "VenFan": "Ventilation fan (換気扇)",
    "CirHoriFan": "Circulation fan (攪拌扇)",
    "AirHeatBurn": "Burner heater (暖房バーナー)",
    "AirHeatHP": "Heat pump (暖房HP)",
    "CO2Burn": "CO2 generator (CO2発生器)",
    # Position-controlled actuators (0-100%)
    "VenRfWin": "Roof window (天窓)",
    "VenSdWin": "Side window (側窓)",
    "ThCrtn": "Thermal curtain (保温カーテン)",
    "LsCrtn": "Light-shading curtain (遮光カーテン)",
    "AirCoolHP": "Cooling heat pump (冷房HP)",
    "AirHumFog": "Humidifying fog (加湿フォグ)",
}


def detect_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((MULTICAST_ADDR, MULTICAST_PORT))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


def build_xml(ccm_type: str, value: int, priority: int, local_ip: str) -> bytes:
    xml = (
        f'<?xml version="1.0"?>\n'
        f'<UECS ver="1.00-E10">\n'
        f'  <DATA type="{ccm_type}" room="1" region="1" '
        f'order="1" priority="{priority}" '
        f'lv="A" cast="uni">{value}</DATA>\n'
        f"  <IP>{local_ip}</IP>\n"
        f"</UECS>\n"
    )
    return xml.encode("utf-8")


def send_packet(xml_bytes: bytes) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.sendto(xml_bytes, (MULTICAST_ADDR, MULTICAST_PORT))
    sock.close()


def main():
    parser = argparse.ArgumentParser(
        description="UECS-CCM send test",
        epilog="Allowed actuators: " + ", ".join(ALLOWED_ACTUATORS.keys()),
    )
    parser.add_argument("ccm_type", help="CCM actuator type (e.g., Irri, VenRfWin)")
    parser.add_argument("value", type=int, help="0=OFF, 1=ON (switch) or 0-100 (position %%)")
    parser.add_argument(
        "--priority", "-p", type=int, default=10,
        help="Priority (1=emergency, 10=normal, 30=low). Default: 10"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt"
    )
    parser.add_argument(
        "--repeat", "-r", type=int, default=1,
        help="Number of times to send (for reliability). Default: 1"
    )
    args = parser.parse_args()

    if args.ccm_type not in ALLOWED_ACTUATORS:
        print(f"ERROR: '{args.ccm_type}' is not an allowed actuator type.")
        print(f"Allowed: {', '.join(ALLOWED_ACTUATORS.keys())}")
        sys.exit(1)

    local_ip = detect_local_ip()
    state_str = "ON" if args.value == 1 else "OFF"
    desc = ALLOWED_ACTUATORS[args.ccm_type]

    print(f"[CCM Send] Target:   {args.ccm_type} ({desc})")
    print(f"[CCM Send] State:    {state_str} (value={args.value})")
    print(f"[CCM Send] Priority: {args.priority}")
    print(f"[CCM Send] From IP:  {local_ip}")
    print(f"[CCM Send] Dest:     {MULTICAST_ADDR}:{MULTICAST_PORT}")

    if not args.force:
        confirm = input("\nSend this packet? [y/N] ").strip().lower()
        if confirm != "y":
            print("[CCM Send] Cancelled.")
            sys.exit(0)

    xml_bytes = build_xml(args.ccm_type, args.value, args.priority, local_ip)

    print(f"\n[CCM Send] XML payload:")
    print(xml_bytes.decode("utf-8"))

    for i in range(args.repeat):
        send_packet(xml_bytes)
        if args.repeat > 1:
            print(f"[CCM Send] Sent {i + 1}/{args.repeat}")

    print(f"[CCM Send] Done. Sent {args.repeat} packet(s).")


if __name__ == "__main__":
    main()
