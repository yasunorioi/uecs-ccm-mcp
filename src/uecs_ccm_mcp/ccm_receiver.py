"""Async UECS-CCM UDP multicast receiver.

Listens on 224.0.0.1:16520 and updates the SensorCache with parsed packets.
Designed to run as an asyncio background task within FastMCP's lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct

from .cache import SensorCache
from .ccm_protocol import MULTICAST_ADDR, MULTICAST_PORT, parse_ccm_xml

logger = logging.getLogger(__name__)


def _create_udp_socket() -> socket.socket:
    """Create and configure a UDP multicast receiver socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MULTICAST_PORT))
    mreq = struct.pack(
        "4sL", socket.inet_aton(MULTICAST_ADDR), socket.INADDR_ANY
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    return sock


class CcmReceiver:
    """Async UDP receiver that updates a SensorCache."""

    def __init__(self, cache: SensorCache) -> None:
        self.cache = cache
        self._sock: socket.socket | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the receiver loop as a background task."""
        self._sock = _create_udp_socket()
        self._task = asyncio.create_task(self._receive_loop())
        logger.info(
            "CCM receiver started on %s:%d", MULTICAST_ADDR, MULTICAST_PORT
        )

    async def stop(self) -> None:
        """Stop the receiver loop and close the socket."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._sock:
            self._sock.close()
        logger.info("CCM receiver stopped")

    async def _receive_loop(self) -> None:
        """Main receive loop using asyncio sock_recvfrom."""
        loop = asyncio.get_running_loop()
        assert self._sock is not None

        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._sock, 4096)
                source_ip = addr[0]
                packets = parse_ccm_xml(data, source_ip)
                for pkt in packets:
                    self.cache.update(pkt)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in CCM receive loop")
                await asyncio.sleep(1.0)
