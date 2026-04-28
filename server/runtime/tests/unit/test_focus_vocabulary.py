"""Unit tests for `huxley.focus.vocabulary` — enum invariants + Activity."""

from __future__ import annotations

from datetime import timedelta

import pytest

from huxley.focus.vocabulary import (
    CHANNEL_PRIORITY,
    Activity,
    Channel,
    ContentType,
    FocusState,
    MixingBehavior,
    mixing_for_background,
)


class TestChannelPriorities:
    def test_all_channels_have_priority(self) -> None:
        for channel in Channel:
            assert channel in CHANNEL_PRIORITY, channel

    def test_priorities_are_distinct(self) -> None:
        values = list(CHANNEL_PRIORITY.values())
        assert len(values) == len(set(values))

    def test_dialog_is_highest_priority(self) -> None:
        dialog_pri = CHANNEL_PRIORITY[Channel.DIALOG]
        for channel, priority in CHANNEL_PRIORITY.items():
            if channel is Channel.DIALOG:
                continue
            assert dialog_pri < priority, f"DIALOG ({dialog_pri}) !< {channel.value} ({priority})"

    def test_content_is_lowest_priority(self) -> None:
        content_pri = CHANNEL_PRIORITY[Channel.CONTENT]
        for channel, priority in CHANNEL_PRIORITY.items():
            if channel is Channel.CONTENT:
                continue
            assert content_pri > priority


class TestEnumValues:
    def test_focus_state_values(self) -> None:
        assert {s.value for s in FocusState} == {"foreground", "background", "none"}

    def test_content_type_values(self) -> None:
        assert {c.value for c in ContentType} == {"mixable", "nonmixable"}

    def test_mixing_behavior_values(self) -> None:
        assert {m.value for m in MixingBehavior} == {
            "primary",
            "may_duck",
            "must_pause",
            "must_stop",
        }


class TestMixingForBackground:
    def test_mixable_becomes_may_duck(self) -> None:
        assert mixing_for_background(ContentType.MIXABLE) is MixingBehavior.MAY_DUCK

    def test_nonmixable_becomes_must_pause(self) -> None:
        assert mixing_for_background(ContentType.NONMIXABLE) is MixingBehavior.MUST_PAUSE


class _NoopObserver:
    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        pass


class TestActivity:
    def test_construct_with_all_fields(self) -> None:
        obs = _NoopObserver()
        a = Activity(
            channel=Channel.DIALOG,
            interface_name="turn.user.abc",
            content_type=ContentType.NONMIXABLE,
            observer=obs,
            patience=timedelta(seconds=30),
        )
        assert a.channel is Channel.DIALOG
        assert a.interface_name == "turn.user.abc"
        assert a.content_type is ContentType.NONMIXABLE
        assert a.patience == timedelta(seconds=30)
        assert a.observer is obs

    def test_patience_defaults_to_zero(self) -> None:
        a = Activity(
            channel=Channel.ALERT,
            interface_name="chime",
            content_type=ContentType.NONMIXABLE,
            observer=_NoopObserver(),
        )
        assert a.patience == timedelta(0)

    def test_frozen_rejects_reassignment(self) -> None:
        a = Activity(
            channel=Channel.ALERT,
            interface_name="chime",
            content_type=ContentType.NONMIXABLE,
            observer=_NoopObserver(),
        )
        with pytest.raises(AttributeError):
            a.interface_name = "other"  # type: ignore[misc]

    def test_no_eq_override_identity_by_default(self) -> None:
        """Two Activities with the same fields but different observers are
        NOT equal under default dataclass equality — by design, no __eq__
        override exists, so equality is structural and includes observer
        identity. This matches the plan: dedup is via explicit
        (channel, interface_name) scanning, not `==`.
        """
        obs_a = _NoopObserver()
        obs_b = _NoopObserver()
        a = Activity(
            channel=Channel.ALERT,
            interface_name="chime",
            content_type=ContentType.NONMIXABLE,
            observer=obs_a,
        )
        b = Activity(
            channel=Channel.ALERT,
            interface_name="chime",
            content_type=ContentType.NONMIXABLE,
            observer=obs_b,
        )
        assert a != b  # different observer → different Activity under default eq
