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
import audioop
import logging

import numpy as np
import samplerate

from app.config import Config

logger = logging.getLogger(__name__)


class AudioTranscoder:
    """Handles audio conversion between Asterisk (μ-law 8kHz) and Gemini (PCM 16k/24k)."""

    def __init__(self):
        self.resampler_up = samplerate.Resampler(
            "sinc_fastest", channels=1
        )  # 8k -> 16k
        self.resampler_down = samplerate.Resampler(
            "sinc_fastest", channels=1
        )  # 24k -> 8k

    def asterisk_to_gemini(self, ulaw_pcm: bytes) -> bytes:
        """
        Convert Asterisk RTP payload to Gemini input. Synchronous for speed.
        """
        try:
            # 1. Decode μ-law to PCM16
            pcm_8k = audioop.ulaw2lin(ulaw_pcm, 2)

            # 2. Resample 8k -> 16k using efficient audioop
            # Using audioop.ratecv for fastest integer resampling
            pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
            return pcm_16k
        except Exception as e:
            logger.error(f"Error in asterisk_to_gemini: {e}")
            return b""

    def gemini_to_asterisk(self, pcm_24k: bytes) -> bytes:
        """
        Convert Gemini output to Asterisk RTP payload. Synchronous for speed.
        """
        try:
            # 1. Resample 24k -> 8k using efficient audioop
            pcm_8k, _ = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)

            # 2. Convert PCM to μ-law
            ulaw = audioop.lin2ulaw(pcm_8k, 2)
            return ulaw
        except Exception as e:
            logger.error(f"Error in gemini_to_asterisk: {e}")
            return b""

    def get_silence_frame(self) -> bytes:
        """Return 20ms of silence (160 samples at 8kHz μ-law)."""
        return b"\xff" * 160  # μ-law silence

