# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import socket
import struct
from typing import Optional, Tuple

from app.config import Config

logger = logging.getLogger(__name__)

# RTP Header constants
RTP_VERSION = 2
RTP_PAYLOAD_TYPE_PCMU = 0  # PCMU (μ-law)


class RTPPacket:
    """Simple RTP packet parser and builder."""

    def __init__(
        self,
        payload: bytes,
        sequence_num: int,
        timestamp: int,
        ssrc: int,
        marker: bool = False,
    ):
        self.version = RTP_VERSION
        self.padding = False
        self.extension = False
        self.cc = 0  # CSRC count
        self.marker = marker
        self.payload_type = RTP_PAYLOAD_TYPE_PCMU
        self.sequence_num = sequence_num
        self.timestamp = timestamp
        self.ssrc = ssrc
        self.payload = payload

    def to_bytes(self) -> bytes:
        """Serialize RTP packet to bytes."""
        # First byte: V(2) P(1) X(1) CC(4)
        byte0 = (self.version << 6) | (int(self.padding) << 5) | (int(self.extension) << 4) | self.cc

        # Second byte: M(1) PT(7)
        byte1 = (int(self.marker) << 7) | self.payload_type

        # Pack header: V/P/X/CC, M/PT, SEQ, TS, SSRC
        header = struct.pack(
            "!BBHII", byte0, byte1, self.sequence_num, self.timestamp, self.ssrc
        )

        return header + self.payload

    @staticmethod
    def from_bytes(data: bytes) -> Optional["RTPPacket"]:
        """Deserialize RTP packet from bytes."""
        if len(data) < 12:
            return None

        byte0, byte1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])

        version = (byte0 >> 6) & 0x03
        padding = (byte0 >> 5) & 0x01
        extension = (byte0 >> 4) & 0x01
        cc = byte0 & 0x0F

        if version != RTP_VERSION:
            logger.warning(f"Unexpected RTP version: {version}")
            return None

        marker = (byte1 >> 7) & 0x01
        pt = byte1 & 0x7F

        payload_start = 12 + (cc * 4)
        if extension:
            # Parse extension header
            if len(data) < payload_start + 4:
                return None
            ext_len = struct.unpack("!H", data[payload_start + 2 : payload_start + 4])[0]
            payload_start += 4 + (ext_len * 4)

        payload = data[payload_start:]

        packet = RTPPacket(payload, seq, ts, ssrc, bool(marker))
        packet.padding = bool(padding)
        return packet


class RTPUDPTransport:
    """Manages RTP UDP socket for Asterisk external media."""

    def __init__(self, local_ip: str, port: int):
        self.local_ip = local_ip
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.remote_addr: Optional[Tuple[str, int]] = None
        self.ssrc = 0x12345678  # Fixed SSRC for simplicity
        self.sequence_num = 0
        self.timestamp = 0

    async def bind(self) -> None:
        """Bind UDP socket."""
        loop = asyncio.get_event_loop()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.local_ip, self.port))
        self.socket.setblocking(False)
        logger.info(f"RTP socket bound to {self.local_ip}:{self.port}")

    async def receive_packet(self) -> Optional[Tuple[bytes, Tuple[str, int]]]:
        """Receive RTP packet from socket."""
        if not self.socket:
            return None

        loop = asyncio.get_event_loop()
        try:
            # Note: Windows loop.sock_recvfrom is often not implemented. 
            # On Linux it usually works, but if it fails with NotImplementedError, 
            # we fallback to run_in_executor with a standard recvfrom.
            try:
                data, addr = await loop.sock_recvfrom(self.socket, 4096)
            except NotImplementedError:
                # Fallback for systems where sock_recvfrom is not implemented
                data, addr = await loop.run_in_executor(None, self.socket.recvfrom, 4096)

            self.remote_addr = addr
            packet = RTPPacket.from_bytes(data)
            if packet:
                return packet.payload, addr
            return None
        except asyncio.TimeoutError:
            # Timeout is expected in non-blocking mode - just return None
            return None
        except (BlockingIOError, ResourceWarning, InterruptedError):
            # These are expected in non-blocking mode when no data is ready
            return None
        except OSError as e:
            if e.errno in (11, 35):  # EAGAIN (11) or EWOULDBLOCK (35)
                return None
            logger.error(f"OSError receiving RTP packet: {e} (errno={e.errno})")
            return None
        except Exception as e:
            logger.error(f"Error receiving RTP packet: {type(e).__name__}: {e}")
            return None

    async def send_packet(self, payload: bytes) -> None:
        """Send RTP packet."""
        if not self.socket or not self.remote_addr:
            return

        packet = RTPPacket(payload, self.sequence_num, self.timestamp, self.ssrc)
        self.sequence_num = (self.sequence_num + 1) % 65536

        # Increment timestamp based on payload size
        # For μ-law at 8kHz, 160 bytes = 20ms = 160 samples
        self.timestamp += len(payload)

        loop = asyncio.get_event_loop()
        try:
            if not self.socket:
                return
            await loop.sock_sendto(self.socket, packet.to_bytes(), self.remote_addr)
        except Exception as e:
            logger.error(f"Error sending RTP packet to {self.remote_addr}: {type(e).__name__}: {e}")
            if hasattr(e, 'errno'):
                logger.error(f"  Errno: {e.errno}")

    async def close(self) -> None:
        """Close socket."""
        if self.socket:
            self.socket.close()
            logger.info("RTP socket closed")


class RTPManager:
    """Manages RTP I/O for a call session."""

    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.transport: Optional[RTPUDPTransport] = None
        self.port_pool = list(
            range(Config.RTP_LOCAL_PORT_RANGE_START, Config.RTP_LOCAL_PORT_RANGE_END)
        )
        self._port_offset = 0

    async def initialize(self) -> int:
        """Initialize RTP transport and return allocated port."""
        port = self._allocate_port()
        self.transport = RTPUDPTransport(Config.RTP_LOCAL_IP, port)
        await self.transport.bind()
        return port

    def _allocate_port(self) -> int:
        """Allocate next available port."""
        port = self.port_pool[self._port_offset % len(self.port_pool)]
        self._port_offset += 1
        return port

    async def receive(self) -> Optional[Tuple[bytes, Tuple[str, int]]]:
        """Receive audio payload from RTP."""
        if self.transport:
            return await self.transport.receive_packet()
        return None

    async def send(self, payload: bytes) -> None:
        """Send audio payload via RTP."""
        if self.transport:
            await self.transport.send_packet(payload)

    async def close(self) -> None:
        """Close RTP transport."""
        if self.transport:
            await self.transport.close()
