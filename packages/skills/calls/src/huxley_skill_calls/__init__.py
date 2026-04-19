"""`huxley-skill-calls` — inbound call skill.

Mario's web app rings grandpa's device; this skill answers, relays
PCM both ways, and announces end-of-call. Built on the Stage 2
InputClaim primitive: the claim's `on_mic_frame` forwards grandpa's
mic to the caller; the claim's `speaker_source` yields the caller's
PCM to grandpa's speaker.

See `docs/skills/calls.md` for the user flow and `huxley.server.server`
for the `/call/ring` and `/call` routes the skill registers against.
"""

from __future__ import annotations

from huxley_skill_calls.skill import CallsSkill

__all__ = ["CallsSkill"]
