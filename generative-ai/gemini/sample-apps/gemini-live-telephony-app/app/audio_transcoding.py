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

    async def asterisk_to_gemini(self, ulaw_pcm: bytes) -> bytes:
        """
        Convert Asterisk RTP payload to Gemini input.

        Pipeline:
        1. PCMU μ-law 8kHz -> PCM16 8kHz
        2. Resample 8kHz -> 16kHz
        3. Return as PCM16 bytes
        """
        try:
            # 1. Decode μ-law to PCM16
            pcm_8k = audioop.ulaw2lin(ulaw_pcm, 2)

            # 2. Convert to float32 for resampling
            arr_8k = np.frombuffer(pcm_8k, dtype=np.int16)
            arr_8k_float = arr_8k.astype(np.float32) / 32768.0

            # 3. Resample 8k -> 16k
            arr_16k_float = self.resampler_up.process(
                arr_8k_float, ratio=2.0, end_of_input=False
            )

            # 4. Convert back to int16
            arr_16k = (arr_16k_float * 32767).astype(np.int16)

            return arr_16k.tobytes()
        except Exception as e:
            logger.error(f"Error in asterisk_to_gemini: {e}")
            return b""

    async def gemini_to_asterisk(self, pcm_24k: bytes) -> bytes:
        """
        Convert Gemini output to Asterisk RTP payload.

        Pipeline:
        1. PCM16 24kHz -> PCM16 8kHz (resample down)
        2. PCM16 8kHz -> PCMU μ-law 8kHz
        3. Return as μ-law bytes in 20ms RTP frames
        """
        try:
            # 1. Convert to float32 for resampling
            arr_24k = np.frombuffer(pcm_24k, dtype=np.int16)
            arr_24k_float = arr_24k.astype(np.float32) / 32768.0

            # 2. Resample 24k -> 8k
            arr_8k_float = self.resampler_down.process(
                arr_24k_float, ratio=(8000.0 / 24000.0), end_of_input=False
            )

            # 3. Convert back to int16
            arr_8k = (arr_8k_float * 32767).astype(np.int16)

            # 4. Convert PCM to μ-law
            ulaw = audioop.lin2ulaw(arr_8k.tobytes(), 2)

            return ulaw
        except Exception as e:
            logger.error(f"Error in gemini_to_asterisk: {e}")
            return b""

    def get_silence_frame(self) -> bytes:
        """Return 20ms of silence (160 samples at 8kHz μ-law)."""
        return b"\xff" * 160  # μ-law silence


async def rtp_inbound_to_gemini(
    rtp_manager, audio_queue: asyncio.Queue, transcoder: AudioTranscoder, session
) -> None:
    """
    Receive RTP from Asterisk and forward to Gemini.
    """
    while session and not session.is_playing:
        try:
            result = await rtp_manager.receive()
            if result:
                ulaw_payload, addr = result
                # Convert to Gemini format
                gemini_audio = await transcoder.asterisk_to_gemini(ulaw_payload)
                if gemini_audio:
                    await audio_queue.put(gemini_audio)
            else:
                await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Error in rtp_inbound_to_gemini: {e}")
            break


async def gemini_outbound_to_rtp(
    rtp_manager, audio_queue: asyncio.Queue, transcoder: AudioTranscoder, session
) -> None:
    """
    Receive audio from Gemini and packetize into RTP for Asterisk.
    """
    buffer = b""
    frame_size = 160  # 20ms at 8kHz μ-law

    while session:
        try:
            # Collect audio from Gemini
            try:
                chunk_24k = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                if chunk_24k:
                    ulaw = await transcoder.gemini_to_asterisk(chunk_24k)
                    buffer += ulaw
            except asyncio.TimeoutError:
                # Send silence if timeout
                if not session.is_playing:
                    buffer += transcoder.get_silence_frame()

            # Packetize into 20ms frames and send
            while len(buffer) >= frame_size:
                frame = buffer[:frame_size]
                buffer = buffer[frame_size:]
                await rtp_manager.send(frame)

            await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in gemini_outbound_to_rtp: {e}")
            break
