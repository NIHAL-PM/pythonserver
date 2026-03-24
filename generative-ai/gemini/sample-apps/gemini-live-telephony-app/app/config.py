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

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Configuration for the Asterisk + Gemini Live service."""

    # Asterisk ARI Configuration
    ARI_BASE_URL: str = os.getenv(
        "ARI_BASE_URL", "http://localhost:8088"
    )  # ARI HTTP base URL
    ARI_USERNAME: str = os.getenv("ARI_USERNAME", "asterisk")
    ARI_PASSWORD: str = os.getenv("ARI_PASSWORD", "asterisk")
    ARI_APP_NAME: str = os.getenv("ARI_APP_NAME", "convobridge")  # Stasis app name

    # RTP Configuration
    RTP_LOCAL_IP: str = os.getenv(
        "RTP_LOCAL_IP", "0.0.0.0"
    )  # Listen on all interfaces
    # The external host address given to Asterisk for externalMedia
    ARI_EXTERNAL_HOST: str = os.getenv("ARI_EXTERNAL_HOST", "127.0.0.1")
    RTP_LOCAL_PORT_RANGE_START: int = int(
        os.getenv("RTP_LOCAL_PORT_RANGE_START", "10000")
    )
    RTP_LOCAL_PORT_RANGE_END: int = int(
        os.getenv("RTP_LOCAL_PORT_RANGE_END", "20000")
    )

    # Audio Configuration
    ASTERISK_AUDIO_FORMAT: str = "ulaw"  # Asterisk external media format
    ASTERISK_SAMPLE_RATE: int = 8000  # Hz
    ASTERISK_FRAME_MS: int = 20  # milliseconds per RTP frame
    ASTERISK_CHANNELS: int = 1  # mono

    GEMINI_SAMPLE_RATE: int = 16000  # Hz for Gemini input
    GEMINI_OUTPUT_SAMPLE_RATE: int = 24000  # Hz from Gemini
    GEMINI_CHANNELS: int = 1  # mono

    # Gemini Live Configuration
    GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY", None)
    GOOGLE_CLOUD_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    GOOGLE_CLOUD_LOCATION: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    GEMINI_MODEL: str = os.getenv(
        "GEMINI_MODEL", "gemini-2.0-flash-exp"
    )  # Model name
    GEMINI_VOICE: str = os.getenv("GEMINI_VOICE", "Puck")  # Voice presets

    # Service Configuration
    SERVICE_LOG_LEVEL: str = os.getenv("SERVICE_LOG_LEVEL", "INFO").upper()
    ENABLE_TRANSCRIPT_LOGGING: bool = (
        os.getenv("ENABLE_TRANSCRIPT_LOGGING", "true").lower() == "true"
    )
    ENABLE_REDIS_STATE: bool = (
        os.getenv("ENABLE_REDIS_STATE", "false").lower() == "true"
    )

    # Concurrency & Call Duration Limits
    MAX_CONCURRENT_CALLS: int = int(os.getenv("MAX_CONCURRENT_CALLS", "10"))
    CALL_DURATION_LIMIT_SECONDS: int = int(
        os.getenv("CALL_DURATION_LIMIT_SECONDS", "300")
    )
    SILENCE_PADDING_MS: int = int(os.getenv("SILENCE_PADDING_MS", "100"))

    # Conversation Settings
    INITIAL_MESSAGE: str = os.getenv("INITIAL_MESSAGE", "Hi there!")

    # Multi-tenancy
    COMPANY_SLUG: str = os.getenv("COMPANY_SLUG", "default")

    # Redis Configuration (optional for scaling)
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL", None)

    # PostgreSQL Configuration (optional for transcript/metrics storage)
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "convobridge")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASS: str = os.getenv("DB_PASS", "")
    ENABLE_DB_LOGGING: bool = (
        os.getenv("ENABLE_DB_LOGGING", "false").lower() == "true"
    )

    # Telegram Integration (optional for notifications)
    TELEGRAM_TOKEN: Optional[str] = os.getenv("TELEGRAM_TOKEN", None)
    TELEGRAM_ADMINS: str = os.getenv("TELEGRAM_ADMINS", "")

    # System Instruction
    SYSTEM_INSTRUCTION: str = os.getenv(
        "SYSTEM_INSTRUCTION",
        """You are a helpful AI assistant integrated with Asterisk telephony.
Respond naturally and conversationally. Keep responses concise for telephony.
If the user interrupts, acknowledge and respond to the new input promptly.""",
    )

    @classmethod
    def validate(cls) -> None:
        """Validate critical configuration."""
        # Require either API key or Google Cloud project for Gemini
        if not cls.GOOGLE_API_KEY and not cls.GOOGLE_CLOUD_PROJECT:
            raise ValueError(
                "Either GOOGLE_API_KEY or GOOGLE_CLOUD_PROJECT must be set for Gemini Auth"
            )
        if not cls.ARI_BASE_URL:
            raise ValueError("ARI_BASE_URL environment variable must be set")
