# AbuelOS — Documentation

Single source of truth for what AbuelOS is, why it exists, and how it's built. Code comments describe _what_; this folder describes _why_ and _what we're aiming at_.

## Reading order

1. [**vision.md**](./vision.md) — Who this is for, why current assistants fail him, the _"nunca decir no"_ contract
2. [**roadmap.md**](./roadmap.md) — v0 / v1 / v2 scope with skill priorities
3. [**architecture.md**](./architecture.md) — System diagrams, state machine, sequence flows
4. [**protocol.md**](./protocol.md) — WebSocket contract between client and server
5. [**skills/README.md**](./skills/README.md) — How skills work, how to author one
6. [**skills/audiobooks.md**](./skills/audiobooks.md) — The v0 skill spec
7. [**decisions.md**](./decisions.md) — Architectural decision log (ADRs)

## Design specs (in flight)

- [**turns.md**](./turns.md) — Turn-based audio coordination spec. How a tool call's side-effect audio is sequenced after the model's verbal acknowledgement. Not yet implemented — this is the design under review. See [ADR 2026-04-13 — Turn-based coordinator for voice tool calls](./decisions.md#2026-04-13--turn-based-coordinator-for-voice-tool-calls).

## Ownership

Claude maintains this folder. Mario is the source of truth for product direction.

**Hard rule**: any code change that invalidates a doc must update the doc in the same commit. No stale docs.

## Where this fits in the repo

- `docs/` — product + architecture + protocol (this folder)
- `/CLAUDE.md` — quick-start for Claude and Mario; methodology; pointers into `docs/`
- `server/` and `web/` — implementation; the "how" lives here, the "why" does not

When in doubt about where something belongs: **product or architectural _why_ → `docs/`. How-to-run or methodology → `CLAUDE.md`. Implementation detail → code comments.**
