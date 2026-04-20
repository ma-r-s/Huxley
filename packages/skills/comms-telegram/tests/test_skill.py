"""Skill-level tests — tool dispatch, config parsing, claim wiring.

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
    InputClaim,
    SkillContext,
)
from huxley_skill_comms_telegram.skill import CommsTelegramSkill


class StubTransport:
    """In-memory stand-in for `TelegramTransport` — records calls,
    never touches the network. Injected via `transport_factory`.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_dir: Path,
        userbot_phone: str | None = None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = session_dir
        self.userbot_phone = userbot_phone
        self.connected = False
        self.placed_calls: list[int] = []
        self.mic_frames: list[bytes] = []
        self.ended_calls: int = 0
        self.disconnected: int = 0
        # Scripted behavior for tests to toggle.
        self.resolve_map: dict[str, int] = {}
        self.raise_on_place_call: Exception | None = None

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

    def send_pcm(self, pcm: bytes) -> None:
        self.mic_frames.append(pcm)

    async def peer_audio_chunks(self):  # type: ignore[no-untyped-def]
        # Empty iterator by default; a specific test can replace this.
        if False:
            yield b""

    async def end_call(self) -> None:
        self.ended_calls += 1

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


def _build_ctx(config: dict[str, Any], data_dir: Path) -> tuple[SkillContext, list[StubTransport]]:
    """Return (ctx, captured_transports). Tests inspect the captured
    stub after calling `setup()` to verify config was applied."""

    async def inject_turn(prompt: str) -> None:
        pass

    def background_task(name: str, fn: Any, **kwargs: Any) -> Any:
        raise AssertionError("comms_telegram shouldn't use background_task yet")

    async def start_input_claim(claim: InputClaim) -> Any:
        raise AssertionError("skill should use side_effect=InputClaim, not start_input_claim")

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
        # Missing contacts is a warning, not a failure — lets someone
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
        # Secrets should come from env when set, even if persona.yaml has
        # placeholder values — so a public repo can commit the skeleton
        # without leaking api_id/hash.
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
                "api_id": 111,  # config value — should be overridden
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
        # Persona.yaml sets NO credentials at all; env provides everything.
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
                    "broken": 1234,  # int, not string — dropped
                    "empty": "",  # falsy — dropped
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
        # Side effect MUST be None — we don't want to latch the mic for
        # a failed call setup (would block grandpa from doing anything
        # else).
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

        # Transport lifecycle: connected, resolved, dialed.
        assert transport.connected is True
        assert transport.placed_calls == [7392572538]

        # Tool output signals success to the LLM.
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["contact"] == "hija"

        # Side effect is an InputClaim wired to the transport.
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

        # Upper-case + whitespace should still match.
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

        # Simulate the framework firing on_claim_end.
        await result.side_effect.on_claim_end(ClaimEndReason.USER_PTT)
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
        # Before setup: empty list.
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
