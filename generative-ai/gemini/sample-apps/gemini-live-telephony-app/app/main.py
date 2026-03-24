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

"""
BSNL + Asterisk + Gemini Live Python Service

Main entry point for Asterisk ARI integration.
Replaces Twilio webhook model with Asterisk Stasis + RTP architecture.
"""

import asyncio
import logging
import signal
import sys

import uvicorn
from fastapi import FastAPI
from google import genai

from app.asterisk_ari import AsteriskARIClient, StasisEventHandler
from app.audio_transcoding import (
    AudioTranscoder,
    gemini_outbound_to_rtp,
    rtp_inbound_to_gemini,
)
from app.config import Config
from app.gemini_live import run_gemini_session
from app.rtp_io import RTPManager
from app.session_state import get_session_manager

# Configuration
logging.basicConfig(
    level=Config.SERVICE_LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("google.auth").setLevel(logging.WARNING)

# FastAPI app (for health checks and admin endpoints)
app = FastAPI(title="Asterisk + Gemini Live")

# Global state
ari_client: AsteriskARIClient = None
gemini_client: genai.Client = None
session_manager = get_session_manager()
event_handler: StasisEventHandler = None
call_workers: dict = {}  # channel_id -> asyncio.Task


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    sessions = await session_manager.list_sessions()
    return {
        "status": "healthy",
        "active_calls": len(sessions),
        "ari_connected": ari_client is not None and ari_client.session is not None,
    }


@app.get("/metrics")
async def metrics():
    """Metrics endpoint."""
    sessions = await session_manager.list_sessions()
    return {
        "active_calls": len(sessions),
        "sessions": [
            {
                "channel_id": s.channel_id,
                "caller": s.caller_number,
                "dialed_did": s.dialed_did,
                "call_duration_seconds": asyncio.get_event_loop().time() - s.call_start_time if s.call_start_time else 0,
            }
            for s in sessions
        ],
    }


async def handle_call_worker(channel_id: str, session) -> None:
    """
    Worker task for a single call.

    Orchestrates:
    1. RTP I/O
    2. Audio transcoding
    3. Gemini Live session
    """
    logger.info(f"Starting call worker for {channel_id}")

    try:
        # Initialize RTP
        rtp_manager = RTPManager(channel_id)
        rtp_port = await rtp_manager.initialize()
        logger.info(f"RTP port allocated: {rtp_port} for {channel_id}")

        # Create external media channel in Asterisk
        ext_media_result = await ari_client.create_external_media(
            channel_id=f"external-{channel_id}",
            external_host="127.0.0.1",
            external_port=rtp_port,
            format="ulaw",
        )
        if ext_media_result:
            session.external_media_channel_id = ext_media_result.get("id")
            session.rtp_local_port = rtp_port
            logger.info(f"External media created for {channel_id}")

        # Audio subsystem
        transcoder = AudioTranscoder()
        in_q = asyncio.Queue()  # RTP -> Gemini
        out_q = asyncio.Queue()  # Gemini -> RTP

        # Launch concurrent tasks
        tasks = [
            asyncio.create_task(
                rtp_inbound_to_gemini(rtp_manager, in_q, transcoder, session)
            ),
            asyncio.create_task(
                gemini_outbound_to_rtp(rtp_manager, out_q, transcoder, session)
            ),
            asyncio.create_task(
                run_gemini_session(
                    gemini_client,
                    Config.GEMINI_MODEL,
                    in_q,
                    out_q,
                    session,
                    Config.SYSTEM_INSTRUCTION,
                )
            ),
        ]

        # Wait for any task to complete (which signals end of call)
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Cleanup RTP
        await rtp_manager.close()
        logger.info(f"Call worker completed for {channel_id}")

    except Exception as e:
        logger.error(f"Error in call worker for {channel_id}: {e}")

    finally:
        # Remove from active workers
        if channel_id in call_workers:
            del call_workers[channel_id]
        # Session cleanup is handled by ARI event handler
        logger.info(f"Call worker cleanup completed for {channel_id}")


async def stasis_event_wrapper(event: dict) -> None:
    """Wrapper for Stasis event handling with call worker spawning."""
    await event_handler.handle_event(event)

    # Spawn call worker for StasisStart
    if event.get("type") == "StasisStart":
        channel_id = event.get("channel", {}).get("id")
        if channel_id and channel_id not in call_workers:
            session = await session_manager.get_session(channel_id)
            if session:
                task = asyncio.create_task(handle_call_worker(channel_id, session))
                call_workers[channel_id] = task
                logger.info(f"Spawned call worker for {channel_id}")


async def start_ari_listener() -> None:
    """Start listening to Asterisk ARI events."""
    logger.info(f"Starting ARI listener for app: {Config.ARI_APP_NAME}")
    await ari_client.subscribe_stasis_events(Config.ARI_APP_NAME, stasis_event_wrapper)


def signal_handler(sig, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {sig}, shutting down...")
    sys.exit(0)


async def startup():
    """Initialize service on startup."""
    global ari_client, gemini_client, event_handler

    try:
        # Validate configuration
        Config.validate()

        # Initialize Gemini client
        if Config.GOOGLE_API_KEY:
            # Use API key directly
            gemini_client = genai.Client(api_key=Config.GOOGLE_API_KEY)
            logger.info("Gemini client initialized with API key")
        else:
            # Use Vertex AI with Application Default Credentials
            gemini_client = genai.Client(
                vertexai=True,
                project=Config.GOOGLE_CLOUD_PROJECT,
                location=Config.GOOGLE_CLOUD_LOCATION,
            )
            logger.info("Gemini client initialized with Vertex AI")

        # Initialize ARI client
        ari_client = AsteriskARIClient(
            Config.ARI_BASE_URL, Config.ARI_USERNAME, Config.ARI_PASSWORD, Config.ARI_APP_NAME
        )
        await ari_client.connect()
        logger.info("ARI client connected")

        # Initialize event handler
        event_handler = StasisEventHandler(ari_client, session_manager)

        # Start ARI listener in background
        asyncio.create_task(start_ari_listener())
        logger.info("ARI listener started")

    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise


async def shutdown():
    """Cleanup on shutdown."""
    logger.info("Shutting down...")

    # Cancel all call workers
    # Create a list copy to avoid "dictionary changed size during iteration" error
    tasks_to_cancel = list(call_workers.values())
    for task in tasks_to_cancel:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Disconnect ARI
    if ari_client:
        await ari_client.disconnect()

    logger.info("Shutdown complete")


@app.on_event("startup")
async def on_startup():
    await startup()


@app.on_event("shutdown")
async def on_shutdown():
    await shutdown()


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run FastAPI server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=Config.SERVICE_LOG_LEVEL.lower(),
    )
