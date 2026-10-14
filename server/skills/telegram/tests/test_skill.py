"""Skill-level tests -- tool dispatch, config parsing, claim wiring.

Uses a stub transport so no pyrogram imports are needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
import structlog

if TYPE_CHECKING:
    from pathlib import Path

from huxley_sdk import (
    ClaimEndReason,
    ClaimHandle,
    InjectOutcome,
    InjectPriority,
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

    async def resolve_cached(self, identifier: str) -> int | None:
        return self.resolve_map.get(identifier)

    async def resolve_contact(self, identifier: str, name: str | None = None) -> int:
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


class FakeSecrets:
    """In-memory SkillSecrets for tests. The telegram skill reads
    creds via ``await ctx.secrets.get(...)`` (T2.9); tests populate
    this fake's ``_data`` to drive the same paths."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def keys(self) -> list[str]:
        return sorted(self._data.keys())


def _build_ctx(
    config: dict[str, Any],
    data_dir: Path,
    *,
    captured_turns: list[str] | None = None,
    captured_inject_calls: list[dict[str, Any]] | None = None,
    captured_claims: list[InputClaim] | None = None,
    secrets: dict[str, str] | None = None,
) -> tuple[SkillContext, list[StubTransport]]:
    """Return (ctx, captured_transports). Tests inspect the captured
    stub after calling `setup()` to verify config was applied.

    `captured_turns` receives the prompt string from each inject_turn() call.
    `captured_inject_calls` receives the full kwargs (prompt, dedup_key,
    priority) so tests can assert on prioritization / dedup keys.
    `captured_claims` receives any start_input_claim() calls if provided.
    `secrets` pre-populates the FakeSecrets so post-T2.9 tests can drive
    `ctx.secrets.get(...)` returns directly.
    """
    _turns = captured_turns if captured_turns is not None else []
    _calls = captured_inject_calls if captured_inject_calls is not None else []
    _claims = captured_claims if captured_claims is not None else []

    async def inject_turn(prompt: str, **kwargs: Any) -> None:
        _turns.append(prompt)
        _calls.append({"prompt": prompt, **kwargs})

    async def inject_turn_and_wait(prompt: str, **kwargs: Any) -> InjectOutcome:
        _turns.append(prompt)
        _calls.append({"prompt": prompt, "wait": True, **kwargs})
        return InjectOutcome.DELIVERED

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
        secrets=FakeSecrets(secrets),  # type: ignore[arg-type]
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
                "userbot_phone": "+573000000073",
                "contacts": {
                    "Hija": "+57 300 000 0078",
                    "Hijo": "+573001234567",
                },
            },
            tmp_path,
        )
        await skill.setup(ctx)

        # Names lowercased, phones normalized.
        assert skill._contacts == {  # type: ignore[attr-defined]
            "hija": "+573000000078",
            "hijo": "+573001234567",
        }
        assert len(captured) == 1
        assert captured[0].api_id == 12345678
        assert captured[0].userbot_phone == "+573000000073"
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
    async def test_ctx_secrets_takes_precedence_over_env_and_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ctx.secrets values beat env vars (which beat persona.yaml).
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_ID", "22222222")
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "env_hash")
        monkeypatch.setenv("HUXLEY_TELEGRAM_USERBOT_PHONE", "+22222222")

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
            secrets={
                "api_id": "11111111",
                "api_hash": "file_hash_wins",
                "userbot_phone": "+11111111",
            },
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 11111111
        assert captured[0].api_hash == "file_hash_wins"
        assert captured[0].userbot_phone == "+11111111"

    @pytest.mark.asyncio
    async def test_ctx_secrets_only_also_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_ID", raising=False)
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_HASH", raising=False)
        monkeypatch.delenv("HUXLEY_TELEGRAM_USERBOT_PHONE", raising=False)

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"contacts": {"x": "+1"}},
            tmp_path,
            secrets={"api_id": "55555555", "api_hash": "file_only_hash"},
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 55555555
        assert captured[0].api_hash == "file_only_hash"

    @pytest.mark.asyncio
    async def test_empty_ctx_secrets_falls_back_to_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When ctx.secrets has no values for these keys (the post-T2.9
        # equivalent of "no values.json file" or "values.json missing
        # the key"), the chain falls through to env vars without
        # erroring. Recovery for a corrupt values.json is handled at
        # the JsonFileSecrets layer (see runtime test_secrets.py); from
        # the skill's perspective, the only signal is a None return.
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_ID", "66666666")
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "env_no_secrets")

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx({"contacts": {"x": "+1"}}, tmp_path)
        await skill.setup(ctx)
        assert captured[0].api_id == 66666666
        assert captured[0].api_hash == "env_no_secrets"

    @pytest.mark.asyncio
    async def test_partial_ctx_secrets_merges_per_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ctx.secrets supplies only api_id; api_hash + userbot_phone
        # come from env. The priority chain fires per-field, not
        # whole-dict.
        monkeypatch.setenv("HUXLEY_TELEGRAM_API_HASH", "env_only_hash")
        monkeypatch.setenv("HUXLEY_TELEGRAM_USERBOT_PHONE", "+19999999999")
        monkeypatch.delenv("HUXLEY_TELEGRAM_API_ID", raising=False)

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {"contacts": {"x": "+1"}},
            tmp_path,
            secrets={"api_id": "12121212"},
        )
        await skill.setup(ctx)
        assert captured[0].api_id == 12121212
        assert captured[0].api_hash == "env_only_hash"
        assert captured[0].userbot_phone == "+19999999999"

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

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "cousin_bob" in payload["error"]
        assert result.side_effect is None

    @pytest.mark.asyncio
    async def test_happy_path_dials_and_returns_input_claim(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000078": 1000000081}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(
            {
                "api_id": 1,
                "api_hash": "x",
                "contacts": {"hija": "+57 300 000 0078"},
            },
            tmp_path,
        )
        await skill.setup(ctx)

        result = await skill.handle("call_contact", {"name": "hija"})
        transport = captured[0]

        assert transport.connected is True
        assert transport.placed_calls == [1000000081]

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
            "contacts": {"mario": "+573000000073", "hija": "+573000000078"},
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
        # All six tools are always exposed; inbound config only affects
        # whether the skill listens for INCOMING_CALL rings and inbound
        # MessageHandler events, not the tool surface.
        assert sorted(t.name for t in skill.tools) == [
            "add_contact",
            "call_contact",
            "list_contacts",
            "my_phone_number",
            "remove_contact",
            "send_message",
        ]

    @pytest.mark.asyncio
    async def test_inbound_enabled_connects_eagerly_at_setup(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000073": 111, "+573000000078": 222}
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
            t.resolve_map = {"+573000000073": 111, "+573000000078": 222}
            captured.append(t)
            return t

        turns: list[str] = []
        claims: list[InputClaim] = []

        # Patch inject_turn_and_wait to record ordering relative to accept_call.
        base_ctx, _ = _build_ctx(
            self._inbound_config(), tmp_path, captured_turns=turns, captured_claims=claims
        )

        original_inject_and_wait = base_ctx.inject_turn_and_wait

        async def ordered_inject_and_wait(prompt: str, **kwargs: Any) -> InjectOutcome:
            accept_order.append("turn")
            return await original_inject_and_wait(prompt, **kwargs)

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
        # A fixed line and no tool: "Llamada de mario, contestando." once
        # made the model call mario back while the caller waited in silence.
        assert "«Te llama" in turns[0]
        assert "No llames a ninguna herramienta" in turns[0]
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
            t.resolve_map = {"+573000000073": 111, "+573000000078": 222}
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
            t.resolve_map = {"+573000000073": 111, "+573000000078": 222}
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
            t.resolve_map = {"+573000000073": 111}
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
    async def test_a_rate_limit_still_recognises_cached_contacts(self, tmp_path: Path) -> None:
        """FLOOD_WAIT on a lookup (2696 s after a few restarts, 2026-10-14):
        no more lookups, but a contact the cache already knows (Mario) is
        still recognised, or contacts_only would refuse his calls."""
        from huxley_skill_telegram.transport import TransportRateLimitedError

        tried: list[str] = []

        class LimitedStubTransport(StubTransport):
            async def resolve_contact(self, identifier: str, name: str | None = None) -> int:
                tried.append(identifier)
                raise TransportRateLimitedError("wait", 2696)

            async def resolve_cached(self, identifier: str) -> int | None:
                return {"+573000000078": 222}.get(identifier)

        skill = TelegramSkill(transport_factory=LimitedStubTransport)
        ctx, _ = _build_ctx(self._inbound_config(), tmp_path)
        await skill.setup(ctx)

        assert len(tried) == 1  # one lookup, then the cache only
        assert skill._user_id_to_name == {222: "hija"}  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_inbound_reverse_map_soft_fails_unresolvable(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import TransportError

        fail_count = 0

        class FailingStubTransport(StubTransport):
            async def resolve_contact(self, identifier: str, name: str | None = None) -> int:
                nonlocal fail_count
                if identifier == "+573000000073":
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
        assert set(tools) == {
            "call_contact",
            "send_message",
            "add_contact",
            "remove_contact",
            "list_contacts",
            "my_phone_number",
        }
        # Tools that mention the contact list should show the empty sentinel.
        for name in ("call_contact", "send_message", "add_contact", "remove_contact"):
            assert "(none)" in tools[name].description

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
        # Tools that embed the contacts list should show both names.
        # list_contacts has a static description with no contact list.
        for name in ("call_contact", "send_message", "add_contact", "remove_contact"):
            assert "hija" in tools[name].description
            assert "hijo" in tools[name].description

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
            assert sorted(t.name for t in skill.tools) == [
                "add_contact",
                "call_contact",
                "list_contacts",
                "my_phone_number",
                "remove_contact",
                "send_message",
            ]


# ---------- Messaging: outbound send_message tool ----------


class TestSendMessageTool:
    @staticmethod
    def _config(extra_contacts: dict[str, str] | None = None) -> dict[str, Any]:
        contacts = {"hija": "+57 300 000 0078", "hijo": "+573001234567"}
        if extra_contacts:
            contacts.update(extra_contacts)
        return {"api_id": 1, "api_hash": "x", "contacts": contacts}

    @pytest.mark.asyncio
    async def test_happy_path_sends_text_and_returns_metadata(self, tmp_path: Path) -> None:
        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000078": 222}
            captured.append(t)
            return t

        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)

        result = await skill.handle("send_message", {"name": "hija", "text": "te quiero"})

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
            "userbot_phone": "+573000000073",
            "contacts": {"hija": "+57 300 000 0078", "hijo": "+57 300 123 4567"},
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
        # NORMAL priority (queues behind active calls), awaited until heard
        # with no expiry and no dedup key (T1.44: a later burst under the
        # same key would replace this one unheard).
        assert calls[0]["priority"] == InjectPriority.NORMAL
        assert calls[0]["wait"] is True
        assert calls[0]["expires_after"] is None
        assert "dedup_key" not in calls[0]

    @pytest.mark.asyncio
    async def test_burst_from_same_contact_coalesces_into_one_inject(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
        assert "dedup_key" not in calls[0]

    @pytest.mark.asyncio
    async def test_messages_from_two_senders_fire_two_independent_injects(
        self, tmp_path: Path
    ) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
        prompts = sorted(c["prompt"] for c in calls)
        assert any("hija" in p for p in prompts)
        assert any("hijo" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_teardown_flushes_pending_burst(self, tmp_path: Path) -> None:
        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
            "userbot_phone": "+573000000073",
            "contacts": {"hija": "+57 300 000 0078", "hijo": "+57 300 123 4567"},
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
        # Backfill is single-shot; no dedup_key needed. Unread messages
        # stay worth saying until heard (T1.44).
        assert "dedup_key" not in backfill_calls[0]
        assert backfill_calls[0]["expires_after"] is None

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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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
            t.resolve_map = {"+573000000073": 1, "+573000000078": 222, "+573001234567": 333}
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


# ---------------------------------------------------------------------------
# Contact management: add_contact / remove_contact / list_contacts
# ---------------------------------------------------------------------------

_BASIC_CFG = {
    "api_id": 12345678,
    "api_hash": "abcdef0123456789",
    "userbot_phone": "+573000000073",
    "contacts": {"mario": "+573000000078"},
}


class TestContactManagement:
    @pytest.mark.asyncio
    async def test_contacts_json_loaded_at_setup(self, tmp_path: Path) -> None:
        """contacts.json in data dir is merged on top of YAML contacts at setup."""
        import json

        (tmp_path / "contacts.json").write_text(
            json.dumps({"nieto": "+573001234567"}), encoding="utf-8"
        )
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        assert "mario" in skill._contacts  # type: ignore[attr-defined]
        assert "nieto" in skill._contacts  # type: ignore[attr-defined]
        assert skill._contacts["nieto"] == "+573001234567"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_contacts_json_wins_over_yaml(self, tmp_path: Path) -> None:
        """JSON contact for the same name overrides the YAML entry."""
        import json

        (tmp_path / "contacts.json").write_text(
            json.dumps({"mario": "+573999999999"}), encoding="utf-8"
        )
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        assert skill._contacts["mario"] == "+573999999999"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_add_contact_persists_to_json(self, tmp_path: Path) -> None:
        """add_contact writes the new contact to contacts.json."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        result = json.loads(
            (await skill.handle("add_contact", {"name": "Nieta", "phone": "+573001234567"})).output
        )
        assert result["ok"] is True
        assert result["action"] == "added"
        assert result["name"] == "nieta"
        assert result["phone"] == "+573001234567"

        # Contact is live immediately.
        assert skill._contacts["nieta"] == "+573001234567"  # type: ignore[attr-defined]

        # Persisted to JSON file.
        saved = json.loads((tmp_path / "contacts.json").read_text(encoding="utf-8"))
        assert saved["nieta"] == "+573001234567"

    @pytest.mark.asyncio
    async def test_add_contact_update_existing(self, tmp_path: Path) -> None:
        """Adding a contact that already exists reports action='updated'."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        result = json.loads(
            (await skill.handle("add_contact", {"name": "mario", "phone": "+573000000001"})).output
        )
        assert result["ok"] is True
        assert result["action"] == "updated"
        assert skill._contacts["mario"] == "+573000000001"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_add_contact_normalizes_phone(self, tmp_path: Path) -> None:
        """Spaces and dashes in the phone number are stripped; missing + is added."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        result = json.loads(
            (
                await skill.handle("add_contact", {"name": "vecina", "phone": "57 300 123 4567"})
            ).output
        )
        assert result["ok"] is True
        assert result["phone"] == "+573001234567"

    @pytest.mark.asyncio
    async def test_add_contact_invalid_phone(self, tmp_path: Path) -> None:
        """Clearly invalid phone strings return an LLM-facing error."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        result = json.loads(
            (await skill.handle("add_contact", {"name": "nadie", "phone": "abc"})).output
        )
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_remove_contact_json_contact(self, tmp_path: Path) -> None:
        """remove_contact removes a JSON-added contact permanently."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        await skill.handle("add_contact", {"name": "nieta", "phone": "+573001234567"})
        result = json.loads((await skill.handle("remove_contact", {"name": "nieta"})).output)
        assert result["ok"] is True
        assert "nieta" not in skill._contacts  # type: ignore[attr-defined]

        saved = json.loads((tmp_path / "contacts.json").read_text(encoding="utf-8"))
        assert "nieta" not in saved

    @pytest.mark.asyncio
    async def test_remove_contact_yaml_contact_warns(self, tmp_path: Path) -> None:
        """Removing a YAML-only contact succeeds in-memory but returns a note
        explaining it will be restored on restart."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        result = json.loads((await skill.handle("remove_contact", {"name": "mario"})).output)
        assert result["ok"] is True
        assert "mario" not in skill._contacts  # type: ignore[attr-defined]
        assert "note" in result  # restart-warning present

    @pytest.mark.asyncio
    async def test_remove_contact_unknown_name(self, tmp_path: Path) -> None:
        """Trying to remove a name not in the list returns an LLM-facing error."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        result = json.loads((await skill.handle("remove_contact", {"name": "fantasma"})).output)
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_list_contacts(self, tmp_path: Path) -> None:
        """list_contacts returns the current live contacts list."""
        import json

        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        await skill.handle("add_contact", {"name": "nieta", "phone": "+573001234567"})
        result = json.loads((await skill.handle("list_contacts", {})).output)
        assert result["ok"] is True
        assert "mario" in result["contacts"]
        assert "nieta" in result["contacts"]

    @pytest.mark.asyncio
    async def test_contacts_json_ignored_on_corrupt_file(self, tmp_path: Path) -> None:
        """A corrupt contacts.json is silently skipped; YAML contacts still load."""
        (tmp_path / "contacts.json").write_text("not json!!", encoding="utf-8")
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(_BASIC_CFG, tmp_path)
        await skill.setup(ctx)

        assert skill._contacts == {"mario": "+573000000078"}  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_add_contact_removes_reverse_map_on_update(self, tmp_path: Path) -> None:
        """Updating a contact whose user_id was resolved replaces the reverse-map entry."""
        import json

        captured: list[StubTransport] = []

        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000078": 111}
            captured.append(t)
            return t

        inbound_cfg = dict(_BASIC_CFG)
        inbound_cfg["inbound"] = {"enabled": True, "auto_answer": "contacts_only"}
        skill = TelegramSkill(transport_factory=factory)
        ctx, _ = _build_ctx(inbound_cfg, tmp_path)
        await skill.setup(ctx)
        await asyncio.sleep(0.1)

        # Resolve map is built — mario -> uid 111.
        assert skill._user_id_to_name.get(111) == "mario"  # type: ignore[attr-defined]

        # Now add a new contact; resolution works because stub has the mapping.
        captured[0].resolve_map["+573001234567"] = 222
        result = json.loads(
            (await skill.handle("add_contact", {"name": "nieta", "phone": "+573001234567"})).output
        )
        assert result["ok"] is True
        assert skill._user_id_to_name.get(222) == "nieta"  # type: ignore[attr-defined]


class TestDefaultCountryCode:
    """Persona-level `default_country_code` config — when set, bare digits
    sent to `add_contact` get the persona's country prefix prepended instead
    of just '+'. The LLM is told the default via the tool description so it
    can read back the full international number for confirmation."""

    def _cfg(self, default_cc: str | None = "+57") -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "api_id": 12345678,
            "api_hash": "abcdef0123456789",
            "userbot_phone": "+573000000073",
            "contacts": {"mario": "+573000000078"},
        }
        if default_cc is not None:
            cfg["default_country_code"] = default_cc
        return cfg

    @pytest.mark.asyncio
    async def test_default_country_code_loaded(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg("+57"), tmp_path)
        await skill.setup(ctx)
        assert skill._default_country_code == "+57"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_no_default_means_plain_plus_prefix(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg(default_cc=None), tmp_path)
        await skill.setup(ctx)
        assert skill._default_country_code is None  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_add_contact_prepends_default_country_code(self, tmp_path: Path) -> None:
        """Bare digits get the persona's country code prepended (not just '+').

        This is the bug the layered approach exists to fix: when the LLM forgets
        to include the country code in the read-back, the skill still does the
        right thing because it knows the persona's default.
        """
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg("+57"), tmp_path)
        await skill.setup(ctx)

        result = json.loads(
            (await skill.handle("add_contact", {"name": "patricia", "phone": "3000000076"})).output
        )
        assert result["ok"] is True
        assert result["phone"] == "+573000000076"  # +57 prepended, not just "+"

    @pytest.mark.asyncio
    async def test_add_contact_with_plus_but_local_digits_prepends_cc(
        self, tmp_path: Path
    ) -> None:
        """Defense in depth: LLM commonly produces '+<local digits>' without
        the country code. The skill detects 'short' numbers (<=10 digits) that
        don't start with the default country code and prepends it. This was
        the exact bug observed in smoke testing — LLM read back '+3104567891'
        for a Colombian number, missing the +57."""
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg("+57"), tmp_path)
        await skill.setup(ctx)

        result = json.loads(
            (
                await skill.handle("add_contact", {"name": "patricia", "phone": "+3104567891"})
            ).output
        )
        assert result["ok"] is True
        # '+3104567891' has only 10 digits and doesn't start with '57', so the
        # skill treats it as missing the country code and prepends +57.
        assert result["phone"] == "+573104567891"

    @pytest.mark.asyncio
    async def test_explicit_country_code_in_input_wins(self, tmp_path: Path) -> None:
        """When the user explicitly includes a country code, it overrides the default."""
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg("+57"), tmp_path)
        await skill.setup(ctx)

        # User-provided "+34..." is for Spain — must NOT get +57 prepended.
        result = json.loads(
            (await skill.handle("add_contact", {"name": "amiga", "phone": "+34612345678"})).output
        )
        assert result["ok"] is True
        assert result["phone"] == "+34612345678"

    @pytest.mark.asyncio
    async def test_no_default_bare_digits_get_plus_only(self, tmp_path: Path) -> None:
        """No persona default → fall back to the prior behavior (just prepend +).

        The validation regex then catches anything that doesn't look international.
        """
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg(default_cc=None), tmp_path)
        await skill.setup(ctx)

        # 12 digits is in-range for the E.164 regex (+\d{7,15}).
        result = json.loads(
            (await skill.handle("add_contact", {"name": "x", "phone": "573000000076"})).output
        )
        assert result["ok"] is True
        assert result["phone"] == "+573000000076"

    @pytest.mark.asyncio
    async def test_invalid_default_country_code_logs_and_ignores(self, tmp_path: Path) -> None:
        """Malformed default_country_code (no leading +, too many digits, etc.)
        is logged as a warning and falls back to None — the skill keeps working."""
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg("57"), tmp_path)  # missing +
        await skill.setup(ctx)
        assert skill._default_country_code is None  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_tool_description_advertises_default(self, tmp_path: Path) -> None:
        """The LLM sees the default country code in the add_contact description
        so it can read back the full international number for confirmation."""
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg("+57"), tmp_path)
        await skill.setup(ctx)

        add_tool = next(t for t in skill.tools if t.name == "add_contact")
        assert "+57" in add_tool.description

    @pytest.mark.asyncio
    async def test_tool_description_no_default_no_hint(self, tmp_path: Path) -> None:
        """No default → the country code hint clause is absent from the description."""
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._cfg(default_cc=None), tmp_path)
        await skill.setup(ctx)

        add_tool = next(t for t in skill.tools if t.name == "add_contact")
        # No "Default country code" / "Código de país" substring.
        desc_lower = add_tool.description.lower()
        assert "default country code" not in desc_lower
        assert "país por defecto" not in desc_lower


class TestWaitingForAListener:
    """T1.44: proactive turns wait for a client that can hear them."""

    _CONFIG: ClassVar[dict[str, Any]] = {
        "api_id": 1,
        "api_hash": "x",
        "userbot_phone": "+573000000073",
        "contacts": {"mario": "+573000000073", "hija": "+573000000081"},
        "inbound": {
            "enabled": True,
            "auto_answer": "contacts_only",
            "debounce_seconds": 0.05,
            "backfill_hours": 0,
        },
    }

    @staticmethod
    def _factory(captured: list[StubTransport]) -> Any:
        def factory(**kwargs: Any) -> StubTransport:
            t = StubTransport(**kwargs)
            t.resolve_map = {"+573000000073": 111, "+573000000081": 222}
            captured.append(t)
            return t

        return factory

    @pytest.mark.asyncio
    async def test_an_unheard_call_announcement_hangs_up_and_tells_the_user_later(
        self, tmp_path: Path
    ) -> None:
        """The walkie is away: the call is already accepted, the caller hears
        silence. After 30 s unheard, hang up rather than bridge to nobody,
        and queue a missed-call turn that waits until someone can hear it."""
        import dataclasses
        from datetime import timedelta

        captured: list[StubTransport] = []
        calls: list[dict[str, Any]] = []
        claims: list[InputClaim] = []
        base_ctx, _ = _build_ctx(
            self._CONFIG, tmp_path, captured_inject_calls=calls, captured_claims=claims
        )

        async def unheard(prompt: str, **kwargs: Any) -> InjectOutcome:
            calls.append({"prompt": prompt, "wait": True, **kwargs})
            return InjectOutcome.EXPIRED

        ctx = dataclasses.replace(base_ctx, inject_turn_and_wait=unheard)
        skill = TelegramSkill(transport_factory=self._factory(captured))
        await skill.setup(ctx)

        await skill._on_incoming_ring(111)  # type: ignore[attr-defined]

        announce, missed = calls
        assert announce["wait"] is True
        assert announce["priority"] == InjectPriority.BLOCK_BEHIND_COMMS
        assert announce["expires_after"] == timedelta(seconds=30)
        assert captured[0].accepted_calls == [111]
        assert captured[0].ended_calls == 1, "hung up instead of bridging to nobody"
        assert claims == []
        assert "mario" in missed["prompt"].lower()
        assert missed["priority"] == InjectPriority.BLOCK_BEHIND_COMMS
        assert missed["expires_after"] is None

    @pytest.mark.asyncio
    async def test_later_messages_follow_as_one_announcement_once_the_first_is_heard(
        self, tmp_path: Path
    ) -> None:
        """While a sender's announcement waits for the walkie, their later
        messages gather and follow as one more: two announcements, not one
        per burst, and none replaces another unheard."""
        import dataclasses

        from huxley_skill_telegram.transport import InboundMessage

        captured: list[StubTransport] = []
        prompts: list[str] = []
        heard = asyncio.Event()
        base_ctx, _ = _build_ctx(self._CONFIG, tmp_path)

        async def waits_for_the_walkie(prompt: str, **_kwargs: Any) -> InjectOutcome:
            prompts.append(prompt)
            await heard.wait()
            return InjectOutcome.DELIVERED

        ctx = dataclasses.replace(base_ctx, inject_turn_and_wait=waits_for_the_walkie)
        skill = TelegramSkill(transport_factory=self._factory(captured))
        await skill.setup(ctx)

        async def message(text: str) -> None:
            await captured[0].on_message(
                InboundMessage(user_id=222, sender_display="hija", text=text, timestamp=0)
            )

        await message("hola")
        await asyncio.sleep(0.15)  # first burst flushed, waiting
        await message("¿estás?")
        await asyncio.sleep(0.15)
        await message("llámame")
        await asyncio.sleep(0.15)
        assert len(prompts) == 1, "later messages wait for the first announcement"

        heard.set()
        await asyncio.sleep(0.2)

        assert len(prompts) == 2
        assert "hola" in prompts[0]
        assert "estás" in prompts[1]
        assert "llámame" in prompts[1]


class TestMyPhoneNumber:
    """He asks the walkie for his own number (Mario, 2026-10-14)."""

    def _config(self, **extra: Any) -> dict[str, Any]:
        return {
            "api_id": 1,
            "api_hash": "x",
            "userbot_phone": "+573000000073",
            "default_country_code": "+57",
            "contacts": {"mario": "+573000000078"},
            **extra,
        }

    @pytest.mark.asyncio
    async def test_reads_his_number_digit_by_digit(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        ctx, _ = _build_ctx(self._config(), tmp_path)
        await skill.setup(ctx)
        result = await skill.handle("my_phone_number", {})
        out = json.loads(result.output)
        assert out["ok"] is True
        assert (
            "«Tu número es tres cero cero, cero cero cero, cero cero siete tres. ¿Te lo repito?»"
            in out["then"]
        )
        assert "No llames a ninguna herramienta" in out["then"]

    @pytest.mark.asyncio
    async def test_no_number_saved_says_so(self, tmp_path: Path) -> None:
        skill = TelegramSkill(transport_factory=StubTransport)
        config = self._config()
        del config["userbot_phone"]
        ctx, _ = _build_ctx(config, tmp_path)
        await skill.setup(ctx)
        out = json.loads((await skill.handle("my_phone_number", {})).output)
        assert out["ok"] is False

    @pytest.mark.asyncio
    async def test_the_tool_names_him_when_telegram_knows_his_name(self, tmp_path: Path) -> None:
        class NamedStub(StubTransport):
            self_name = "Rafael"

        skill = TelegramSkill(transport_factory=NamedStub)
        ctx, _ = _build_ctx(self._config(inbound={"enabled": True}), tmp_path)
        await skill.setup(ctx)
        (tool,) = [t for t in skill.tools if t.name == "my_phone_number"]
        assert "«¿cuál es mi número?»" in tool.description
        assert "el número de Rafael, que es él mismo" in tool.description


@pytest.mark.parametrize(
    ("phone", "cc", "lang", "spoken"),
    [
        ("+573000000073", "+57", "es", "tres cero cero, cero cero cero, cero cero siete tres"),
        ("+573000000073", "+57", "en", "three zero zero, zero zero zero, zero zero seven three"),
        (
            "+15551234567",
            "+57",
            "es",
            "uno cinco cinco, cinco uno dos, tres cuatro cinco, seis siete",
        ),
    ],
)
def test_spoken_number(phone: str, cc: str, lang: str, spoken: str) -> None:
    from huxley_skill_telegram.skill import _spoken_number

    assert _spoken_number(phone, cc, lang) == spoken
