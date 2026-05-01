"""Telegram skill -- voice calls + text messages over a single userbot.

The skill exposes Telegram both as a voice transport (live p2p calls)
and a text transport (send + receive private messages), sharing one
Pyrogram session, one contacts list, and one whitelist policy.

## Calls

Outbound: `call_contact(name)` -- resolves a name from the contacts
list and starts an InputClaim that bridges grandpa's mic/speaker to
the live call.

Inbound: when `inbound.enabled` is true the skill connects eagerly,
listens for INCOMING_CALL events, accepts immediately (preserves
WebRTC audio quality), announces via inject_turn, then bridges audio.

## Messages

Outbound: `send_message(contact, text)` -- send a text message to a
named contact via the userbot.

Inbound: with `inbound.enabled`, every private incoming message from
the contacts whitelist (or unknown sender, surfaced as "numero
desconocido") is appended to a per-sender debounce buffer. After a
short pause the buffer fires one coalesced `inject_turn` covering the
whole burst -- so a chatty sender's "hola/papá/¿estás?" lands as one
announcement, not three competing ones.

On connect: the skill backfills the last 6 hours of unread messages
from whitelisted contacts (capped at 50) into a single inject so a
post-restart user doesn't silently lose pre-restart messages.

See `docs/skills/telegram.md` for the user-facing flow and
`docs/research/telegram-voice.md` for why the call transport is
shaped the way it is.

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
        debounce_seconds: 2.5        # default; per-sender coalesce window
        backfill_hours: 6            # default; window for unread backfill
        backfill_max: 50             # default; cap on backfill messages

`userbot_phone` is only consulted on first-run auth; the sqlite
session file persists across restarts, and the SMS-code prompt only
fires once per deploy.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    ClaimBusyError,
    ClaimEndReason,
    InjectPriority,
    InputClaim,
    SkillContext,
    SkillLogger,
    ToolDefinition,
    ToolResult,
)
from huxley_skill_telegram.inbox import (
    InboxBuffer,
    build_announcement,
    build_backfill_announcement,
)
from huxley_skill_telegram.transport import (
    InboundMessage,
    TelegramTransport,
    TransportError,
    normalize_phone,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path

    from huxley_sdk import SkillContext


def _telegram_lang_bucket(language: str) -> str:
    code = (language or "en").lower()
    for key in ("es", "en", "fr"):
        if code.startswith(key):
            return key
    return "en"


def _load_creds_from_secrets_file(secrets_dir: Path) -> dict[str, str]:
    """Read `<secrets_dir>/values.json` if it exists.

    Returns the parsed flat dict (stringified values) or an empty dict
    when the file is absent / unreadable / malformed. Caller layers env
    vars + persona.yaml fallbacks on top.

    The file shape is the same one T1.14's `ctx.secrets` API will own:
    a flat `dict[str, str]`. Pre-T1.14 we read it directly here; once
    T1.14 ships, this loader collapses into `await ctx.secrets.get(key)`
    calls and the file shape stays unchanged.
    """
    path = secrets_dir / "values.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v is not None}


_TOOL_DESC: dict[str, dict[str, str]] = {
    "es": {
        "call_contact": (
            "Llama por teléfono (Telegram) a una persona de la lista de "
            "contactos. Úsalo cuando el usuario pida llamar a alguien -- "
            "ej. 'llama a mi hija', 'quiero hablar con el hijo'. La "
            "llamada abre el micrófono: el usuario oye a la persona y "
            "la persona lo oye a él. Contactos disponibles: {contacts}."
        ),
        "call_name_param": (
            "Nombre (en minúsculas) del contacto, tal como aparece en "
            "la lista de contactos configurados."
        ),
        "send_message": (
            "Envía un mensaje de texto por Telegram a una persona de "
            "la lista de contactos. Úsalo cuando el usuario quiera "
            "mandar un recado escrito -- ej. 'manda un mensaje a mi "
            "hija diciéndole que ya almorcé'. El mensaje llega al "
            "teléfono de la persona como un texto normal de Telegram. "
            "Contactos disponibles: {contacts}."
        ),
        "send_name_param": "Nombre (en minúsculas) del contacto destinatario.",
        "send_text_param": (
            "Texto del mensaje a enviar. Telegram acepta hasta 4096 caracteres por mensaje."
        ),
        "contacts_empty": "(ninguno)",
    },
    "en": {
        "call_contact": (
            "Call a person (Telegram voice call) from the contacts list. "
            "Use when the user asks to call someone -- e.g. 'call my "
            "daughter', 'I want to talk to my son'. The call opens the "
            "microphone: the user hears the person and the person hears "
            "them. Available contacts: {contacts}."
        ),
        "call_name_param": (
            "Contact name (lowercase), exactly as it appears in the configured contacts list."
        ),
        "send_message": (
            "Send a text message on Telegram to a contact from the list. "
            "Use when the user wants to send a written note -- e.g. "
            "'text my daughter that I already had lunch'. The message "
            "arrives on their phone as a normal Telegram text. "
            "Available contacts: {contacts}."
        ),
        "send_name_param": "Contact name (lowercase) to send the message to.",
        "send_text_param": ("Body of the message. Telegram accepts up to 4096 characters."),
        "contacts_empty": "(none)",
    },
    "fr": {
        "call_contact": (
            "Appelle quelqu'un (appel vocal Telegram) depuis la liste "
            "des contacts. À utiliser quand l'utilisateur demande "
            "d'appeler une personne -- ex. 'appelle ma fille'. L'appel "
            "ouvre le micro : l'utilisateur entend la personne et la "
            "personne l'entend. Contacts disponibles : {contacts}."
        ),
        "call_name_param": (
            "Nom du contact (en minuscules), tel qu'il apparaît dans la "
            "liste de contacts configurée."
        ),
        "send_message": (
            "Envoie un message texte sur Telegram à un contact de la "
            "liste. À utiliser quand l'utilisateur veut envoyer un "
            "message écrit -- ex. 'envoie un message à ma fille pour "
            "lui dire que j'ai déjà mangé'. Le message arrive sur son "
            "téléphone comme un SMS Telegram normal. Contacts "
            "disponibles : {contacts}."
        ),
        "send_name_param": "Nom du contact destinataire (en minuscules).",
        "send_text_param": ("Corps du message. Telegram accepte jusqu'à 4096 caractères."),
        "contacts_empty": "(aucun)",
    },
}


_STRINGS: dict[str, dict[str, str]] = {
    "es": {
        "unknown_caller": "número desconocido",
        "unknown_caller_article": "un número desconocido",
        "the_caller": "la persona",
        "empty_message_arg": "El mensaje está vacío. Dime qué quieres decirle a esa persona.",
        "not_configured_call": (
            "Las llamadas de Telegram no están configuradas en este dispositivo. "
            "No puedo llamar a nadie hasta que alguien configure las credenciales."
        ),
        "not_configured_send": (
            "Los mensajes de Telegram no están configurados en este dispositivo. "
            "No puedo enviar nada hasta que alguien configure las credenciales."
        ),
        "contact_missing_call": (
            "No tengo a '{name}' en la lista de contactos. Contactos conocidos: {known}"
        ),
        "contact_missing_send": (
            "No tengo a '{name}' en la lista de contactos para enviarle mensajes. "
            "Contactos conocidos: {known}"
        ),
        "place_call_failed": (
            "No pude conectar la llamada a {name}: {exc}. "
            "Puede ser que el contacto no tenga Telegram con ese número."
        ),
        "send_failed": (
            "No pude enviar el mensaje a {name}: {exc}. "
            "Puede ser que el contacto no tenga Telegram con ese número."
        ),
        "too_long": (
            "El mensaje es muy largo ({chars} caracteres; Telegram acepta "
            "máximo 4096). Acórtalo o divídelo en varios mensajes."
        ),
        "accept_failed": ("Intente contestar la llamada de {display} pero falló la conexión."),
        "answering": "Llamada de {display}, contestando.",
        "call_no_longer_available": (
            "La llamada de {name} ya no está disponible -- la persona "
            "colgó antes de que se contestara."
        ),
        "contacts_none": "(ninguno)",
    },
    "en": {
        "unknown_caller": "unknown number",
        "unknown_caller_article": "an unknown number",
        "the_caller": "the caller",
        "empty_message_arg": "The message is empty. Tell me what you want to say.",
        "not_configured_call": (
            "Telegram calls aren't configured on this device. "
            "I can't call anyone until someone sets up the credentials."
        ),
        "not_configured_send": (
            "Telegram messaging isn't configured on this device. "
            "I can't send anything until someone sets up the credentials."
        ),
        "contact_missing_call": (
            "I don't have '{name}' in the contact list. Known contacts: {known}"
        ),
        "contact_missing_send": (
            "I don't have '{name}' in the contact list to message. Known contacts: {known}"
        ),
        "place_call_failed": (
            "I couldn't connect the call to {name}: {exc}. "
            "Maybe the contact doesn't have Telegram under that number."
        ),
        "send_failed": (
            "I couldn't send the message to {name}: {exc}. "
            "Maybe the contact doesn't have Telegram under that number."
        ),
        "too_long": (
            "The message is too long ({chars} chars; Telegram accepts up to "
            "4096). Shorten it or split it into several messages."
        ),
        "accept_failed": "I tried to answer the call from {display} but the connection failed.",
        "answering": "Call from {display}, answering.",
        "call_no_longer_available": (
            "The call from {name} is no longer available -- the person "
            "hung up before it could be answered."
        ),
        "contacts_none": "(none)",
    },
    "fr": {
        "unknown_caller": "numéro inconnu",
        "unknown_caller_article": "un numéro inconnu",
        "the_caller": "la personne",
        "empty_message_arg": "Le message est vide. Dis-moi ce que tu veux lui dire.",
        "not_configured_call": (
            "Les appels Telegram ne sont pas configurés sur cet appareil. "
            "Je ne peux appeler personne tant que quelqu'un ne configure "
            "pas les identifiants."
        ),
        "not_configured_send": (
            "La messagerie Telegram n'est pas configurée sur cet appareil. "
            "Je ne peux rien envoyer tant que quelqu'un ne configure pas "
            "les identifiants."
        ),
        "contact_missing_call": (
            "Je n'ai pas '{name}' dans la liste de contacts. Contacts connus : {known}"
        ),
        "contact_missing_send": (
            "Je n'ai pas '{name}' dans la liste de contacts pour lui envoyer "
            "des messages. Contacts connus : {known}"
        ),
        "place_call_failed": (
            "Je n'ai pas pu établir l'appel vers {name} : {exc}. "
            "Peut-être que le contact n'est pas sur Telegram avec ce numéro."
        ),
        "send_failed": (
            "Je n'ai pas pu envoyer le message à {name} : {exc}. "
            "Peut-être que le contact n'est pas sur Telegram avec ce numéro."
        ),
        "too_long": (
            "Le message est trop long ({chars} caractères ; Telegram "
            "accepte jusqu'à 4096). Raccourcis-le ou divise-le."
        ),
        "accept_failed": (
            "J'ai tenté de répondre à l'appel de {display} mais la connexion a échoué."
        ),
        "answering": "Appel de {display}, je réponds.",
        "call_no_longer_available": (
            "L'appel de {name} n'est plus disponible -- la personne a "
            "raccroché avant qu'on ne réponde."
        ),
        "contacts_none": "(aucun)",
    },
}


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
        # Default-drop unknown-sender messages: symmetric with the
        # contacts_only call-rejection policy. Spammers exist on Telegram;
        # silently announcing every unknown number is a DoS vector for an
        # always-on-audio user. Opt in via inbound.unknown_messages: "announce".
        self._unknown_messages: str = "drop"  # "drop" | "announce"
        self._debounce_seconds: float = 2.5
        self._backfill_hours: int = 6
        self._backfill_max: int = 50

        # Reverse map: user_id -> contact name. Built at connect() time
        # from the contacts dict. Unknown callers stay absent from the map.
        self._user_id_to_name: dict[int, str] = {}

        # Per-sender debounce/coalesce buffer for inbound messages. Built
        # in setup() once we know the debounce window. None when inbound
        # is disabled or credentials are missing.
        self._inbox: InboxBuffer | None = None
        # Tracks every fire-and-forget task spawned by the skill -- backfill,
        # hangup, claim-end inject, etc. Teardown awaits all of them so a
        # message-mid-flight isn't silently dropped. Tasks remove themselves
        # on done; the set never grows unbounded.
        self._tasks: set[asyncio.Task[None]] = set()

        # Name of the contact currently in a call (set in handle()/
        # Guard for the announce-before-accept window in _on_incoming_ring.
        # Set when a ring arrives; cleared by _on_ring_cancelled (race: caller
        # hung up during announcement) or after accept_call succeeds.
        self._pending_incoming: int | None = None

        # Active-call state (set in _call_contact / _on_incoming_ring, cleared in _on_claim_end).
        self._active_contact_name: str | None = None

        # Session UI language — drives tool descriptions, error copy,
        # and the inject_turn prompts this skill builds. Updated every
        # session via reconfigure().
        self._language: str = "en"

    def _t(self, key: str, **fmt: Any) -> str:
        bucket = _telegram_lang_bucket(self._language)
        table = _STRINGS.get(bucket) or _STRINGS["en"]
        template = table.get(key) or _STRINGS["en"].get(key) or key
        return template.format(**fmt) if fmt else template

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def tools(self) -> list[ToolDefinition]:
        bucket = _telegram_lang_bucket(self._language)
        td = _TOOL_DESC.get(bucket, _TOOL_DESC["en"])
        contacts_list = (
            ", ".join(sorted(self._contacts)) if self._contacts else td["contacts_empty"]
        )
        return [
            ToolDefinition(
                name="call_contact",
                description=td["call_contact"].format(contacts=contacts_list),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": td["call_name_param"],
                        },
                    },
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="send_message",
                description=td["send_message"].format(contacts=contacts_list),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": td["send_name_param"],
                        },
                        "text": {
                            "type": "string",
                            "description": td["send_text_param"],
                        },
                    },
                    "required": ["name", "text"],
                },
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger
        self._ctx = ctx
        self._language = ctx.language or "en"

        cfg = ctx.config
        # Cred resolution priority (T2.8 — establishes the per-persona
        # secrets-dir pattern T1.14 generalizes via ctx.secrets):
        #   1. <persona.data_dir>/secrets/telegram/values.json   (preferred)
        #   2. HUXLEY_TELEGRAM_* env vars                         (fallback)
        #   3. persona.yaml `skills.telegram.<field>`             (dev/test)
        # Contacts stay in persona.yaml -- they're not really "secrets" for
        # a family-specific persona, and they aren't a flat string dict.
        secrets = _load_creds_from_secrets_file(ctx.persona_data_dir / "secrets" / "telegram")
        api_id_raw = (
            secrets.get("api_id") or os.environ.get("HUXLEY_TELEGRAM_API_ID") or cfg.get("api_id")
        )
        api_hash = (
            secrets.get("api_hash")
            or os.environ.get("HUXLEY_TELEGRAM_API_HASH")
            or cfg.get("api_hash")
        )
        userbot_phone = (
            secrets.get("userbot_phone")
            or os.environ.get("HUXLEY_TELEGRAM_USERBOT_PHONE")
            or cfg.get("userbot_phone")
        )
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
            debounce_raw = inbound_cfg.get("debounce_seconds", 2.5)
            if isinstance(debounce_raw, int | float) and debounce_raw > 0:
                self._debounce_seconds = float(debounce_raw)
            backfill_hours_raw = inbound_cfg.get("backfill_hours", 6)
            if isinstance(backfill_hours_raw, int) and backfill_hours_raw >= 0:
                self._backfill_hours = backfill_hours_raw
            backfill_max_raw = inbound_cfg.get("backfill_max", 50)
            if isinstance(backfill_max_raw, int) and backfill_max_raw >= 0:
                self._backfill_max = backfill_max_raw
            unknown_raw = inbound_cfg.get("unknown_messages", "drop")
            if isinstance(unknown_raw, str) and unknown_raw in ("drop", "announce"):
                self._unknown_messages = unknown_raw

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
                    "Drop `{api_id, api_hash, userbot_phone}` into "
                    "<persona>/data/secrets/telegram/values.json (preferred), "
                    "or set HUXLEY_TELEGRAM_API_ID + HUXLEY_TELEGRAM_API_HASH "
                    "env vars, or put `api_id` / `api_hash` in persona.yaml. "
                    "Get them from my.telegram.org/apps. Skill will register "
                    "but call_contact will return an error until configured."
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
                self._inbox = InboxBuffer(
                    debounce_seconds=self._debounce_seconds,
                    on_flush=self._flush_inbox,
                )
                inbound_kwargs = {
                    "on_incoming_ring": self._on_incoming_ring,
                    "on_ring_cancelled": self._on_ring_cancelled,
                    "on_message": self._on_inbound_message,
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
                # Connect eagerly so we're listening for incoming calls
                # AND messages from the moment the persona starts.
                # Outbound-only config stays lazy (connects on first
                # handle() call).
                await self._transport.connect()
                await self._build_reverse_map()
                # Fire-and-forget backfill so setup() doesn't block on
                # a possibly-slow get_dialogs() pass. Errors logged via
                # the task wrapper; no inject if no unread or no contacts.
                self._spawn_task(self._run_backfill(), name="telegram-backfill")

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
            case "send_message":
                name_raw = args.get("name")
                text_raw = args.get("text")
                if not isinstance(name_raw, str) or not name_raw.strip():
                    return _error_result("send_message requires a non-empty `name` argument")
                if not isinstance(text_raw, str) or not text_raw.strip():
                    return _error_result(self._t("empty_message_arg"))
                return await self._send_message(name_raw.lower().strip(), text_raw)
            case _:
                await self._logger.awarning("telegram.unknown_tool", tool=tool_name)
                return _error_result(f"Unknown tool: {tool_name}")

    # --- Outbound tool ---

    async def _call_contact(self, name: str) -> ToolResult:
        assert self._logger is not None

        if self._transport is None:
            await self._logger.awarning("telegram.called_unconfigured", name=name)
            return _error_result(self._t("not_configured_call"))

        phone = self._contacts.get(name)
        if phone is None:
            await self._logger.ainfo(
                "telegram.contact_not_found",
                name=name,
                known=list(self._contacts),
            )
            known = ", ".join(sorted(self._contacts)) or self._t("contacts_none")
            return _error_result(self._t("contact_missing_call", name=name, known=known))

        self._active_contact_name = name
        try:
            await self._transport.connect()
            user_id = await self._transport.resolve_contact(phone)
            await self._transport.place_call(user_id)
        except TransportError as exc:
            self._active_contact_name = None
            await self._logger.aexception("telegram.place_call_failed", name=name)
            return _error_result(self._t("place_call_failed", name=name, exc=str(exc)))

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

    async def _send_message(self, name: str, text: str) -> ToolResult:
        """Send a Telegram text message. Resolves the contact name to a
        user_id (cached in _user_id_to_name when known, falls back to
        a fresh resolve_contact() if not).
        """
        assert self._logger is not None
        from datetime import UTC, datetime

        if self._transport is None:
            await self._logger.awarning("telegram.send_unconfigured", name=name)
            return _error_result(self._t("not_configured_send"))

        phone = self._contacts.get(name)
        if phone is None:
            await self._logger.ainfo(
                "telegram.send_contact_not_found", name=name, known=list(self._contacts)
            )
            known = ", ".join(sorted(self._contacts)) or self._t("contacts_none")
            return _error_result(self._t("contact_missing_send", name=name, known=known))

        # Telegram caps at 4096 chars per message. Validate at the skill
        # layer so the LLM gets a clean, localized message ("too long")
        # rather than the transport's generic TransportError surface.
        # The transport trusts its caller; this skill is the only caller.
        if len(text) > 4096:
            await self._logger.ainfo("telegram.send_text_too_long", name=name, chars=len(text))
            return _error_result(self._t("too_long", chars=len(text)))

        try:
            await self._transport.connect()
            user_id = await self._transport.resolve_contact(phone)
            await self._transport.send_text(user_id, text)
        except TransportError as exc:
            await self._logger.aexception("telegram.send_failed", name=name)
            return _error_result(self._t("send_failed", name=name, exc=str(exc)))

        sent_at = datetime.now(UTC).isoformat(timespec="seconds")
        await self._logger.ainfo(
            "telegram.send_message_ok", name=name, user_id=user_id, chars=len(text)
        )
        return ToolResult(
            output=json.dumps(
                {"ok": True, "contact": name, "chars": len(text), "sent_at": sent_at}
            )
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
        if self._transport is not None and self._transport.is_in_call:
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

        display = name or self._t("unknown_caller")
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
            await self._ctx.inject_turn(self._t("accept_failed", display=display))
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
        await self._ctx.inject_turn_and_wait(self._t("answering", display=display))

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

        name = self._user_id_to_name.get(user_id, self._t("the_caller"))
        await self._logger.ainfo(
            "telegram.inbound.ring_cancelled", user_id=user_id, caller_name=name
        )

        if self._pending_incoming == user_id:
            # Signal _on_incoming_ring to abort -- don't accept a dead call.
            self._pending_incoming = None
        await self._ctx.inject_turn(self._t("call_no_longer_available", name=name))

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
        assert self._logger is not None
        contact = self._active_contact_name
        self._active_contact_name = None
        await self._logger.ainfo("telegram.claim_ended", reason=reason.value, contact=contact)

        if self._transport is not None:
            self._spawn_task(self._transport.end_call(), name="telegram-hangup")

        if reason is ClaimEndReason.NATURAL and self._ctx is not None:
            ctx = self._ctx
            who = contact or self._t("the_caller")
            prompt = self._call_ended_prompt(who)
            self._spawn_task(ctx.inject_turn(prompt), name="telegram-claim-end-inject")
            await self._logger.ainfo("telegram.injecting_call_ended_turn", contact=who)

    def _call_ended_prompt(self, who: str) -> str:
        """Localized LLM-instruction prompt for 'the peer hung up'."""
        bucket = _telegram_lang_bucket(self._language)
        if bucket == "es":
            return (
                f"La llamada con {who} ha terminado porque la otra persona colgó. "
                "Informa brevemente al usuario que la llamada ha concluido."
            )
        if bucket == "fr":
            return (
                f"L'appel avec {who} s'est terminé parce que la personne a "
                "raccroché. Informe brièvement l'utilisateur que l'appel est fini."
            )
        return (
            f"The call with {who} has ended because the other person hung up. "
            "Briefly let the user know the call is over."
        )

    # --- Inbound message callbacks (called by transport) ---

    async def _on_inbound_message(self, message: InboundMessage) -> None:
        """Called by the transport for each incoming private text message.

        Resolves the sender against the contacts whitelist:
        - Known contact: use the configured persona-side name ("hija").
        - Unknown sender + `unknown_messages=announce`: surface as
          "un número desconocido" so the user still hears it.
        - Unknown sender + `unknown_messages=drop` (default): log and
          drop. Symmetric with the contacts_only call-rejection policy --
          spammers can't trigger announcements.

        Each accepted message is appended to a per-sender debounce
        buffer; the buffer fires `_flush_inbox` after the debounce
        window elapses with no further messages from the same sender,
        coalescing bursts into a single inject. Avoids the framework's
        same-key-inject drop-in-flight footgun.
        """
        assert self._logger is not None
        if self._inbox is None:
            return

        contact_name = self._user_id_to_name.get(message.user_id)
        if contact_name is None:
            if self._unknown_messages == "drop":
                await self._logger.ainfo(
                    "telegram.inbound.unknown_dropped",
                    user_id=message.user_id,
                    sender_display=message.sender_display,
                    hint=(
                        "Set inbound.unknown_messages: announce in persona.yaml "
                        "to surface unknown-sender messages, or add this user_id "
                        "to skills.telegram.contacts."
                    ),
                )
                return
            display = self._t("unknown_caller_article")
            await self._logger.ainfo(
                "telegram.inbound.message_from_unknown",
                user_id=message.user_id,
                sender_display=message.sender_display,
            )
        else:
            display = contact_name

        await self._logger.ainfo(
            "telegram.inbound.message_buffered",
            user_id=message.user_id,
            display=display,
            chars=len(message.text),
        )
        self._inbox.add(message.user_id, display, message.text)

    async def _flush_inbox(self, user_id: int, display: str, messages: list[str]) -> None:
        """Called by the inbox buffer when a sender's debounce window elapses.

        Builds the coalesced announcement and fires inject_turn(NORMAL).
        NORMAL priority queues behind active calls (Stage 2b) so an inbound
        message during a phone call doesn't interrupt; it'll fire on the
        next quiet turn-end after the call ends.

        `dedup_key=msg_burst:<user_id>` is defense-in-depth against an
        accidental double-flush -- the real coalescing happens in the
        InboxBuffer, but if the buffer ever races we'd rather drop than
        double-narrate.
        """
        assert self._logger is not None
        if self._ctx is None:
            return
        prompt = build_announcement(display, messages, language=self._language)
        await self._logger.ainfo(
            "telegram.inbound.flushing",
            user_id=user_id,
            display=display,
            message_count=len(messages),
        )
        await self._ctx.inject_turn(
            prompt,
            dedup_key=f"msg_burst:{user_id}",
            priority=InjectPriority.NORMAL,
        )

    # Wait this long after setup() before firing the backfill inject, so
    # the OpenAI Realtime session has time to fully connect. Confirmed
    # 2026-04-24 first smoke test: the backfill was firing within ~300ms
    # of setup_complete -- BEFORE session_connected -- so coord.inject_turn
    # logged but no session.tx.conversation_message ever followed and the
    # inject was effectively lost. The live-message path doesn't have this
    # problem because messages arrive after the user has been talking
    # (session is established by then).
    _BACKFILL_STARTUP_DELAY_S = 5.0

    async def _run_backfill(self) -> None:
        """Fetch unread messages from whitelisted contacts since the cutoff
        and fire one summary inject so a post-restart user doesn't silently
        lose pre-restart messages.

        No-op when:
        - backfill_hours == 0 or backfill_max == 0 (config opt-out)
        - no whitelisted contacts resolved (empty reverse map)
        - no unread messages from whitelisted senders within the window

        Soft-fails on transport errors -- a failed backfill must not block
        the rest of setup.
        """
        assert self._logger is not None
        if self._transport is None or self._ctx is None:
            return
        if self._backfill_hours <= 0 or self._backfill_max <= 0:
            return
        if not self._user_id_to_name:
            await self._logger.ainfo(
                "telegram.backfill.skipped",
                reason="no_resolved_contacts",
            )
            return

        # Wait for the OpenAI Realtime session to be established before
        # firing the inject. The fetch_unread Pyrogram round-trip already
        # takes ~300ms; we add headroom so the inject's conversation_message
        # actually reaches a live session. If the user PTTs first, their
        # turn takes priority and the backfill inject queues behind it
        # (Stage 2b queue-behind-COMMS does the right thing).
        await asyncio.sleep(self._BACKFILL_STARTUP_DELAY_S)
        if self._transport is None or self._ctx is None:
            return  # teardown raced us during the sleep

        whitelist = set(self._user_id_to_name.keys())
        try:
            unread = await self._transport.fetch_unread(
                whitelist,
                since_seconds=self._backfill_hours * 3600,
                max_messages=self._backfill_max,
            )
        except Exception:
            await self._logger.aexception("telegram.backfill.fetch_failed")
            return

        if not unread:
            await self._logger.ainfo("telegram.backfill.no_unread")
            return

        per_sender: dict[str, list[str]] = {}
        unknown_display = self._t("unknown_caller_article")
        for msg in unread:
            display = self._user_id_to_name.get(msg.user_id, unknown_display)
            per_sender.setdefault(display, []).append(msg.text)

        prompt = build_backfill_announcement(per_sender, language=self._language)
        await self._logger.ainfo(
            "telegram.backfill.injecting",
            total=len(unread),
            per_sender_counts={k: len(v) for k, v in per_sender.items()},
        )
        # No dedup_key: backfill fires once per session, no resend path.
        await self._ctx.inject_turn(prompt, priority=InjectPriority.NORMAL)

    # --- Task helper ---

    def _spawn_task(self, coro: Coroutine[Any, Any, None], *, name: str) -> None:
        """Track an asyncio task so GC can't collect it mid-run.

        Used for fire-and-forget work spawned from setup() (backfill) or
        from sync timer callbacks (none today). Tasks remove themselves
        from the set on completion.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def reconfigure(self, ctx: SkillContext) -> None:
        """Refresh language-dependent state for a new session.

        Flips tool descriptions, error copy, and the inject-prompt
        builders to the session's language. The transport + InboxBuffer
        + pending backfill task are intentionally preserved — they're
        persona-scoped, not session-scoped.
        """
        self._language = ctx.language or self._language
        await ctx.logger.ainfo("telegram.reconfigure", language=self._language)

    async def teardown(self) -> None:
        # Drain pending message bursts so a sender mid-typing at shutdown
        # doesn't silently lose their last announcement -- the buffer
        # spawns the flush as a task; we await all in-flight here.
        if self._inbox is not None:
            await self._inbox.flush_all()

        # Cancel any other tracked tasks (backfill in flight, etc.).
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        if self._transport is not None:
            await self._transport.disconnect()
        if self._logger is not None:
            await self._logger.ainfo("telegram.teardown_complete")


def _error_result(message: str) -> ToolResult:
    """LLM-facing error ToolResult. Message is what the LLM sees and
    will narrate to the user; keep it in the persona's language."""
    return ToolResult(output=json.dumps({"ok": False, "error": message}))
