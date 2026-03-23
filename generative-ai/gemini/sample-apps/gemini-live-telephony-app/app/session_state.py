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
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CallSession:
    """Per-call state stored by channel_id."""

    channel_id: str
    caller_number: str
    dialed_did: str
    bridge_id: Optional[str] = None
    external_media_channel_id: Optional[str] = None

    # RTP Configuration
    rtp_local_port: Optional[int] = None
    rtp_remote_ip_port: Optional[tuple] = None  # (ip, port)

    # Gemini Live Session
    gemini_ws: Optional[Any] = None
    gemini_session_handle: Optional[str] = None

    # State Flags
    is_playing: bool = False
    stop_playback_event: asyncio.Event = field(default_factory=asyncio.Event)

    # Tool Processing
    tool_processing_flag: bool = False
    audio_gate_buffer: bytearray = field(default_factory=bytearray)

    # Tenant/Company (optional)
    tenant_id: Optional[str] = None

    # Metrics
    call_start_time: Optional[float] = field(default_factory=time.time)
    transcript: list = field(default_factory=list)

    async def wait_stop_playback(self) -> None:
        """Wait for stop playback signal."""
        self.stop_playback_event.clear()
        await self.stop_playback_event.wait()

    def signal_stop_playback(self) -> None:
        """Signal to stop playback."""
        self.stop_playback_event.set()

    async def cleanup(self) -> None:
        """Cleanup session resources."""
        try:
            if self.gemini_ws:
                await self.gemini_ws.close()
                logger.info(f"Closed Gemini WS for channel {self.channel_id}")
            self.gemini_ws = None
            self.bridge_id = None
            self.external_media_channel_id = None
        except Exception as e:
            logger.error(f"Error during cleanup for {self.channel_id}: {e}")


class SessionStateManager:
    """In-memory session state manager keyed by channel_id."""

    def __init__(self):
        self._sessions: Dict[str, CallSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        channel_id: str,
        caller_number: str,
        dialed_did: str,
        tenant_id: Optional[str] = None,
    ) -> CallSession:
        """Create a new call session."""
        async with self._lock:
            if channel_id in self._sessions:
                raise ValueError(f"Session {channel_id} already exists")

            session = CallSession(
                channel_id=channel_id,
                caller_number=caller_number,
                dialed_did=dialed_did,
                tenant_id=tenant_id,
            )
            self._sessions[channel_id] = session
            logger.info(f"Created session for channel {channel_id}")
            return session

    async def get_session(self, channel_id: str) -> Optional[CallSession]:
        """Retrieve session by channel_id."""
        async with self._lock:
            return self._sessions.get(channel_id)

    async def delete_session(self, channel_id: str) -> None:
        """Delete and cleanup session."""
        async with self._lock:
            if channel_id in self._sessions:
                session = self._sessions.pop(channel_id)
                await session.cleanup()
                logger.info(f"Deleted session for channel {channel_id}")

    async def list_sessions(self) -> list:
        """List all active sessions."""
        async with self._lock:
            return list(self._sessions.values())

    async def update_session(self, channel_id: str, **kwargs) -> Optional[CallSession]:
        """Update session fields."""
        async with self._lock:
            session = self._sessions.get(channel_id)
            if session:
                for key, value in kwargs.items():
                    if hasattr(session, key):
                        setattr(session, key, value)
            return session


# Global session manager instance
_session_manager: Optional[SessionStateManager] = None


def get_session_manager() -> SessionStateManager:
    """Get or create the global session manager."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionStateManager()
    return _session_manager
