"""Skill-level tests -- tool dispatch, config parsing, claim wiring.

Uses a stub transport so no pyrogram imports are needed.
"""

from __future__ import annotations

import asyncio
import json
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
from huxley_skill_telegram.skill import TelegramSkill


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
        on_message: Any = None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = session_dir
        self.userbot_phone = userbot_phone
        self.on_incoming_ring = on_incoming_ring
        self.on_ring_cancelled = on_ring_cancelled
        self.on_message = on_message
        self.connected = False
        self.placed_calls: list[int] = []
        self.accepted_calls: list[int] = []
        self.rejected_calls: list[int] = []
        self.mic_frames: list[bytes] = []
        self.ended_calls: int = 0
        self.disconnected: int = 0
        # Messaging.
        self.sent_texts: list[tuple[int, str]] = []
        self.raise_on_send_text: Exception | None = None
        # Scripted unread to return from fetch_unread; set per test.
        self.unread_to_return: list[Any] = []
        self.fetch_unread_calls: list[tuple[set[int], int, int]] = []
        self.raise_on_fetch_unread: Exception | None = None
        # Scripted behavior for tests to toggle.
        self.resolve_map: dict[str, int] = {}
        self.raise_on_place_call: Exception | None = None
        self.raise_on_accept_call: Exception | None = None
        self.raise_on_resolve_for: set[str] = set()
        # Simulate an active call for busy-rejection tests.
        self._active_user_id: int | None = None

    async def connect(self) -> None:
        self.connected = True

    async def resolve_contact(self, identifier: str) -> int:
        if identifier in self.raise_on_resolve_for:
            from huxley_skill_telegram.transport import TransportError

            raise TransportError(f"resolve failed for {identifier}")
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

    async def send_text(self, user_id: int, text: str) -> None:
        if self.raise_on_send_text is not None:
            raise self.raise_on_send_text
        self.sent_texts.append((user_id, text))

    @property
    def is_in_call(self) -> bool:
        return self._active_user_id is not None

    async def fetch_unread(
        self,
        whitelist_user_ids: set[int],
        *,
        since_seconds: int,
        max_messages: int,
    ) -> list[Any]:
        self.fetch_unread_calls.append((set(whitelist_user_ids), since_seconds, max_messages))
        if self.raise_on_fetch_unread is not None:
            raise self.raise_on_fetch_unread
        return list(self.unread_to_return)


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
    captured_inject_calls: list[dict[str, Any]] | None = None,
    captured_claims: list[InputClaim] | None = None,
) -> tuple[SkillContext, list[StubTransport]]:
    """Return (ctx, captured_transports). Tests inspect the captured
    stub after calling `setup()` to verify config was applied.

    `captured_turns` receives the prompt string from each inject_turn() call.
    `captured_inject_calls` receives the full kwargs (prompt, dedup_key,
    priority) so tests can assert on prioritization / dedup keys.
    `captured_claims` receives any start_input_claim() calls if provided.
    """
    _turns = captured_turns if captured_turns is not None else []
    _calls = captured_inject_calls if captured_inject_calls is not None else []
    _claims = captured_claims if captured_claims is not None else []

    async def inject_turn(prompt: str, **kwargs: Any) -> None:
        _turns.append(prompt)
        _calls.append({"prompt": prompt, **kwargs})

    async def inject_turn_and_wait(prompt: str, **kwargs: Any) -> None:
        _turns.append(prompt)
        _calls.append({"prompt": prompt, "wait": True, **kwargs})

    def background_task(name: str, fn: Any, **kwargs: Any) -> Any:
        raise AssertionError("telegram shouldn't use background_task yet")

    async def start_input_claim(claim: InputClaim) -> ClaimHandle:
        _claims.append(claim)

        async def _wait() -> ClaimEndReason:
            return ClaimEndReason.NATURAL

        return ClaimHandle(_cancel=lambda: None, _wait_end=_wait)

    async def cancel_active_claim() -> None:
        pass

    return SkillContext(  # type: ignore[call-arg]
        logger=structlog.get_logger().bind(skill="telegram"),
        storage=FakeStorage(),  # type: ignore[arg-type]
        persona_data_dir=data_dir,
        config=config,
        # Tests assert against the Spanish copy (historic assumption — the
        # contacts and phrasing are Spanish-first). Override in specific
        # i18n tests as needed.
        language="es",
        inject_turn=inject_turn,
        inject_turn_and_wait=inject_turn_and_wait,
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

        skill = TelegramSkill(transport_factory=factory)
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

        skill = TelegramSkill(transport_factory=StubTransport)
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
        skill = TelegramSkill(transport_factory=StubTransport)
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

        skill = TelegramSkill(transport_factory=factory)
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

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"contacts": {"x": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 77777777

    @pytest.mark.asyncio
    async def test_secrets_file_takes_precedence_over_env_and_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # values.json beats env vars (which beat persona.yaml).
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_ID", "22222222")
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "env_hash")
        monkeypatch.setenv("HUXLEY_TELEGRAM_USERBOT_PHONE", "+22222222")
        secrets_dir = tmp_path / "secrets" / "telegram"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "values.json").write_text(
            json.dumps(
                {
                    "api_id": "11111111",
                    "api_hash": "file_hash_wins",
                    "userbot_phone": "+11111111",
                }
            )
        )

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {
                "api_id": 33333333,
                "api_hash": "yaml_hash",
                "userbot_phone": "+33333333",
                "contacts": {"x": "+1"},
            },
            tmp_path,
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 11111111
        assert captured[0].api_hash == "file_hash_wins"
        assert captured[0].userbot_phone == "+11111111"

    @pytest.mark.asyncio
    async def test_secrets_file_only_also_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_ID", raising=False)
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_HASH", raising=False)
        monkeypatch.delenv("HUXLEY_TELEGRAM_USERBOT_PHONE", raising=False)
        secrets_dir = tmp_path / "secrets" / "telegram"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "values.json").write_text(
            json.dumps({"api_id": "55555555", "api_hash": "file_only_hash"})
        )

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx({"contacts": {"x": "+1"}}, tmp_path)
        await skill.setup(ctx)
        assert captured[0].api_id == 55555555
        assert captured[0].api_hash == "file_only_hash"

    @pytest.mark.asyncio
    async def test_malformed_secrets_file_falls_back_to_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A corrupted values.json must NOT break boot — it falls through to
        # env vars / persona.yaml. Recovery is "fix the file"; in the
        # meantime the running server stays up.
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_ID", "44444444")
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "env_after_corrupt")
        secrets_dir = tmp_path / "secrets" / "telegram"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "values.json").write_text("{not valid json")

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx({"contacts": {"x": "+1"}}, tmp_path)
        await skill.setup(ctx)
        assert captured[0].api_id == 44444444
        assert captured[0].api_hash == "env_after_corrupt"

    @pytest.mark.asyncio
    async def test_non_string_phone_values_dropped(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
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
        skill = TelegramSkill(transport_factory=StubTransport)
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

        skill = TelegramSkill(transport_factory=factory)
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
        skill = TelegramSkill(transport_factory=StubTransport)
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
        from huxley_skill_telegram.transport import TransportError

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.raise_on_place_call = TransportError("simulated failure")
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
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
        skill = TelegramSkill(transport_factory=StubTransport)
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

        skill = TelegramSkill(transport_factory=factory)
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

        skill = TelegramSkill(transport_factory=factory)
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

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)

        # No eager connect without inbound.
        assert captured[0].connected is False
        # Both outbound tools are always exposed (call + message); inbound
        # config only affects whether the skill listens for INCOMING_CALL
        # rings and inbound MessageHandler events, not the tool surface.
        assert sorted(t.name for t in skill.tools) == ["call_contact", "send_message"]

    @pytest.mark.asyncio
    async def test_inbound_enabled_connects_eagerly_at_setup(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 111, "+573186851696": 222}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
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

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path)
        await skill.setup(ctx)

        transport = captured[0]
        assert transport.on_incoming_ring is not None
        assert transport.on_ring_cancelled is not None

    @pytest.mark.asyncio
    async def test_ring_known_contact_accepts_then_announces_and_bridges(
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

        # Patch inject_turn_and_wait to record ordering relative to accept_call.
        base_ctx, _ = _build_ctx(
            self._inbound_config(), tmp_path, captured_turns=turns, captured_claims=claims
        )

        original_inject_and_wait = base_ctx.inject_turn_and_wait

        async def ordered_inject_and_wait(prompt: str) -> None:
            accept_order.append("turn")
            await original_inject_and_wait(prompt)

        import dataclasses

        ctx = dataclasses.replace(base_ctx, inject_turn_and_wait=ordered_inject_and_wait)

        original_accept = StubTransport.accept_call

        async def ordered_accept(self_t: StubTransport, user_id: int) -> None:
            accept_order.append("accept")
            await original_accept(self_t, user_id)

        skill = TelegramSkill(transport_factory=factory)
        await skill.setup(ctx)

        # Monkey-patch accepted transport to track order.
        transport = captured[0]
        transport.accept_call = lambda uid: ordered_accept(transport, uid)  # type: ignore[method-assign]

        await skill._on_incoming_ring(111)  # type: ignore[attr-defined]

        # Accept fires BEFORE announcement (accept-first ordering preserves WebRTC quality).
        assert accept_order == ["accept", "turn"], (
            f"expected accept before turn, got {accept_order}"
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
        skill = TelegramSkill(transport_factory=factory)
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
        skill = TelegramSkill(transport_factory=factory)
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
        skill = TelegramSkill(transport_factory=factory)
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
        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path, captured_turns=turns)
        await skill.setup(ctx)

        # Simulate ring cancelled (race window: caller hung up before accept completed).
        await skill._on_ring_cancelled(111)  # type: ignore[attr-defined]

        assert len(turns) == 1
        assert "mario" in turns[0].lower()

    @pytest.mark.asyncio
    async def test_ring_accept_error_injects_turn_and_no_claim(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import TransportError

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
        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(cfg, tmp_path, captured_turns=turns, captured_claims=claims)
        await skill.setup(ctx)

        await skill._on_incoming_ring(111)  # type: ignore[attr-defined]

        # Accept fails before announcement -- only the error turn fires, no bridge.
        assert len(claims) == 0
        assert len(turns) == 1
        assert "falló" in turns[0].lower()  # error

    @pytest.mark.asyncio
    async def test_inbound_reverse_map_soft_fails_unresolvable(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import TransportError

        fail_count = 0

        class FailingStubTransport(StubTransport):
            async def resolve_contact(self, identifier: str) -> int:
                nonlocal fail_count
                if identifier == "+573153283397":
                    fail_count += 1
                    raise TransportError("PEER_ID_INVALID")
                return 222

        skill = TelegramSkill(transport_factory=FailingStubTransport)
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

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"api_id": 1, "api_hash": "x", "contacts": {"x": "+1"}},
            tmp_path,
        )
        await skill.setup(ctx)
        await skill.teardown()
        assert captured[0].disconnected == 1


class TestTools:
    def test_tool_descriptions_list_contacts(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        # Before setup: empty list, inbound disabled. Both tools are exposed
        # and both should mention the (empty) contact catalog so the LLM can
        # tell the user nothing is configured. Default language is English
        # pre-setup (`SkillContext.language` default) so we assert on the
        # English sentinel; per-language behavior is covered elsewhere.
        tools = {t.name: t for t in skill.tools}
        assert set(tools) == {"call_contact", "send_message"}
        for tool in tools.values():
            assert "(none)" in tool.description

    @pytest.mark.asyncio
    async def test_tool_descriptions_include_contact_names_after_setup(
        self, tmp_path: Path
    ) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(
            {
                "api_id": 1,
                "api_hash": "x",
                "contacts": {"Hija": "+1", "Hijo": "+2"},
            },
            tmp_path,
        )
        await skill.setup(ctx)
        tools = {t.name: t for t in skill.tools}
        for tool in tools.values():
            assert "hija" in tool.description
            assert "hijo" in tool.description

    @pytest.mark.asyncio
    async def test_tool_surface_independent_of_inbound_flag(self, tmp_path: Path) -> None:
        # Inbound path uses start_input_claim + a Pyrogram MessageHandler
        # directly -- no LLM tool needed for the receive side. Both outbound
        # tools are advertised regardless of the inbound flag.
        for inbound_enabled in (True, False):
            skill = TelegramSkill(transport_factory=StubTransport)
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
            assert sorted(t.name for t in skill.tools) == ["call_contact", "send_message"]


# ---------- Messaging: outbound send_message tool ----------


class TestSendMessageTool:
    @staticmethod
    def _config(extra_contacts: dict[str, str] | None = None) -> dict[str, Any]:
        contacts = {"hija": "+57 318 685 1696", "hijo": "+573001234567"}
        if extra_contacts:
            contacts.update(extra_contacts)
        return {"api_id": 1, "api_hash": "x", "contacts": contacts}

    @pytest.mark.asyncio
    async def test_happy_path_sends_text_and_returns_metadata(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573186851696": 222}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)

        result = await skill.handle("send_message", {"name": "hija", "text": "te quiero"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["contact"] == "hija"
        assert payload["chars"] == len("te quiero")
        assert "sent_at" in payload
        assert captured[0].sent_texts == [(222, "te quiero")]
        # No InputClaim side-effect for messaging.
        assert result.side_effect is None

    @pytest.mark.asyncio
    async def test_unknown_contact_returns_error(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)

        result = await skill.handle("send_message", {"name": "vecino", "text": "hola"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "vecino" in payload["error"]
        # The error names known contacts so the LLM can offer alternatives.
        assert "hija" in payload["error"]
        assert "hijo" in payload["error"]

    @pytest.mark.asyncio
    async def test_empty_text_returns_error_without_dispatch(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)

        result = await skill.handle("send_message", {"name": "hija", "text": "   "})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "vacío" in payload["error"]
        assert captured[0].sent_texts == []

    @pytest.mark.asyncio
    async def test_text_above_4096_chars_returns_error(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)

        long_text = "a" * 4097
        result = await skill.handle("send_message", {"name": "hija", "text": long_text})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "4097" in payload["error"] or "muy largo" in payload["error"]
        assert captured[0].sent_texts == []

    @pytest.mark.asyncio
    async def test_transport_error_surfaces_as_llm_error(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import TransportError

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.raise_on_send_text = TransportError("USER_DEACTIVATED")
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)

        result = await skill.handle("send_message", {"name": "hija", "text": "hola"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "hija" in payload["error"]
        assert "USER_DEACTIVATED" in payload["error"]

    @pytest.mark.asyncio
    async def test_unconfigured_transport_returns_friendly_error(self, tmp_path: Path) -> None:
        # No api_id/api_hash -> transport stays None -> tool says "no
        # configurado" instead of crashing.
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx({"contacts": {"hija": "+1"}}, tmp_path)
        await skill.setup(ctx)

        result = await skill.handle("send_message", {"name": "hija", "text": "hola"})
        import json

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "configurad" in payload["error"].lower()


# ---------- Messaging: inbound MessageHandler + debounce + backfill ----------


class TestInboundMessages:
    @staticmethod
    def _inbound_config(**overrides: Any) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "api_id": 1,
            "api_hash": "x",
            "userbot_phone": "+573153283397",
            "contacts": {"hija": "+57 318 685 1696", "hijo": "+57 300 123 4567"},
            "inbound": {
                "enabled": True,
                "auto_answer": "contacts_only",
                # Short debounce keeps tests fast without sacrificing realism.
                "debounce_seconds": 0.05,
                # Disable backfill by default; specific tests opt-in.
                "backfill_hours": 0,
            },
        }
        cfg["inbound"].update(overrides)
        return cfg

    @pytest.mark.asyncio
    async def test_setup_wires_on_message_callback_when_inbound_enabled(
        self, tmp_path: Path
    ) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path)
        await skill.setup(ctx)
        assert captured[0].on_message is not None

    @pytest.mark.asyncio
    async def test_setup_does_not_wire_on_message_when_inbound_disabled(
        self, tmp_path: Path
    ) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx({"api_id": 1, "api_hash": "x", "contacts": {"hija": "+1"}}, tmp_path)
        await skill.setup(ctx)
        # Outbound-only setup -- no message handler wired.
        assert captured[0].on_message is None

    @pytest.mark.asyncio
    async def test_known_contact_message_fires_inject_after_debounce(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        turns: list[str] = []
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(
            self._inbound_config(),
            tmp_path,
            captured_turns=turns,
            captured_inject_calls=calls,
        )
        await skill.setup(ctx)

        msg = InboundMessage(
            user_id=222, sender_display="Maria Lopez", text="hola papa", timestamp=0
        )
        await captured[0].on_message(msg)

        # Wait past the debounce window.
        await asyncio.sleep(0.15)

        assert len(calls) == 1
        assert "hija" in calls[0]["prompt"]
        assert "hola papa" in calls[0]["prompt"]
        # Coalesce dedup_key + NORMAL priority (queues behind active calls).
        assert calls[0]["dedup_key"] == "msg_burst:222"
        from huxley_sdk import InjectPriority

        assert calls[0]["priority"] == InjectPriority.NORMAL

    @pytest.mark.asyncio
    async def test_burst_from_same_contact_coalesces_into_one_inject(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)

        for text in ("hola", "papa", "estas?"):
            await captured[0].on_message(
                InboundMessage(user_id=222, sender_display="hija", text=text, timestamp=0)
            )
            await asyncio.sleep(0.01)  # well within debounce

        await asyncio.sleep(0.15)
        # All three messages collapse into one inject.
        assert len(calls) == 1
        prompt = calls[0]["prompt"]
        assert "hola" in prompt
        assert "papa" in prompt
        assert "estas?" in prompt
        # 3 messages -> "X te envió 3 mensajes" phrasing.
        assert "3 mensajes" in prompt

    @pytest.mark.asyncio
    async def test_unknown_sender_dropped_by_default(self, tmp_path: Path) -> None:
        # Default policy mirrors `auto_answer: contacts_only` for calls --
        # spam vector mitigation. Unknown senders silently log and drop.
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        # Inbound config without unknown_messages override -> drop.
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)

        msg = InboundMessage(user_id=999, sender_display="Random", text="spam", timestamp=0)
        await captured[0].on_message(msg)
        await asyncio.sleep(0.15)

        assert calls == []

    @pytest.mark.asyncio
    async def test_unknown_sender_announced_when_opted_in(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(
            self._inbound_config(unknown_messages="announce"),
            tmp_path,
            captured_inject_calls=calls,
        )
        await skill.setup(ctx)

        msg = InboundMessage(user_id=999, sender_display="Random Person", text="hola", timestamp=0)
        await captured[0].on_message(msg)
        await asyncio.sleep(0.15)

        assert len(calls) == 1
        assert "desconocido" in calls[0]["prompt"]
        assert "hola" in calls[0]["prompt"]
        assert calls[0]["dedup_key"] == "msg_burst:999"

    @pytest.mark.asyncio
    async def test_messages_from_two_senders_fire_two_independent_injects(
        self, tmp_path: Path
    ) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)

        await captured[0].on_message(
            InboundMessage(user_id=222, sender_display="hija", text="hola", timestamp=0)
        )
        await captured[0].on_message(
            InboundMessage(user_id=333, sender_display="hijo", text="papa", timestamp=0)
        )
        await asyncio.sleep(0.15)

        assert len(calls) == 2
        prompts_by_dedup = {c["dedup_key"]: c["prompt"] for c in calls}
        assert "hija" in prompts_by_dedup["msg_burst:222"]
        assert "hijo" in prompts_by_dedup["msg_burst:333"]

    @pytest.mark.asyncio
    async def test_teardown_flushes_pending_burst(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        # Long debounce so the timer never fires on its own during this test.
        cfg = self._inbound_config(debounce_seconds=10.0)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(cfg, tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)

        await captured[0].on_message(
            InboundMessage(user_id=222, sender_display="hija", text="urgente", timestamp=0)
        )
        # Without flush_all, this would silently drop the message at shutdown.
        await skill.teardown()

        assert len(calls) == 1
        assert "urgente" in calls[0]["prompt"]


class TestBackfill:
    @staticmethod
    def _config(**inbound_overrides: Any) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "api_id": 1,
            "api_hash": "x",
            "userbot_phone": "+573153283397",
            "contacts": {"hija": "+57 318 685 1696", "hijo": "+57 300 123 4567"},
            "inbound": {
                "enabled": True,
                "auto_answer": "contacts_only",
                "debounce_seconds": 0.05,
                "backfill_hours": 6,
                "backfill_max": 50,
            },
        }
        cfg["inbound"].update(inbound_overrides)
        return cfg

    @pytest.fixture(autouse=True)
    def _fast_backfill_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The skill waits 5s after setup() before firing the backfill so the
        # OpenAI session has time to connect; tests don't need that delay.
        monkeypatch.setattr(TelegramSkill, "_BACKFILL_STARTUP_DELAY_S", 0.0)

    @pytest.mark.asyncio
    async def test_backfill_fires_summary_inject_for_unread(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            t.unread_to_return = [
                InboundMessage(user_id=222, sender_display="hija", text="hola", timestamp=100),
                InboundMessage(user_id=222, sender_display="hija", text="estas?", timestamp=200),
                InboundMessage(user_id=333, sender_display="hijo", text="papa", timestamp=150),
            ]
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(self._config(), tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)

        # Backfill is fire-and-forget; let it complete.
        await asyncio.sleep(0.1)

        # One coalesced backfill inject -- not three separate ones.
        backfill_calls = [c for c in calls if "Mientras estabas desconectado" in c["prompt"]]
        assert len(backfill_calls) == 1
        prompt = backfill_calls[0]["prompt"]
        # Both senders mentioned by name AND the message bodies are present
        # verbatim -- if the prompt only had counts, the LLM would have
        # nothing to read when the user says "yes please read them."
        assert "hija" in prompt and "hijo" in prompt
        assert "'hola'" in prompt and "'estas?'" in prompt and "'papa'" in prompt
        # Backfill is single-shot; no dedup_key needed.
        assert "dedup_key" not in backfill_calls[0]

        # Window applied: 6 hours -> 21600s.
        assert captured[0].fetch_unread_calls
        whitelist, since_seconds, max_messages = captured[0].fetch_unread_calls[0]
        assert since_seconds == 6 * 3600
        assert max_messages == 50
        # Whitelist contains the resolved user_ids of all configured contacts.
        assert whitelist == {222, 333}

    @pytest.mark.asyncio
    async def test_backfill_no_unread_does_not_inject(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            t.unread_to_return = []
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(self._config(), tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)
        await asyncio.sleep(0.1)

        # No backfill inject when nothing was unread.
        assert [c for c in calls if "Mientras estabas desconectado" in c["prompt"]] == []

    @pytest.mark.asyncio
    async def test_backfill_disabled_when_hours_is_zero(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            t.unread_to_return = [
                InboundMessage(user_id=222, sender_display="hija", text="hola", timestamp=0),
            ]
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        cfg = self._config(backfill_hours=0)
        ctx, _ = _build_ctx(cfg, tmp_path, captured_inject_calls=calls)
        await skill.setup(ctx)
        await asyncio.sleep(0.1)

        # Backfill skipped -> fetch_unread never called -> no inject.
        assert captured[0].fetch_unread_calls == []
        assert [c for c in calls if "Mientras estabas desconectado" in c["prompt"]] == []

    @pytest.mark.asyncio
    async def test_backfill_fetch_failure_swallowed(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573153283397": 1, "+573186851696": 222, "+573001234567": 333}
            t.raise_on_fetch_unread = RuntimeError("network down")
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        calls: list[dict[str, Any]] = []
        ctx, _ = _build_ctx(self._config(), tmp_path, captured_inject_calls=calls)
        # A failing backfill must not propagate out of setup() -- a flaky
        # network on boot can't take the persona down.
        await skill.setup(ctx)
        await asyncio.sleep(0.1)
        assert [c for c in calls if "Mientras estabas desconectado" in c["prompt"]] == []
