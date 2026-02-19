#!/usr/bin/env python3
"""Standalone UECS-CCM receive monitor.

No external dependencies - uses only Python standard library.
Designed to be scp'd to RPi and run directly.

Usage:
    python3 ccm_receive_test.py                  # Show all CCM packets
    python3 ccm_receive_test.py --filter InAirTemp  # Filter by type
    python3 ccm_receive_test.py --filter InAirTemp --count 10  # Stop after 10 packets
"""

import argparse
import re
import socket
import struct
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

MULTICAST_ADDR = "224.0.0.1"
MULTICAST_PORT = 16520
BUFFER_SIZE = 4096

_CCM_SUFFIX_RE = re.compile(r"\.(mC|cMC|MC)$")


def strip_suffix(ccm_type: str) -> str:
    return _CCM_SUFFIX_RE.sub("", ccm_type)


def create_receiver_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MULTICAST_PORT))
    mreq = struct.pack(
        "4sL", socket.inet_aton(MULTICAST_ADDR), socket.INADDR_ANY
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(5.0)
    return sock


def parse_packet(data: bytes, addr: tuple) -> list[dict]:
    results = []
    try:
        text = data.decode("utf-8", errors="ignore")
        root = ET.fromstring(text)
    except ET.ParseError:
        return results

    for elem in root.findall("DATA"):
        raw_type = elem.get("type", "")
        raw_value = (elem.text or "").strip()
        try:
            value = float(raw_value)
        except ValueError:
            value = raw_value

        results.append(
            {
                "type": strip_suffix(raw_type),
                "raw_type": raw_type,
                "value": value,
                "room": elem.get("room", ""),
                "region": elem.get("region", ""),
                "order": elem.get("order", ""),
                "priority": elem.get("priority", ""),
                "lv": elem.get("lv", ""),
                "cast": elem.get("cast", ""),
                "source_ip": addr[0],
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description="UECS-CCM receive monitor")
    parser.add_argument(
        "--filter", "-f", type=str, default=None,
        help="Filter by CCM type (substring match, e.g., InAirTemp)"
    )
    parser.add_argument(
        "--count", "-c", type=int, default=0,
        help="Stop after N matching packets (0=infinite)"
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Show raw XML payload"
    )
    args = parser.parse_args()

    sock = create_receiver_socket()
    print(f"[CCM Receiver] Listening on {MULTICAST_ADDR}:{MULTICAST_PORT}")
    if args.filter:
        print(f"[CCM Receiver] Filter: {args.filter}")
    print("-" * 80)

    received = 0
    try:
        while True:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue

            if args.raw:
                print(f"\n--- RAW from {addr[0]} ---")
                print(data.decode("utf-8", errors="replace"))
                print("---")

            packets = parse_packet(data, addr)
            for pkt in packets:
                if args.filter and args.filter.lower() not in pkt["type"].lower():
                    continue

                ts = datetime.now().strftime("%H:%M:%S")
                val = pkt["value"]
                if isinstance(val, float):
                    val_str = f"{val:>10.2f}"
                else:
                    val_str = f"{val:>10s}"

                print(
                    f"[{ts}] {pkt['source_ip']:>15s}  "
                    f"{pkt['type']:<20s} = {val_str}  "
                    f"(room={pkt['room']} region={pkt['region']} "
                    f"pri={pkt['priority']} lv={pkt['lv']})"
                )

                received += 1
                if args.count > 0 and received >= args.count:
                    print(f"\n[CCM Receiver] Received {received} packets. Done.")
                    return

    except KeyboardInterrupt:
        print(f"\n[CCM Receiver] Stopped. Total received: {received}")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
