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

"""Optional database layer for transcripts and metrics."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TranscriptStore:
    """Simple in-memory transcript storage (replace with database in production)."""

    def __init__(self):
        self.transcripts = {}

    async def save_transcript(
        self, channel_id: str, caller: str, dialed_did: str, transcript: list
    ) -> None:
        """Save transcript for a call."""
        try:
            self.transcripts[channel_id] = {
                "caller": caller,
                "dialed_did": dialed_did,
                "transcript": transcript,
            }
            logger.info(f"Saved transcript for {channel_id}")
        except Exception as e:
            logger.error(f"Error saving transcript: {e}")

    async def get_transcript(self, channel_id: str) -> Optional[dict]:
        """Retrieve transcript."""
        return self.transcripts.get(channel_id)

    async def delete_transcript(self, channel_id: str) -> None:
        """Delete transcript."""
        if channel_id in self.transcripts:
            del self.transcripts[channel_id]


class CallMetrics:
    """Store call metrics for monitoring."""

    def __init__(self):
        self.metrics = {}

    async def record_metric(self, channel_id: str, metric_name: str, value) -> None:
        """Record a metric."""
        if channel_id not in self.metrics:
            self.metrics[channel_id] = {}
        self.metrics[channel_id][metric_name] = value

    async def get_metrics(self, channel_id: str) -> dict:
        """Get all metrics for a call."""
        return self.metrics.get(channel_id, {})


# Global instances
_transcript_store: Optional[TranscriptStore] = None
_call_metrics: Optional[CallMetrics] = None


def get_transcript_store() -> TranscriptStore:
    """Get global transcript store."""
    global _transcript_store
    if _transcript_store is None:
        _transcript_store = TranscriptStore()
    return _transcript_store


def get_call_metrics() -> CallMetrics:
    """Get global call metrics."""
    global _call_metrics
    if _call_metrics is None:
        _call_metrics = CallMetrics()
    return _call_metrics
