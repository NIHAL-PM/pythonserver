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

from google import genai
from google.genai import types

from app.config import Config

logger = logging.getLogger(__name__)


async def run_gemini_session(
    client,
    model_id: str,
    in_q: asyncio.Queue,
    out_q: asyncio.Queue,
    session,
    system_instruction: str = None,
) -> None:
    """
    Manages Gemini Live session for a call.

    Adapted from the Twilio sample to work with per-call session state.
    """
    if not system_instruction:
        system_instruction = Config.SYSTEM_INSTRUCTION

    session_handle = session.gemini_session_handle

    # 10ms of silence at 16kHz (320 bytes for 16-bit PCM)
    silent_chunk = b"\x00" * 320

    while session:
        try:
            logger.info(
                f"Connecting to Gemini for {session.channel_id} (Resumption: {session_handle is not None})..."
            )

            config = types.LiveConnectConfig(
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
                response_modalities=["AUDIO"],
                session_resumption=types.SessionResumptionConfig(handle=session_handle),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=Config.GEMINI_VOICE,
                        )
                    ),
                    language_code="en-US",
                ),
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                        prefix_padding_ms=0,
                        silence_duration_ms=50,
                    )
                ),
            )

            async with client.aio.live.connect(
                model=model_id, config=config
            ) as gemini_session:
                logger.info(f"Gemini connected for {session.channel_id}")
                session.gemini_ws = gemini_session

                async def sender_loop():
                    """Send audio to Gemini."""
                    while session and session.gemini_ws:
                        try:
                            if session.tool_processing_flag:
                                # Gate audio during tool processing
                                await asyncio.sleep(0.01)
                                continue

                            chunk = await asyncio.wait_for(in_q.get(), timeout=0.01)
                            await gemini_session.send_realtime_input(
                                audio=types.Blob(
                                    data=chunk, mime_type="audio/pcm;rate=16000"
                                )
                            )
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            logger.error(f"Sender error for {session.channel_id}: {e}")
                            break

                async def heartbeat_loop():
                    """Send heartbeat to keep session alive."""
                    while session and session.gemini_ws:
                        try:
                            await gemini_session.send_realtime_input(
                                audio=types.Blob(
                                    data=silent_chunk, mime_type="audio/pcm;rate=16000"
                                )
                            )
                            await asyncio.sleep(5)
                        except Exception as e:
                            logger.error(f"Heartbeat error for {session.channel_id}: {e}")
                            break

                sender_task = asyncio.create_task(sender_loop())
                heartbeat_task = asyncio.create_task(heartbeat_loop())

                try:
                    while session and session.gemini_ws:
                        try:
                            message = await asyncio.wait_for(
                                gemini_session.receive().__anext__(), timeout=0.01
                            )

                            if message.session_resumption_update:
                                update = message.session_resumption_update
                                if update.new_handle:
                                    session_handle = update.new_handle
                                    session.gemini_session_handle = session_handle
                                    logger.info(
                                        f"Saved session handle for {session.channel_id}"
                                    )

                            if message.server_content:
                                if message.server_content.interrupted:
                                    logger.info(f"Gemini interrupted for {session.channel_id}")
                                    # Clear the outbound queue on interruption
                                    while not out_q.empty():
                                        try:
                                            out_q.get_nowait()
                                        except asyncio.QueueEmpty:
                                            break

                                if message.server_content.model_turn:
                                    for part in message.server_content.model_turn.parts:
                                        if part.inline_data:
                                            await out_q.put(part.inline_data.data)

                                if message.server_content.turn_complete:
                                    logger.info(
                                        f"Turn complete for {session.channel_id}"
                                    )

                        except asyncio.TimeoutError:
                            continue
                        except StopAsyncIteration:
                            logger.warning(f"Gemini stream closed for {session.channel_id}")
                            break
                        except Exception as e:
                            logger.error(f"Receiver error for {session.channel_id}: {e}")
                            break
                finally:
                    sender_task.cancel()
                    heartbeat_task.cancel()
                    session.gemini_ws = None

        except Exception as e:
            logger.error(f"Session error for {session.channel_id}: {e}")
            if not session:
                break
            await asyncio.sleep(2)

    logger.info(f"Gemini session completed for {session.channel_id}")
