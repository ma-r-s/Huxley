"""Skill-level tests -- tool dispatch, config parsing, claim wiring.

Uses a stub transport so no pyrogram imports are needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import structlog

if TYPE_CHECKING:
    from pathlib import Path

from huxley_sdk import (
    ClaimEndReason,
    ClaimHandle,
    InputClaim,
    SkillContext,
)
from huxley_skill_comms_telegram.skill import CommsTelegramSkill


class StubTransport:
    """In-memory stand-in for `TelegramTransport` -- records calls,
    never touches the network. Injected via `transport_factory`.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_dir: Path,
        userbot_phone: str | None = None,
        on_incoming_ring: Any = None,
        on_ring_cancelled: Any = None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = session_dir
        self.userbot_phone = userbot_phone
        self.on_incoming_ring = on_incoming_ring
        self.on_ring_cancelled = on_ring_cancelled
        self.connected = False
        self.placed_calls: list[int] = []
        self.accepted_calls: list[int] = []
        self.rejected_calls: list[int] = []
        self.mic_frames: list[bytes] = []
        self.ended_calls: int = 0
        self.disconnected: int = 0
        # Scripted behavior for tests to toggle.
        self.resolve_map: dict[str, int] = {}
        self.raise_on_place_call: Exception | None = None
        self.raise_on_accept_call: Exception | None = None
        # Simulate an active call for busy-rejection tests.
        self._active_user_id: int | None = None

    async def connect(self) -> None:
        self.connected = True

    async def resolve_contact(self, identifier: str) -> int:
        if identifier in self.resolve_map:
            return self.resolve_map[identifier]
        return 12345  # default stub user_id

    async def place_call(self, user_id: int) -> None:
        if self.raise_on_place_call is not None:
            raise self.raise_on_place_call
        self.placed_calls.append(user_id)
        self._active_user_id = user_id

    async def accept_call(self, user_id: int) -> None:
        if self.raise_on_accept_call is not None:
            raise self.raise_on_accept_call
        self.accepted_calls.append(user_id)
        self._active_user_id = user_id

    async def reject_call(self, user_id: int) -> None:
        self.rejected_calls.append(user_id)

    async def send_pcm(self, pcm: bytes) -> None:
        self.mic_frames.append(pcm)

    async def peer_audio_chunks(self):  # type: ignore[no-untyped-def]
        # Empty iterator by default; a specific test can replace this.
        if False:
            yield b""

    async def end_call(self) -> None:
        self.ended_calls += 1
        self._active_user_id = None

    async def disconnect(self) -> None:
        self.disconnected += 1


class FakeStorage:
    async def get_setting(self, key: str) -> str | None:
        return None

    async def set_setting(self, key: str, value: str) -> None:
        pass

    async def delete_setting(self, key: str) -> None:
        pass

    async def list_settings(self, prefix: str) -> list[tuple[str, str]]:
        return []


def _build_ctx(
    config: dict[str, Any],
    data_dir: Path,
    *,
    captured_turns: list[str] | None = None,
    captured_claims: list[InputClaim] | None = None,
) -> tuple[SkillContext, list[StubTransport]]:
    """Return (ctx, captured_transports). Tests inspect the captured
    stub after calling `setup()` to verify config was applied.
    `captured_turns` receives any inject_turn() calls if provided.
    `captured_claims` receives any start_input_claim() calls if provided.
    """
    _turns = captured_turns if captured_turns is not None else []
    _claims = captured_claims if captured_claims is not None else []

    async def inject_turn(prompt: str) -> None:
        _turns.append(prompt)

    def background_task(name: str, fn: Any, **kwargs: Any) -> Any:
        raise AssertionError("comms_telegram shouldn't use background_task yet")

    async def start_input_claim(claim: InputClaim) -> ClaimHandle:
        _claims.append(claim)

        async def _wait() -> ClaimEndReason:
            return ClaimEndReason.NATURAL

        return ClaimHandle(_cancel=lambda: None, _wait_end=_wait)

    async def cancel_active_claim() -> None:
        pass

    return SkillContext(  # type: ignore[call-arg]
        logger=structlog.get_logger().bind(skill="comms_telegram"),
        storage=FakeStorage(),  # type: ignore[arg-type]
        persona_data_dir=data_dir,
        config=config,
        inject_turn=inject_turn,
        background_task=background_task,  # type: ignore[arg-type]
        start_input_claim=start_input_claim,  # type: ignore[arg-type]
        cancel_active_claim=cancel_active_claim,
    ), []


class TestSetup:
    @pytest.mark.asyncio
    async def test_valid_config_populates_contacts(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {
                "api_id": 12345678,
                "api_hash": "abcdef0123456789",
                "userbot_phone": "+573153283397",
                "contacts": {
                    "Hija": "+57 318 685 1696",
                    "Hijo": "+573001234567",
                },
            },
            tmp_path,
        )
        await skill.setup(ctx)

        # Names lowercased, phones normalized.
        assert skill._contacts == {  # type: ignore[attr-defined]
            "hija": "+573186851696",
            "hijo": "+573001234567",
        }
        assert len(captured) == 1
        assert captured[0].api_id == 12345678
        assert captured[0].userbot_phone == "+573153283397"
        assert captured[0].session_dir == tmp_path

    @pytest.mark.asyncio
    async def test_missing_api_id_soft_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ensure no env vars leak in from the test environment.
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_ID", raising=False)
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_HASH", raising=False)

        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {"api_hash": "abc", "contacts": {"x": "+1"}},
            tmp_path,
        )
        # Soft-fail: setup completes, but transport stays None so
        # a persona listing this skill can still boot. `call_contact`
        # returns an LLM-facing error rather than exploding.
        await skill.setup(ctx)
        assert skill._transport is None  # type: ignore[attr-defined]

        result = await skill.handle("call_contact", {"name": "x"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "configura" in payload["error"].lower()

    @pytest.mark.asyncio
    async def test_empty_contacts_still_sets_up(self, tmp_path: Path) -> None:
        # Missing contacts is a warning, not a failure -- lets someone
        # deploy with config-first-then-contacts-later without breaking.
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {"api_id": 12345, "api_hash": "abc"},
            tmp_path,
        )
        await skill.setup(ctx)
        assert skill._contacts == {}  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_env_vars_override_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_ID", "99999999")
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "env_hash_winning")
        monkeypatch.setenv("HUXLEY_TELEGRAM_USERBOT_PHONE", "+19999999999")

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {
                "api_id": 111,
                "api_hash": "config_hash",
                "userbot_phone": "+57111",
                "contacts": {"x": "+1"},
            },
            tmp_path,
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 99999999
        assert captured[0].api_hash == "env_hash_winning"
        assert captured[0].userbot_phone == "+19999999999"

    @pytest.mark.asyncio
    async def test_env_vars_only_also_work(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_ID", "77777777")
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "envhashonly")

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"contacts": {"x": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 77777777

    @pytest.mark.asyncio
    async def test_non_string_phone_values_dropped(self, tmp_path: Path) -> None:
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {
                "api_id": 12345,
                "api_hash": "abc",
                "contacts": {
                    "good": "+1234",
                    "broken": 1234,  # int, not string -- dropped
                    "empty": "",  # falsy -- dropped
                },
            },
            tmp_path,
        )
        await skill.setup(ctx)
        assert skill._contacts == {"good": "+1234"}  # type: ignore[attr-defined]


class TestCallContactTool:
    @pytest.mark.asyncio
    async def test_unknown_contact_returns_error(self, tmp_path: Path) -> None:
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)

        result = await skill.handle("call_contact", {"name": "cousin_bob"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "cousin_bob" in payload["error"]
        assert result.side_effect is None

    @pytest.mark.asyncio
    async def test_happy_path_dials_and_returns_input_claim(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573186851696": 7392572538}
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {
                "api_id": 1,
                "api_hash": "x",
                "contacts": {"hija": "+57 318 685 1696"},
            },
            tmp_path,
        )
        await skill.setup(ctx)

        result = await skill.handle("call_contact", {"name": "hija"})
        transport = captured[0]

        assert transport.connected is True
        assert transport.placed_calls == [7392572538]

        import json

        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["contact"] == "hija"

        assert isinstance(result.side_effect, InputClaim)
        claim = result.side_effect
        assert claim.on_mic_frame is not None
        assert claim.speaker_source is not None
        assert claim.on_claim_end is not None

    @pytest.mark.asyncio
    async def test_case_insensitive_name_match(self, tmp_path: Path) -> None:
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)

        result = await skill.handle("call_contact", {"name": "  HIJA  "})
        import json

        assert json.loads(result.output)["ok"] is True

    @pytest.mark.asyncio
    async def test_transport_error_returns_clean_error_to_llm(self, tmp_path: Path) -> None:
        from huxley_skill_comms_telegram.transport import TransportError

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.raise_on_place_call = TransportError("simulated failure")
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)

        result = await skill.handle("call_contact", {"name": "hija"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "simulated failure" in payload["error"]
        assert result.side_effect is None

    @pytest.mark.asyncio
    async def test_empty_name_returns_error(self, tmp_path: Path) -> None:
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)

        import json

        for bad in ["", "   ", None, 123]:
            result = await skill.handle("call_contact", {"name": bad})
            assert json.loads(result.output)["ok"] is False

    @pytest.mark.asyncio
    async def test_claim_end_hangs_up_transport(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)
        result = await skill.handle("call_contact", {"name": "hija"})

        assert isinstance(result.side_effect, InputClaim)
        transport = captured[0]
        assert transport.ended_calls == 0

        import asyncio

        await result.side_effect.on_claim_end(ClaimEndReason.USER_PTT)
        await asyncio.sleep(0)
        assert transport.ended_calls == 1

    @pytest.mark.asyncio
    async def test_on_mic_frame_forwards_to_transport(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)
        result = await skill.handle("call_contact", {"name": "hija"})
        assert isinstance(result.side_effect, InputClaim)

        transport = captured[0]
        await result.side_effect.on_mic_frame(b"\x00\x01" * 240)
        await result.side_effect.on_mic_frame(b"\x02\x03" * 240)
        assert len(transport.mic_frames) == 2


class TestInbound:
    def _inbound_config(self, **extra: Any) -> dict[str, Any]:
        return {
            "api_id": 1,
            "api_hash": "x",
            "contacts": {"mario": "+573153283397", "hija": "+573186851696"},
            "inbound": {"enabled": True, "auto_answer": "contacts_only"},
            **extra,
        }

    @pytest.mark.asyncio
    async def test_inbound_disabled_by_default(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)

        # No eager connect without inbound.
        assert captured[0].connected is False
        # Only call_contact tool -- no inbound tools.
        assert [t.name for t in skill.tools] == ["call_contact"]

    @pytest.mark.asyncio
    async def test_inbound_enabled_connects_eagerly_at_setup(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 111, "+573186851696": 222}
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path)
        await skill.setup(ctx)

        assert captured[0].connected is True
        # Reverse map built from contacts.
        assert skill._user_id_to_name == {111: "mario", 222: "hija"}  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_inbound_callbacks_wired_to_transport(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path)
        await skill.setup(ctx)

        transport = captured[0]
        assert transport.on_incoming_ring is not None
        assert transport.on_ring_cancelled is not None

    @pytest.mark.asyncio
    async def test_ring_known_contact_announces_then_accepts_and_bridges(
        self, tmp_path: Path
    ) -> None:
        captured: list[StubTransport] = []
        accept_order: list[str] = []  # tracks interleaving of turns vs accepts

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 111, "+573186851696": 222}
            captured.append(t)
            return t

        turns: list[str] = []
        claims: list[InputClaim] = []

        # Patch inject_turn to record ordering relative to accept_call.
        base_ctx, _ = _build_ctx(
            self._inbound_config(), tmp_path, captured_turns=turns, captured_claims=claims
        )

        original_inject = base_ctx.inject_turn

        async def ordered_inject(prompt: str) -> None:
            accept_order.append("turn")
            await original_inject(prompt)

        import dataclasses

        ctx = dataclasses.replace(base_ctx, inject_turn=ordered_inject)

        original_accept = StubTransport.accept_call

        async def ordered_accept(self_t: StubTransport, user_id: int) -> None:
            accept_order.append("accept")
            await original_accept(self_t, user_id)

        skill = CommsTelegramSkill(transport_factory=factory)
        await skill.setup(ctx)

        # Monkey-patch accepted transport to track order.
        transport = captured[0]
        transport.accept_call = lambda uid: ordered_accept(transport, uid)  # type: ignore[method-assign]

        await skill._on_incoming_ring(111)  # type: ignore[attr-defined]

        # Announcement fires BEFORE accept (announce-before-accept ordering).
        assert accept_order == ["turn", "accept"], (
            f"expected turn before accept, got {accept_order}"
        )
        assert transport.accepted_calls == [111]
        assert len(turns) == 1
        assert "mario" in turns[0].lower()
        # Audio bridge started via start_input_claim.
        assert len(claims) == 1
        assert isinstance(claims[0], InputClaim)

    @pytest.mark.asyncio
    async def test_ring_unknown_caller_rejected_when_contacts_only(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        turns: list[str] = []
        claims: list[InputClaim] = []
        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            self._inbound_config(), tmp_path, captured_turns=turns, captured_claims=claims
        )
        await skill.setup(ctx)

        # Unknown user_id (not in reverse map).
        await skill._on_incoming_ring(999)  # type: ignore[attr-defined]

        assert captured[0].rejected_calls == [999]
        assert len(turns) == 0
        assert len(claims) == 0

    @pytest.mark.asyncio
    async def test_ring_unknown_accepted_when_auto_answer_all(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        turns: list[str] = []
        claims: list[InputClaim] = []
        cfg = self._inbound_config()
        cfg["inbound"]["auto_answer"] = "all"
        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(cfg, tmp_path, captured_turns=turns, captured_claims=claims)
        await skill.setup(ctx)

        await skill._on_incoming_ring(999)  # type: ignore[attr-defined]

        # Call accepted and bridged even for unknown caller.
        assert captured[0].accepted_calls == [999]
        assert len(turns) == 1
        assert len(claims) == 1

    @pytest.mark.asyncio
    async def test_ring_rejected_when_call_already_active(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 111, "+573186851696": 222}
            t._active_user_id = 222  # simulate active call
            captured.append(t)
            return t

        turns: list[str] = []
        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path, captured_turns=turns)
        await skill.setup(ctx)

        await skill._on_incoming_ring(111)  # type: ignore[attr-defined]

        assert captured[0].rejected_calls == [111]
        assert len(turns) == 0

    @pytest.mark.asyncio
    async def test_ring_cancelled_injects_turn(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 111, "+573186851696": 222}
            captured.append(t)
            return t

        turns: list[str] = []
        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path, captured_turns=turns)
        await skill.setup(ctx)

        # Simulate ring cancelled (race window: caller hung up before accept completed).
        await skill._on_ring_cancelled(111)  # type: ignore[attr-defined]

        assert len(turns) == 1
        assert "mario" in turns[0].lower()

    @pytest.mark.asyncio
    async def test_ring_accept_error_injects_turn_and_no_claim(self, tmp_path: Path) -> None:
        from huxley_skill_comms_telegram.transport import TransportError

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 111}
            t.raise_on_accept_call = TransportError("accept failed")
            captured.append(t)
            return t

        turns: list[str] = []
        claims: list[InputClaim] = []
        cfg = self._inbound_config()
        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(cfg, tmp_path, captured_turns=turns, captured_claims=claims)
        await skill.setup(ctx)

        await skill._on_incoming_ring(111)  # type: ignore[attr-defined]

        # Announcement fires first, then error turn -- no bridge started.
        assert len(claims) == 0
        assert len(turns) == 2
        assert "mario" in turns[0].lower()  # announcement
        assert "fallo" in turns[1].lower()  # error

    @pytest.mark.asyncio
    async def test_inbound_reverse_map_soft_fails_unresolvable(self, tmp_path: Path) -> None:
        from huxley_skill_comms_telegram.transport import TransportError

        fail_count = 0

        class FailingStubTransport(StubTransport):
            async def resolve_contact(self, identifier: str) -> int:
                nonlocal fail_count
                if identifier == "+573153283397":
                    fail_count += 1
                    raise TransportError("PEER_ID_INVALID")
                return 222

        skill = CommsTelegramSkill(transport_factory=FailingStubTransport)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path)
        # Should not raise -- soft-fail per contact.
        await skill.setup(ctx)

        assert fail_count == 1
        # hija resolved, mario failed -- only hija in reverse map.
        assert 222 in skill._user_id_to_name  # type: ignore[attr-defined]
        assert skill._inbound_enabled is True  # type: ignore[attr-defined]


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_disconnects_transport(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = CommsTelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"x": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)
        await skill.teardown()
        assert captured[0].disconnected == 1


class TestTools:
    def test_tool_description_lists_contacts(self, tmp_path: Path) -> None:
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        # Before setup: empty list, inbound disabled.
        tools = skill.tools
        assert len(tools) == 1
        assert "(ninguno)" in tools[0].description

    @pytest.mark.asyncio
    async def test_tool_description_includes_contact_names_after_setup(
        self, tmp_path: Path
    ) -> None:
        skill = CommsTelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {
                "api_id": 1,
                "api_hash": "x",
                "contacts": {"Hija": "+1", "Hijo": "+2"},
            },
            tmp_path,
        )
        await skill.setup(ctx)
        tools = skill.tools
        assert "hija" in tools[0].description
        assert "hijo" in tools[0].description

    @pytest.mark.asyncio
    async def test_only_call_contact_tool_regardless_of_inbound(self, tmp_path: Path) -> None:
        # Inbound path uses start_input_claim directly -- no LLM tool needed.
        for inbound_enabled in (True, False):
            skill = CommsTelegramSkill(transport_factory=StubTransport)
            ctx, _ = _build_ctx(
                {
                    "api_id": 1,
                    "api_hash": "x",
                    "contacts": {"hija": "+1"},
                    "inbound": {"enabled": inbound_enabled},
                },
                tmp_path,
            )
            await skill.setup(ctx)
            tool_names = [t.name for t in skill.tools]
            assert tool_names == ["call_contact"]
