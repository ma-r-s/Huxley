"""Huxley Telegram skill.

Voice calls (inbound + outbound) and — coming — text messaging over Telegram.
Built on kurigram (pyrogram-compatible) + py-tgcalls. Tools: `call_contact`.
"""

from __future__ import annotations

from huxley_skill_telegram.skill import TelegramSkill

__all__ = ["TelegramSkill"]
