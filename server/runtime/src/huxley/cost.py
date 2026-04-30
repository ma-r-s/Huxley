"""OpenAI Realtime cost tracking — bug canary, not spend control.

Records per-response token usage from `response.done.usage` payloads and
logs warnings at daily-total thresholds. Optional kill-switch disconnects
the session at the hard ceiling — protection against runaway tool loops
that could 100x a normal day's bill.

The threshold structure is intentionally three-tier:
- `warn`        — about 1x a normal day; informational
- `bug_canary`  — about 10x normal; "something is wrong, investigate"
- `kill_switch` — about 100x normal; "stop now, do not let this run"

Daily totals persist to `Storage.set_setting` under
`cost:YYYY-MM-DD:cents` so warnings survive restarts and the kill-switch
state is recoverable. Cents are stored as integers to avoid float
serialization drift.

See docs/triage.md T2.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from huxley.storage.db import Storage

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per 1M tokens for one Realtime model."""

    text_input_per_1m: float
    text_input_cached_per_1m: float
    text_output_per_1m: float
    audio_input_per_1m: float
    audio_input_cached_per_1m: float
    audio_output_per_1m: float


# Prices verified 2026-04-18 from openai.com/api/pricing.
# Update when OpenAI changes rates or when adding a new model.
PRICES: dict[str, ModelPricing] = {
    "gpt-4o-mini-realtime-preview": ModelPricing(
        text_input_per_1m=0.60,
        text_input_cached_per_1m=0.30,
        text_output_per_1m=2.40,
        audio_input_per_1m=10.00,
        audio_input_cached_per_1m=0.30,
        audio_output_per_1m=20.00,
    ),
    "gpt-4o-realtime-preview": ModelPricing(
        text_input_per_1m=5.00,
        text_input_cached_per_1m=2.50,
        text_output_per_1m=20.00,
        audio_input_per_1m=100.00,
        audio_input_cached_per_1m=20.00,
        audio_output_per_1m=200.00,
    ),
}


@dataclass(frozen=True, slots=True)
class CostThresholds:
    """Daily-total USD ceilings for warnings + kill switch.

    Defaults assume the Abuelo daily-driver pattern: a few conversations
    a day plus passive listening. Adjust per persona if usage profile
    differs (a heavy multi-tool persona could legitimately exceed
    `warn_usd` without being broken).
    """

    warn_usd: float = 0.50
    bug_canary_usd: float = 5.00
    kill_switch_usd: float = 20.00


def compute_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Compute USD cost for one `response.done.usage` payload.

    Unknown models fall back to mini pricing with a warning log so a
    fresh model rollout doesn't silently produce zero-cost numbers.
    Missing token-detail subkeys default to 0. Cached tokens are billed
    at the cached rate; the rest at the full rate.
    """
    pricing = PRICES.get(model)
    if pricing is None:
        logger.warning(
            "cost.unknown_model_using_mini_pricing",
            model=model,
            known=list(PRICES.keys()),
        )
        pricing = PRICES["gpt-4o-mini-realtime-preview"]

    in_details = usage.get("input_token_details", {}) or {}
    out_details = usage.get("output_token_details", {}) or {}

    text_in_total = int(in_details.get("text_tokens", 0))
    audio_in_total = int(in_details.get("audio_tokens", 0))
    cached_in = int(in_details.get("cached_tokens", 0))

    # Cached split between text and audio (when reported). Fall back to
    # "all cached are text" — system prompt re-sends are the dominant
    # cached payload in practice.
    cached_details = in_details.get("cached_tokens_details", {}) or {}
    cached_text = int(cached_details.get("text_tokens", 0))
    cached_audio = int(cached_details.get("audio_tokens", 0))
    if cached_text == 0 and cached_audio == 0 and cached_in > 0:
        cached_text = cached_in

    fresh_text_in = max(0, text_in_total - cached_text)
    fresh_audio_in = max(0, audio_in_total - cached_audio)

    text_out = int(out_details.get("text_tokens", 0))
    audio_out = int(out_details.get("audio_tokens", 0))

    return (
        fresh_text_in * pricing.text_input_per_1m / 1_000_000
        + cached_text * pricing.text_input_cached_per_1m / 1_000_000
        + fresh_audio_in * pricing.audio_input_per_1m / 1_000_000
        + cached_audio * pricing.audio_input_cached_per_1m / 1_000_000
        + text_out * pricing.text_output_per_1m / 1_000_000
        + audio_out * pricing.audio_output_per_1m / 1_000_000
    )


class CostTracker:
    """Accumulates per-day cost and emits warnings at threshold crossings.

    A response.done payload comes in via `record(usage)`. The tracker
    computes cost, persists the new daily total to Storage, logs the
    individual response, and checks against thresholds. Each threshold
    fires its warning at most once per day (idempotency tracked in
    Storage under `cost:YYYY-MM-DD:warned`).

    Kill switch: when configured and the daily total crosses
    `kill_switch_usd`, calls the supplied async callback. Typical
    wiring is `provider.disconnect(save_summary=True)`.
    """

    def __init__(
        self,
        storage: Storage,
        model: str,
        thresholds: CostThresholds | None = None,
        on_kill_switch: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._model = model
        self._thresholds = thresholds or CostThresholds()
        self._on_kill_switch = on_kill_switch
        self._clock = clock or (lambda: datetime.now(UTC))

    async def record(self, usage: dict[str, Any]) -> None:
        """Record one response.done usage payload.

        Idempotent on threshold warnings within a single day — once a
        tier has been warned, won't re-warn until the date rolls.
        """
        cost_usd = compute_cost_usd(self._model, usage)
        if cost_usd <= 0:
            return

        today = self._clock().strftime("%Y-%m-%d")
        cents_key = f"cost:{today}:cents"
        warned_key = f"cost:{today}:warned"

        prev_cents_str = await self._storage.get_setting(cents_key, default="0")
        prev_cents = int(prev_cents_str or "0")
        delta_cents = round(cost_usd * 100)
        new_cents = prev_cents + delta_cents
        await self._storage.set_setting(cents_key, str(new_cents))

        new_total_usd = new_cents / 100
        await logger.ainfo(
            "cost.response_done",
            model=self._model,
            cost_usd=round(cost_usd, 4),
            day_total_usd=round(new_total_usd, 2),
        )

        await self._check_thresholds(warned_key, new_total_usd)

    async def _check_thresholds(self, warned_key: str, day_total_usd: float) -> None:
        warned_str = (await self._storage.get_setting(warned_key, default="")) or ""
        already_warned = set(warned_str.split(",")) if warned_str else set()

        for tier_name, tier_usd in (
            ("warn", self._thresholds.warn_usd),
            ("bug_canary", self._thresholds.bug_canary_usd),
            ("kill_switch", self._thresholds.kill_switch_usd),
        ):
            if tier_name in already_warned:
                continue
            if day_total_usd < tier_usd:
                continue

            await logger.awarning(
                "cost.threshold_crossed",
                tier=tier_name,
                threshold_usd=tier_usd,
                day_total_usd=round(day_total_usd, 2),
            )
            already_warned.add(tier_name)

            if tier_name == "kill_switch" and self._on_kill_switch is not None:
                await logger.aerror(
                    "cost.kill_switch_triggered",
                    day_total_usd=round(day_total_usd, 2),
                )
                await self._on_kill_switch()

        await self._storage.set_setting(warned_key, ",".join(sorted(already_warned)))
