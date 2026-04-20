"""Comms-Telegram skill — place a p2p Telegram voice call to a contact.

One tool: `call_contact(name)`. Persona config supplies the name→phone
mapping; the skill resolves the phone to a Telegram user_id via the
userbot and starts an `InputClaim` that bridges grandpa's mic/speaker
to the live Telegram call.

See `docs/skills/comms-telegram.md` for the user-facing flow and
`docs/research/telegram-voice.md` for why the transport is shaped the
way it is.

Config (persona.yaml `skills.comms_telegram:` block):

    comms_telegram:
      api_id: 12345678
      api_hash: "abc..."
      userbot_phone: "+573153283397"
      contacts:
        hija: "+573186851696"
        hijo: "+573001234567"

`userbot_phone` is only consulted on first-run auth; the sqlite
session file persists across restarts, and the SMS-code prompt only
fires once per deploy.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    ClaimEndReason,
    InputClaim,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)
from huxley_skill_comms_telegram.transport import (
    TelegramTransport,
    TransportError,
    normalize_phone,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable


class CommsTelegramSkill:
    """p2p Telegram voice-call skill. One tool: `call_contact`."""

    def __init__(
        self,
        *,
        transport_factory: Callable[..., TelegramTransport] | None = None,
    ) -> None:
        """`transport_factory` is an injection point for tests — pass a
        fake that returns a stub transport to avoid importing pyrogram.
        """
        self._logger: SkillLogger | None = None
        self._contacts: dict[str, str] = {}  # lowercased name → normalized phone
        self._transport: TelegramTransport | None = None
        self._transport_factory = transport_factory or TelegramTransport
        # Holds refs to in-flight hangup tasks spawned by `_on_claim_end`
        # so GC doesn't eat them mid-hangup. Task removes itself on done.
        self._end_tasks: set[asyncio.Task[None]] = set()

    @property
    def name(self) -> str:
        return "comms_telegram"

    @property
    def tools(self) -> list[ToolDefinition]:
        contacts_list = ", ".join(sorted(self._contacts)) if self._contacts else "(ninguno)"
        return [
            ToolDefinition(
                name="call_contact",
                description=(
                    "Llama por teléfono (Telegram) a una persona de la lista de "
                    "contactos. Úsalo cuando el usuario pida llamar a alguien — "
                    "ej. 'llama a mi hija', 'quiero hablar con el hijo'. La "
                    "llamada abre el micrófono: el usuario oye a la persona y "
                    "la persona lo oye a él. Contactos disponibles: "
                    f"{contacts_list}."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Nombre (en minúsculas) del contacto, tal como "
                                "aparece en la lista de contactos configurados."
                            ),
                        },
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger

        cfg = ctx.config
        # Env vars take precedence so secrets (api_id/hash/phone) don't have
        # to live in a checked-in persona.yaml. A real deployment sets these
        # in `.env` at packages/core/; dev/test can put them directly in the
        # persona file. Contacts stay in persona.yaml — they're not really
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

        # Soft-fail when credentials are missing: log loudly, skip the
        # transport build, and let `call_contact` return an LLM-facing
        # error explaining what's not set up. Cleaner than a hard RuntimeError
        # in setup() — otherwise a persona that *lists* this skill can't boot
        # at all until someone configures Telegram, which blocks unrelated
        # development and demos.
        if not isinstance(api_id, int) or not isinstance(api_hash, str) or not api_hash:
            await ctx.logger.awarning(
                "comms_telegram.credentials_missing",
                hint=(
                    "Set HUXLEY_TELEGRAM_API_ID + HUXLEY_TELEGRAM_API_HASH env "
                    "vars (or `api_id` / `api_hash` in persona.yaml); get them "
                    "from my.telegram.org/apps. Skill will register but "
                    "call_contact will return an error until configured."
                ),
            )
            self._transport = None
        else:
            if not self._contacts:
                await ctx.logger.awarning(
                    "comms_telegram.no_contacts_configured",
                    hint=(
                        "persona.yaml skills.comms_telegram.contacts is empty — "
                        "call_contact will always fail. Add at least one name→phone."
                    ),
                )
            # Session file goes in the persona's data dir, alongside the
            # sqlite DB. Gitignored via personas/*/data/ in .gitignore.
            self._transport = self._transport_factory(
                api_id=api_id,
                api_hash=api_hash,
                session_dir=ctx.persona_data_dir,
                userbot_phone=userbot_phone if isinstance(userbot_phone, str) else None,
            )

        # Lazy-connect in the first handle() so setup() stays fast and
        # tests can construct the skill without network calls.
        await ctx.logger.ainfo(
            "comms_telegram.setup_complete",
            contacts=list(self._contacts),
            configured=self._transport is not None,
        )

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        assert self._logger is not None
        if tool_name != "call_contact":
            await self._logger.awarning("comms_telegram.unknown_tool", tool=tool_name)
            return _error_result(f"Unknown tool: {tool_name}")
        name_raw = args.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            return _error_result("call_contact requires a non-empty `name` argument")
        name = name_raw.lower().strip()

        if self._transport is None:
            await self._logger.awarning("comms_telegram.called_unconfigured", name=name)
            return _error_result(
                "Las llamadas de Telegram no están configuradas en este dispositivo. "
                "No puedo llamar a nadie hasta que alguien configure las credenciales."
            )

        phone = self._contacts.get(name)
        if phone is None:
            await self._logger.ainfo(
                "comms_telegram.contact_not_found",
                name=name,
                known=list(self._contacts),
            )
            return _error_result(
                f"No tengo a '{name}' en la lista de contactos. "
                f"Contactos conocidos: {', '.join(sorted(self._contacts)) or '(ninguno)'}"
            )

        assert self._transport is not None
        try:
            await self._transport.connect()
            user_id = await self._transport.resolve_contact(phone)
            await self._transport.place_call(user_id)
        except TransportError as exc:
            await self._logger.aexception("comms_telegram.place_call_failed", name=name)
            return _error_result(
                f"No pude conectar la llamada a {name}: {exc}. "
                "Puede ser que el contacto no tenga Telegram con ese número."
            )

        await self._logger.ainfo("comms_telegram.call_started", name=name, user_id=user_id)
        return ToolResult(
            output=json.dumps({"ok": True, "contact": name}),
            side_effect=InputClaim(
                on_mic_frame=self._on_mic_frame,
                speaker_source=self._transport.peer_audio_chunks(),
                on_claim_end=self._on_claim_end,
            ),
        )

    async def _on_mic_frame(self, pcm: bytes) -> None:
        """Forward PCM from grandpa's mic into the Telegram call."""
        if self._transport is None:
            return
        self._transport.send_pcm(pcm)

    async def _on_claim_end(self, reason: ClaimEndReason) -> None:
        """Hang up the Telegram call when the claim ends.

        Any `ClaimEndReason` — grandpa pressed PTT, a medication
        reminder preempted, the speaker iterator ran dry, or an error
        — means "we're no longer bridging audio", so end the call.

        Fire-and-forget: the observer's unwind chain awaits this, and
        an ntgcalls-side hang in `end_call` would stall the claim_ended
        / input_mode notify the client depends on. Spawn the actual
        hangup as a background task so the observer resolves
        immediately; `end_call` has its own internal timeouts.
        """
        assert self._logger is not None
        await self._logger.ainfo("comms_telegram.claim_ended", reason=reason.value)
        if self._transport is not None:
            import asyncio

            transport = self._transport
            # Track the task so Python's GC doesn't collect it mid-hangup.
            # Cleared when the task completes. See docs/background.md.
            task = asyncio.create_task(transport.end_call())
            self._end_tasks.add(task)
            task.add_done_callback(self._end_tasks.discard)

    async def teardown(self) -> None:
        if self._transport is not None:
            await self._transport.disconnect()
        if self._logger is not None:
            await self._logger.ainfo("comms_telegram.teardown_complete")


def _error_result(message: str) -> ToolResult:
    """LLM-facing error ToolResult. Message is what the LLM sees and
    will narrate to the user; keep it in the persona's language."""
    return ToolResult(output=json.dumps({"ok": False, "error": message}))
