"""Huxley Telegram voice-call skill.

Bridges grandpa's microphone and speaker through a p2p Telegram call
to a named contact. Outbound tool: `call_contact(name)`.
"""

from __future__ import annotations

from huxley_skill_comms_telegram.skill import CommsTelegramSkill

__all__ = ["CommsTelegramSkill"]
