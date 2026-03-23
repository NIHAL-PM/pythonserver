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
import json
import logging
from typing import Optional

import aiohttp

from app.config import Config

logger = logging.getLogger(__name__)


class AsteriskARIClient:
    """Asterisk ARI HTTP client for call control."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session: Optional[aiohttp.ClientSession] = None
        self.auth = aiohttp.BasicAuth(username, password)

    async def connect(self) -> None:
        """Create HTTP session."""
        self.session = aiohttp.ClientSession(auth=self.auth)
        logger.info(f"ARI client connected to {self.base_url}")

    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            logger.info("ARI client disconnected")

    async def _request(self, method: str, endpoint: str, **kwargs):
        """Make HTTP request to ARI."""
        if not self.session:
            raise RuntimeError("ARI session not connected")

        url = f"{self.base_url}/ari{endpoint}"
        try:
            async with self.session.request(method, url, **kwargs) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    logger.error(f"ARI error {resp.status}: {error_text}")
                    return None
                if resp.status == 204:
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"ARI request failed: {e}")
            return None

    async def subscribe_stasis_events(
        self, app_name: str, callback
    ) -> None:
        """Subscribe to Stasis app events (WebSocket)."""
        ws_url = f"{self.base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ari/events"
        try:
            async with self.session.ws_connect(
                f"{ws_url}?app={app_name}", auth=self.auth
            ) as ws:
                logger.info(f"Subscribed to Stasis app: {app_name}")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(msg.data)
                        await callback(event)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"WS error: {ws.exception()}")
                        break
        except Exception as e:
            logger.error(f"Error subscribing to Stasis events: {e}")

    async def create_bridge(self, bridge_id: str, bridge_type: str = "mixing") -> dict:
        """Create a bridge."""
        return await self._request(
            "POST",
            "/bridges",
            json={"bridgeId": bridge_id, "type": bridge_type},
        )

    async def delete_bridge(self, bridge_id: str) -> None:
        """Delete a bridge."""
        await self._request("DELETE", f"/bridges/{bridge_id}")

    async def add_channel_to_bridge(self, bridge_id: str, channel_id: str) -> dict:
        """Add channel to bridge."""
        return await self._request(
            "POST",
            f"/bridges/{bridge_id}/addChannel",
            json={"channel": channel_id},
        )

    async def remove_channel_from_bridge(self, bridge_id: str, channel_id: str) -> None:
        """Remove channel from bridge."""
        await self._request(
            "POST",
            f"/bridges/{bridge_id}/removeChannel",
            json={"channel": channel_id},
        )

    async def create_external_media(
        self,
        channel_id: str,
        external_host: str,
        external_port: int,
        format: str = "ulaw",
    ) -> dict:
        """Create external media channel for RTP."""
        return await self._request(
            "POST",
            f"/channels/externalMedia",
            json={
                "channelId": channel_id,
                "externalHostIp": external_host,
                "externalHostPort": external_port,
                "encapsulation": "rtp",
                "transport": "udp",
                "format": format,
                "direction": "both",
            },
        )

    async def channel_answer(self, channel_id: str) -> dict:
        """Answer a channel."""
        return await self._request("POST", f"/channels/{channel_id}/answer")

    async def channel_hangup(self, channel_id: str) -> None:
        """Hangup a channel."""
        await self._request("DELETE", f"/channels/{channel_id}")

    async def get_channel(self, channel_id: str) -> dict:
        """Get channel details."""
        return await self._request("GET", f"/channels/{channel_id}")

    async def send_dtmf(self, channel_id: str, dtmf: str) -> dict:
        """Send DTMF to channel."""
        return await self._request(
            "POST",
            f"/channels/{channel_id}/dtmf",
            json={"dtmf": dtmf},
        )


class StasisEventHandler:
    """Handles Asterisk Stasis app events."""

    def __init__(self, ari_client: AsteriskARIClient, session_manager):
        self.ari_client = ari_client
        self.session_manager = session_manager

    async def handle_stasis_start(self, event: dict) -> None:
        """Handle StasisStart event (call enters app)."""
        try:
            channel = event.get("channel", {})
            channel_id = channel.get("id")
            caller = channel.get("caller", {}).get("number", "unknown")
            dialed_did = channel.get("dialplan", {}).get("exten", "unknown")

            logger.info(f"StasisStart: {channel_id} from {caller} to {dialed_did}")

            # Create session
            session = await self.session_manager.create_session(
                channel_id, caller, dialed_did
            )

            # Answer the channel
            await self.ari_client.channel_answer(channel_id)

            # Create bridge
            bridge_id = f"bridge-{channel_id}"
            await self.ari_client.create_bridge(bridge_id)
            session.bridge_id = bridge_id

            # Add SIP channel to bridge
            await self.ari_client.add_channel_to_bridge(bridge_id, channel_id)

            logger.info(f"Call {channel_id} answered and bridged")
        except Exception as e:
            logger.error(f"Error handling StasisStart: {e}")

    async def handle_stasis_end(self, event: dict) -> None:
        """Handle StasisEnd event (call leaves app)."""
        try:
            channel_id = event.get("channel", {}).get("id")
            if channel_id:
                logger.info(f"StasisEnd: {channel_id}")
                await self.session_manager.delete_session(channel_id)
        except Exception as e:
            logger.error(f"Error handling StasisEnd: {e}")

    async def handle_channel_hangup(self, event: dict) -> None:
        """Handle ChannelHangupRequest event."""
        try:
            channel_id = event.get("channel", {}).get("id")
            if channel_id:
                logger.info(f"ChannelHangupRequest: {channel_id}")
                await self.session_manager.delete_session(channel_id)
        except Exception as e:
            logger.error(f"Error handling ChannelHangupRequest: {e}")

    async def handle_event(self, event: dict) -> None:
        """Route event to handler."""
        event_type = event.get("type")

        if event_type == "StasisStart":
            await self.handle_stasis_start(event)
        elif event_type == "StasisEnd":
            await self.handle_stasis_end(event)
        elif event_type == "ChannelHangupRequest":
            await self.handle_channel_hangup(event)
