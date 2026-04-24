"""Telegram skill — place or receive a p2p Telegram voice call.

Outbound: `call_contact(name)` — resolves a name from the contacts list
to a Telegram user and starts an InputClaim that bridges the user's
mic/speaker to the live call.

Inbound: when `inbound.enabled` is true the skill connects eagerly at
setup, builds a user_id->name reverse map, and listens for INCOMING_CALL
events. On ring it accepts immediately (preserves WebRTC audio quality),
announces via inject_turn, waits for the LLM to finish speaking, then
bridges audio via ctx.start_input_claim — no LLM tool call needed.

See `docs/skills/telegram.md` for the user-facing flow and
`docs/research/telegram-voice.md` for why the transport is shaped the
way it is.

Config (persona.yaml `skills.telegram:` block):

    telegram:
      api_id: 12345678
      api_hash: "abc..."
      userbot_phone: "+573153283397"
      contacts:
        hija: "+573186851696"
        hijo: "+573001234567"
      inbound:
        enabled: true
        auto_answer: contacts_only   # "contacts_only" | "all" | false

`userbot_phone` is only consulted on first-run auth; the sqlite
session file persists across restarts, and the SMS-code prompt only
fires once per deploy.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    ClaimBusyError,
    ClaimEndReason,
    InputClaim,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)
from huxley_skill_telegram.transport import (
    TelegramTransport,
    TransportError,
    normalize_phone,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from huxley_sdk import SkillContext


class TelegramSkill:
    """p2p Telegram voice-call skill — outbound via call_contact,
    inbound via answer_incoming_call (when inbound.enabled)."""

    def __init__(
        self,
        *,
        transport_factory: Callable[..., TelegramTransport] | None = None,
    ) -> None:
        """`transport_factory` is an injection point for tests — pass a
        fake that returns a stub transport to avoid importing pyrogram.
        """
        self._logger: SkillLogger | None = None
        self._ctx: SkillContext | None = None
        self._contacts: dict[str, str] = {}  # lowercased name -> normalized phone
        self._transport: TelegramTransport | None = None
        self._transport_factory = transport_factory or TelegramTransport

        # Inbound config
        self._inbound_enabled: bool = False
        self._auto_answer: str = "contacts_only"  # "contacts_only" | "all"

        # Reverse map: user_id -> contact name. Built at connect() time
        # from the contacts dict. Unknown callers stay absent from the map.
        self._user_id_to_name: dict[int, str] = {}

        # Name of the contact currently in a call (set in handle()/
        # Guard for the announce-before-accept window in _on_incoming_ring.
        # Set when a ring arrives; cleared by _on_ring_cancelled (race: caller
        # hung up during announcement) or after accept_call succeeds.
        self._pending_incoming: int | None = None

        # Active-call state (set in _call_contact / _on_incoming_ring, cleared in _on_claim_end).
        self._active_contact_name: str | None = None
        # Holds refs to in-flight hangup/inject tasks spawned by `_on_claim_end`
        # so GC doesn't eat them mid-execution. Task removes itself on done.
        self._end_tasks: set[asyncio.Task[None]] = set()

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def tools(self) -> list[ToolDefinition]:
        contacts_list = ", ".join(sorted(self._contacts)) if self._contacts else "(ninguno)"
        tools = [
            ToolDefinition(
                name="call_contact",
                description=(
                    "Llama por telefono (Telegram) a una persona de la lista de "
                    "contactos. Usalo cuando el usuario pida llamar a alguien -- "
                    "ej. 'llama a mi hija', 'quiero hablar con el hijo'. La "
                    "llamada abre el microfono: el usuario oye a la persona y "
                    "la persona lo oye a el. Contactos disponibles: "
                    f"{contacts_list}."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Nombre (en minusculas) del contacto, tal como "
                                "aparece en la lista de contactos configurados."
                            ),
                        },
                    },
                    "required": ["name"],
                },
            ),
        ]
        return tools

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        self._ctx = ctx

        cfg = ctx.config
        # Env vars take precedence so secrets (api_id/hash/phone) don't have
        # to live in a checked-in persona.yaml. A real deployment sets these
        # in `.env` at packages/core/; dev/test can put them directly in the
        # persona file. Contacts stay in persona.yaml -- they're not really
        # "secrets" for a family-specific persona.
        api_id_raw = os.environ.get("HUXLEY_TELEGRAM_API_ID") or cfg.get("api_id")
        api_hash = os.environ.get("HUXLEY_TELEGRAM_API_HASH") or cfg.get("api_hash")
        userbot_phone = os.environ.get("HUXLEY_TELEGRAM_USERBOT_PHONE") or cfg.get("userbot_phone")
        raw_contacts = cfg.get("contacts") or {}

        try:
            api_id = int(api_id_raw) if api_id_raw is not None else None
        except (TypeError, ValueError):
            api_id = None

        self._contacts = {
            str(name).lower().strip(): normalize_phone(str(phone))
            for name, phone in (raw_contacts.items() if isinstance(raw_contacts, dict) else [])
            if isinstance(phone, str) and phone
        }

        # Inbound config -- defaults to disabled.
        inbound_cfg = cfg.get("inbound") or {}
        if isinstance(inbound_cfg, dict):
            enabled_raw = inbound_cfg.get("enabled", False)
            self._inbound_enabled = bool(enabled_raw)
            auto_raw = inbound_cfg.get("auto_answer", "contacts_only")
            if auto_raw is False or auto_raw is None:
                self._inbound_enabled = False
            elif isinstance(auto_raw, str) and auto_raw in ("contacts_only", "all"):
                self._auto_answer = auto_raw

        # Soft-fail when credentials are missing: log loudly, skip the
        # transport build, and let `call_contact` return an LLM-facing
        # error explaining what's not set up. Cleaner than a hard RuntimeError
        # in setup() -- otherwise a persona that *lists* this skill can't boot
        # at all until someone configures Telegram, which blocks unrelated
        # development and demos.
        if not isinstance(api_id, int) or not isinstance(api_hash, str) or not api_hash:
            await ctx.logger.awarning(
                "telegram.credentials_missing",
                hint=(
                    "Set HUXLEY_TELEGRAM_API_ID + HUXLEY_TELEGRAM_API_HASH env "
                    "vars (or `api_id` / `api_hash` in persona.yaml); get them "
                    "from my.telegram.org/apps. Skill will register but "
                    "call_contact will return an error until configured."
                ),
            )
            self._transport = None
            self._inbound_enabled = False
        else:
            if not self._contacts:
                await ctx.logger.awarning(
                    "telegram.no_contacts_configured",
                    hint=(
                        "persona.yaml skills.telegram.contacts is empty -- "
                        "call_contact will always fail. Add at least one name->phone."
                    ),
                )
            # Pass inbound callbacks only when inbound is enabled so the
            # transport doesn't register unused handlers.
            inbound_kwargs: dict[str, Any] = {}
            if self._inbound_enabled:
                inbound_kwargs = {
                    "on_incoming_ring": self._on_incoming_ring,
                    "on_ring_cancelled": self._on_ring_cancelled,
                }
            # Session file goes in the persona's data dir, alongside the
            # sqlite DB. Gitignored via personas/*/data/ in .gitignore.
            self._transport = self._transport_factory(
                api_id=api_id,
                api_hash=api_hash,
                session_dir=ctx.persona_data_dir,
                userbot_phone=userbot_phone if isinstance(userbot_phone, str) else None,
                **inbound_kwargs,
            )

            if self._inbound_enabled:
                # Connect eagerly so we're listening for incoming calls from
                # the moment the persona starts. Outbound-only config stays
                # lazy (connects on first handle() call).
                await self._transport.connect()
                await self._build_reverse_map()

        await ctx.logger.ainfo(
            "telegram.setup_complete",
            contacts=list(self._contacts),
            configured=self._transport is not None,
            inbound=self._inbound_enabled,
            auto_answer=self._auto_answer if self._inbound_enabled else None,
        )

    async def _build_reverse_map(self) -> None:
        """Resolve all contacts to Telegram user_ids and build the
        user_id->name reverse map for caller identification.

        Soft-fails per contact: a contact that can't be resolved (hasn't
        messaged the userbot, PEER_ID_INVALID, etc.) is skipped with a
        warning. They'll show as 'numero desconocido' on inbound calls
        and are still dialable outbound (which resolves at call time).
        """
        assert self._transport is not None
        assert self._logger is not None
        resolved = 0
        for name, phone in self._contacts.items():
            try:
                uid = await self._transport.resolve_contact(phone)
                self._user_id_to_name[uid] = name
                resolved += 1
            except Exception:
                await self._logger.awarning(
                    "telegram.inbound.resolve_failed",
                    name=name,
                    phone=phone,
                    hint="contact won't be recognized on incoming calls",
                )
        await self._logger.ainfo(
            "telegram.inbound.reverse_map_built",
            total=len(self._contacts),
            resolved=resolved,
        )

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        assert self._logger is not None
        match tool_name:
            case "call_contact":
                name_raw = args.get("name")
                if not isinstance(name_raw, str) or not name_raw.strip():
                    return _error_result("call_contact requires a non-empty `name` argument")
                return await self._call_contact(name_raw.lower().strip())
            case _:
                await self._logger.awarning("telegram.unknown_tool", tool=tool_name)
                return _error_result(f"Unknown tool: {tool_name}")

    # --- Outbound tool ---

    async def _call_contact(self, name: str) -> ToolResult:
        assert self._logger is not None

        if self._transport is None:
            await self._logger.awarning("telegram.called_unconfigured", name=name)
            return _error_result(
                "Las llamadas de Telegram no estan configuradas en este dispositivo. "
                "No puedo llamar a nadie hasta que alguien configure las credenciales."
            )

        phone = self._contacts.get(name)
        if phone is None:
            await self._logger.ainfo(
                "telegram.contact_not_found",
                name=name,
                known=list(self._contacts),
            )
            return _error_result(
                f"No tengo a '{name}' en la lista de contactos. "
                f"Contactos conocidos: {', '.join(sorted(self._contacts)) or '(ninguno)'}"
            )

        self._active_contact_name = name
        try:
            await self._transport.connect()
            user_id = await self._transport.resolve_contact(phone)
            await self._transport.place_call(user_id)
        except TransportError as exc:
            self._active_contact_name = None
            await self._logger.aexception("telegram.place_call_failed", name=name)
            return _error_result(
                f"No pude conectar la llamada a {name}: {exc}. "
                "Puede ser que el contacto no tenga Telegram con ese numero."
            )

        await self._logger.ainfo("telegram.call_started", name=name, user_id=user_id)
        return ToolResult(
            output=json.dumps({"ok": True, "contact": name}),
            side_effect=InputClaim(
                on_mic_frame=self._on_mic_frame,
                speaker_source=self._transport.peer_audio_chunks(),
                on_claim_end=self._on_claim_end,
                title=name,
            ),
        )

    # --- Inbound ring callbacks (called by transport) ---

    async def _on_incoming_ring(self, user_id: int) -> None:
        """Called by the transport when an INCOMING_CALL update arrives.

        Accepts immediately to preserve pytgcalls WebRTC audio quality --
        delaying accept_call by even ~1s causes near-silent inbound audio
        from the peer. Then announces the caller via inject_turn_and_wait,
        which blocks until the LLM finishes speaking before returning. At
        that point start_input_claim is safe: the LLM is done, so provider
        suspend doesn't cut any speech off. Frames buffered in the inbound
        queue during the announcement are flushed by peer_audio_chunks().
        """
        assert self._logger is not None
        assert self._ctx is not None

        # Reject immediately if already in a call.
        if self._transport is not None and self._transport._active_user_id is not None:
            await self._logger.ainfo("telegram.inbound.rejected_busy", user_id=user_id)
            await self._transport.reject_call(user_id)
            return

        # Reject unknown callers when auto_answer is contacts_only.
        name = self._user_id_to_name.get(user_id)
        if self._auto_answer == "contacts_only" and name is None:
            await self._logger.ainfo("telegram.inbound.rejected_unknown", user_id=user_id)
            if self._transport is not None:
                await self._transport.reject_call(user_id)
            return

        display = name or "numero desconocido"
        self._pending_incoming = user_id

        await self._logger.ainfo("telegram.inbound.ring", user_id=user_id, caller_name=display)

        # Accept immediately -- any delay here degrades WebRTC inbound audio quality.
        self._active_contact_name = display
        try:
            if self._transport is not None:
                await self._transport.accept_call(user_id)
        except TransportError:
            self._active_contact_name = None
            self._pending_incoming = None
            await self._logger.aexception("telegram.inbound.accept_failed", user_id=user_id)
            await self._ctx.inject_turn(
                f"Intente contestar la llamada de {display} pero fallo la conexion."
            )
            return

        # Race: caller hung up in the narrow window before accept_call set
        # _active_user_id (on_ring_cancelled clears _pending_incoming).
        if self._pending_incoming != user_id:
            await self._logger.ainfo(
                "telegram.inbound.ring_expired_before_accept", user_id=user_id
            )
            return
        self._pending_incoming = None

        await self._logger.ainfo("telegram.inbound.call_accepted", user_id=user_id, name=display)

        # Announce the caller and wait for the LLM to finish speaking.
        # inject_turn_and_wait returns only after response_done fires so
        # start_input_claim never preempts the announcement mid-sentence.
        await self._ctx.inject_turn_and_wait(f"Llamada de {display}, contestando.")

        # Bridge audio. peer_audio_chunks() flushes frames buffered during the
        # announcement window so playback is real-time, not offset by ~3s.
        assert self._transport is not None
        try:
            await self._ctx.start_input_claim(
                InputClaim(
                    on_mic_frame=self._on_mic_frame,
                    speaker_source=self._transport.peer_audio_chunks(),
                    on_claim_end=self._on_claim_end,
                    title=display,
                )
            )
        except ClaimBusyError:
            # Coordinator reports another claim is already active on
            # COMMS. This shouldn't happen given the transport-level
            # busy-check above, but single-slot policy demands we
            # reject cleanly rather than stack. Tear down the half-
            # accepted Telegram call so the peer sees BUSY.
            await self._logger.awarning("telegram.inbound.rejected_coord_busy", user_id=user_id)
            try:
                await self._transport.reject_call(user_id)
            except TransportError:
                await self._logger.aexception(
                    "telegram.inbound.reject_call_failed", user_id=user_id
                )
            self._active_contact_name = None

    async def _on_ring_cancelled(self, user_id: int) -> None:
        """Called if the caller hangs up before we accepted the call.

        Clears _pending_incoming so _on_incoming_ring aborts after its
        inject_turn returns (race: caller hung up during the announcement).
        """
        assert self._logger is not None
        assert self._ctx is not None

        name = self._user_id_to_name.get(user_id, "la persona")
        await self._logger.ainfo(
            "telegram.inbound.ring_cancelled", user_id=user_id, caller_name=name
        )

        if self._pending_incoming == user_id:
            # Signal _on_incoming_ring to abort -- don't accept a dead call.
            self._pending_incoming = None
        await self._ctx.inject_turn(
            f"La llamada de {name} ya no esta disponible -- la persona colgo antes de que se contestara."
        )

    # --- Shared claim callbacks ---

    async def _on_mic_frame(self, pcm: bytes) -> None:
        """Forward PCM from grandpa's mic into the Telegram call."""
        if self._transport is None:
            return
        await self._transport.send_pcm(pcm)

    async def _on_claim_end(self, reason: ClaimEndReason) -> None:
        """Hang up the Telegram call when the claim ends and update the LLM.

        Any `ClaimEndReason` -- grandpa pressed PTT, a medication reminder
        preempted, the speaker iterator ran dry, or an error -- means "we're
        no longer bridging audio", so end the call.

        Fire-and-forget for the hangup: the observer's unwind chain awaits
        this, and an ntgcalls-side hang in `end_call` would stall the
        claim_ended / input_mode notify the client depends on. Spawn the
        actual hangup as a background task so the observer resolves
        immediately; `end_call` has its own internal timeouts.

        LLM context update (NATURAL only -- peer hung up): inject a turn so
        the model knows the call ended. This fires as a separate `create_task`
        (NOT awaited) because `_on_claim_end` runs inside the FocusManager's
        actor-task callback chain. Calling `inject_turn` synchronously would
        re-enter the FM (via `fm.acquire` + `fm.wait_drained`) from within
        `_notify_safe`, which deadlocks on `Queue.join`. Scheduling a task
        bypasses the FM's current processing tick and runs on the next event-
        loop iteration when the FM actor is idle.

        For USER_PTT (grandpa tapped to hang up): the coordinator's
        `on_ptt_start` returns immediately after `interrupt()` without
        creating a new listening turn -- grandpa's next PTT will open a fresh
        conversation. No inject needed here.
        """
        import asyncio

        assert self._logger is not None
        contact = self._active_contact_name
        self._active_contact_name = None
        await self._logger.ainfo("telegram.claim_ended", reason=reason.value, contact=contact)

        if self._transport is not None:
            transport = self._transport
            task = asyncio.create_task(transport.end_call())
            self._end_tasks.add(task)
            task.add_done_callback(self._end_tasks.discard)

        if reason is ClaimEndReason.NATURAL and self._ctx is not None:
            ctx = self._ctx
            who = contact or "la persona"
            prompt = (
                f"La llamada con {who} ha terminado porque la otra persona colgo. "
                "Informa brevemente al usuario que la llamada ha concluido."
            )
            inject_task = asyncio.create_task(ctx.inject_turn(prompt))
            self._end_tasks.add(inject_task)
            inject_task.add_done_callback(self._end_tasks.discard)
            await self._logger.ainfo("telegram.injecting_call_ended_turn", contact=who)

    async def teardown(self) -> None:
        if self._transport is not None:
            await self._transport.disconnect()
        if self._logger is not None:
            await self._logger.ainfo("telegram.teardown_complete")


def _error_result(message: str) -> ToolResult:
    """LLM-facing error ToolResult. Message is what the LLM sees and
    will narrate to the user; keep it in the persona's language."""
    return ToolResult(output=json.dumps({"ok": False, "error": message}))
