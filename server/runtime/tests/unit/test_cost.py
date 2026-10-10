"""Tests for the OpenAI Realtime cost tracker.

See docs/triage.md T2.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from huxley.cost import (
    DEFAULT_PRICING_MODEL,
    PRICES,
    CostThresholds,
    CostTracker,
    compute_cost_usd,
)

if TYPE_CHECKING:
    from huxley.storage.db import Storage


# Realistic-ish usage payloads. Token counts are illustrative, not
# captured from actual responses — the math is what's being verified.

USAGE_SIMPLE = {
    "input_token_details": {
        "text_tokens": 1000,
        "audio_tokens": 100,
        "cached_tokens": 0,
    },
    "output_token_details": {
        "text_tokens": 50,
        "audio_tokens": 200,
    },
}

USAGE_WITH_CACHE = {
    "input_token_details": {
        "text_tokens": 1000,
        "audio_tokens": 100,
        "cached_tokens": 800,
        "cached_tokens_details": {
            "text_tokens": 700,
            "audio_tokens": 100,
        },
    },
    "output_token_details": {
        "text_tokens": 50,
        "audio_tokens": 200,
    },
}

USAGE_CACHE_NO_BREAKDOWN = {
    # cached_tokens reported but no per-modality breakdown — exercises
    # the "treat all cached as text" fallback.
    "input_token_details": {
        "text_tokens": 1000,
        "audio_tokens": 100,
        "cached_tokens": 800,
    },
    "output_token_details": {
        "text_tokens": 50,
        "audio_tokens": 200,
    },
}


class TestComputeCostUsd:
    def test_mini_pricing_simple_usage(self) -> None:
        cost = compute_cost_usd("gpt-realtime-2.1-mini", USAGE_SIMPLE)
        # Expected: 1000*0.60/1M + 100*10/1M + 50*2.40/1M + 200*20/1M
        # = 0.0006 + 0.001 + 0.00012 + 0.004 = 0.00572
        assert round(cost, 6) == round(0.00572, 6)

    def test_full_model_pricing(self) -> None:
        cost = compute_cost_usd("gpt-realtime-2.1", USAGE_SIMPLE)
        # Same shape, full pricing: 1000*4/1M + 100*32/1M + 50*24/1M + 200*64/1M
        # = 0.004 + 0.0032 + 0.0012 + 0.0128 = 0.0212
        assert round(cost, 6) == round(0.0212, 6)

    def test_cached_tokens_billed_at_cached_rate(self) -> None:
        cost = compute_cost_usd("gpt-realtime-2.1-mini", USAGE_WITH_CACHE)
        # Fresh text in: 1000-700 = 300. Fresh audio in: 100-100 = 0.
        # Cached text: 700 @ 0.06. Cached audio: 100 @ 0.30.
        # Output text: 50 @ 2.40. Output audio: 200 @ 20.
        expected = (
            300 * 0.60 / 1_000_000  # fresh text in
            + 700 * 0.06 / 1_000_000  # cached text
            + 0 * 10.0 / 1_000_000  # fresh audio in
            + 100 * 0.30 / 1_000_000  # cached audio
            + 50 * 2.40 / 1_000_000  # text out
            + 200 * 20.0 / 1_000_000  # audio out
        )
        assert round(cost, 8) == round(expected, 8)

    def test_cache_without_breakdown_assumes_text(self) -> None:
        cost = compute_cost_usd("gpt-realtime-2.1-mini", USAGE_CACHE_NO_BREAKDOWN)
        # cached_tokens=800, no breakdown -> all treated as cached text.
        # Fresh text: 1000-800=200. Fresh audio: 100. Cached text: 800.
        expected = (
            200 * 0.60 / 1_000_000
            + 800 * 0.06 / 1_000_000
            + 100 * 10.0 / 1_000_000
            + 0 * 0.30 / 1_000_000
            + 50 * 2.40 / 1_000_000
            + 200 * 20.0 / 1_000_000
        )
        assert round(cost, 8) == round(expected, 8)

    def test_unknown_model_falls_back_to_default(self) -> None:
        # Should not raise; should use the default model's pricing.
        cost_unknown = compute_cost_usd("gpt-9000-future", USAGE_SIMPLE)
        cost_default = compute_cost_usd(DEFAULT_PRICING_MODEL, USAGE_SIMPLE)
        assert cost_unknown == cost_default

    def test_missing_token_details_treats_as_zero(self) -> None:
        cost = compute_cost_usd(
            "gpt-realtime-2.1-mini",
            {"input_token_details": {}, "output_token_details": {}},
        )
        assert cost == 0.0

    def test_missing_top_level_keys_treats_as_zero(self) -> None:
        cost = compute_cost_usd("gpt-realtime-2.1-mini", {})
        assert cost == 0.0

    def test_known_models_table_in_sync(self) -> None:
        # The shipped default and the documented upgrade must have pricing
        # rows. Catches a default-model bump without a PRICES update, which
        # would silently bill at the fallback rate.
        from huxley.config import Settings

        assert Settings.model_fields["openai_model"].default in PRICES
        assert DEFAULT_PRICING_MODEL in PRICES
        assert "gpt-realtime-2.1" in PRICES


class _FixedClock:
    def __init__(self, day: str = "2026-04-18") -> None:
        self._day = day

    def __call__(self) -> datetime:
        return datetime.strptime(self._day, "%Y-%m-%d").replace(tzinfo=UTC)

    def advance_to(self, day: str) -> None:
        self._day = day


def _tracker(
    storage: Storage,
    *,
    thresholds: CostThresholds | None = None,
    on_kill_switch=None,
    clock: _FixedClock | None = None,
) -> CostTracker:
    return CostTracker(
        storage=storage,
        model="gpt-realtime-2.1-mini",
        thresholds=thresholds,
        on_kill_switch=on_kill_switch,
        clock=clock or _FixedClock(),
    )


class TestCostTrackerAccumulates:
    async def test_records_micro_usd_to_storage(self, storage: Storage) -> None:
        tr = _tracker(storage)
        await tr.record(USAGE_SIMPLE)

        stored = await storage.get_setting("cost:2026-04-18:micro_usd")
        # USAGE_SIMPLE on mini = 0.00572 USD = 5720 micro-dollars
        assert stored is not None
        assert int(stored) == 5720

    async def test_sub_cent_responses_add_up_and_cross_warn(self, storage: Storage) -> None:
        # Regression: whole-cent rounding stored round(0.4) == 0 for every
        # sub-half-cent response, so a day of cheap mini responses never
        # reached any threshold. 150 x $0.004 = $0.60 must cross warn ($0.50).
        tr = _tracker(storage)
        cheap = {"output_token_details": {"audio_tokens": 200}}  # 200*20/1M = $0.004
        for _ in range(150):
            await tr.record(cheap)

        stored = await storage.get_setting("cost:2026-04-18:micro_usd")
        assert stored is not None
        assert int(stored) == 600_000
        warned = await storage.get_setting("cost:2026-04-18:warned")
        assert warned is not None
        assert "warn" in warned.split(",")

    async def test_accumulates_across_records(self, storage: Storage) -> None:
        tr = _tracker(storage)
        big_usage = {
            "input_token_details": {"text_tokens": 100_000},
            "output_token_details": {"audio_tokens": 100_000},
        }
        # Per record: 100k*0.60/1M + 100k*20/1M = 0.06 + 2 = 2.06 USD
        await tr.record(big_usage)
        await tr.record(big_usage)

        stored = await storage.get_setting("cost:2026-04-18:micro_usd")
        assert stored is not None
        assert int(stored) == 4_120_000  # 2.06 USD * 2

    async def test_zero_cost_record_is_noop(self, storage: Storage) -> None:
        tr = _tracker(storage)
        await tr.record({"input_token_details": {}, "output_token_details": {}})

        # Storage key never written.
        stored = await storage.get_setting("cost:2026-04-18:micro_usd")
        assert stored is None

    async def test_per_day_keys_are_independent(self, storage: Storage) -> None:
        clock = _FixedClock("2026-04-18")
        tr = _tracker(storage, clock=clock)

        big = {
            "input_token_details": {"text_tokens": 100_000},
            "output_token_details": {"audio_tokens": 100_000},
        }
        await tr.record(big)

        clock.advance_to("2026-04-19")
        await tr.record(big)

        d1 = await storage.get_setting("cost:2026-04-18:micro_usd")
        d2 = await storage.get_setting("cost:2026-04-19:micro_usd")
        assert d1 is not None and int(d1) == 2_060_000
        assert d2 is not None and int(d2) == 2_060_000


class TestCostTrackerThresholds:
    async def test_warn_threshold_fires_kill_switch_does_not(self, storage: Storage) -> None:
        kill_calls: list[bool] = []

        async def fake_kill() -> None:
            kill_calls.append(True)

        tr = _tracker(
            storage,
            thresholds=CostThresholds(warn_usd=0.50, bug_canary_usd=5.00, kill_switch_usd=20.00),
            on_kill_switch=fake_kill,
        )

        # One usage that costs ~$0.60 — crosses warn but not bug_canary.
        usage = {
            "input_token_details": {"text_tokens": 1_000_000},  # 0.60 USD
            "output_token_details": {},
        }
        await tr.record(usage)

        assert kill_calls == []
        warned = await storage.get_setting("cost:2026-04-18:warned")
        assert warned is not None
        assert "warn" in warned
        assert "kill_switch" not in warned

    async def test_kill_switch_callback_invoked_when_ceiling_crossed(
        self, storage: Storage
    ) -> None:
        kill_calls: list[bool] = []

        async def fake_kill() -> None:
            kill_calls.append(True)

        tr = _tracker(storage, on_kill_switch=fake_kill)

        # 30 USD in one shot, crosses all three thresholds.
        usage = {
            "input_token_details": {"text_tokens": 50_000_000},  # 30 USD on mini
            "output_token_details": {},
        }
        await tr.record(usage)

        assert kill_calls == [True]

    async def test_threshold_warning_idempotent_within_day(self, storage: Storage) -> None:
        kill_calls: list[bool] = []

        async def fake_kill() -> None:
            kill_calls.append(True)

        tr = _tracker(storage, on_kill_switch=fake_kill)

        big = {
            "input_token_details": {"text_tokens": 50_000_000},
            "output_token_details": {},
        }
        await tr.record(big)
        await tr.record(big)
        await tr.record(big)

        # Kill switch must only fire once, even though every call crosses
        # the ceiling.
        assert kill_calls == [True]

    async def test_thresholds_reset_on_new_day(self, storage: Storage) -> None:
        kill_calls: list[bool] = []

        async def fake_kill() -> None:
            kill_calls.append(True)

        clock = _FixedClock("2026-04-18")
        tr = _tracker(storage, on_kill_switch=fake_kill, clock=clock)

        big = {
            "input_token_details": {"text_tokens": 50_000_000},
            "output_token_details": {},
        }
        await tr.record(big)
        assert kill_calls == [True]

        # New day — fresh state, threshold can fire again.
        clock.advance_to("2026-04-19")
        await tr.record(big)
        assert kill_calls == [True, True]
