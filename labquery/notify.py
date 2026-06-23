"""Slack notification support for labquery.

Sends run completions, errors, and measurement results to a Slack
incoming webhook. Falls back to a no-op stub when no webhook URL
is configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("labquery")


@dataclass
class SlackNotifier:
    webhook_url: str | None = None

    @property
    def enabled(self) -> bool:
        return self.webhook_url is not None

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            resp = httpx.post(self.webhook_url, json={"text": text}, timeout=5)
            resp.raise_for_status()
            return True
        except Exception:
            log.warning("Slack notification failed", exc_info=True)
            return False

    def notify_run_completed(
        self,
        run_id: str,
        protocol_name: str,
        sample_count: int,
        estimated_minutes: float,
    ) -> bool:
        return self.send(
            f":white_check_mark: *Run completed* — `{run_id}`\n"
            f"Protocol: {protocol_name}\n"
            f"Samples: {sample_count} | Est. time: {estimated_minutes:.1f} min"
        )

    def notify_run_error(
        self,
        run_id: str,
        protocol_name: str,
        error: str,
    ) -> bool:
        return self.send(
            f":x: *Run failed* — `{run_id}`\n"
            f"Protocol: {protocol_name}\n"
            f"Error: {error}"
        )

    def notify_measurement(
        self,
        sample_ids: list[str],
        value: float,
    ) -> bool:
        ids = ", ".join(sample_ids)
        return self.send(
            f":bar_chart: *Measurement complete*\n"
            f"Samples: {ids}\n"
            f"Signal: {value:.4f} midi-chlorian"
        )
