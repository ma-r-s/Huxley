# Huxley — Documentation

Single source of truth for what Huxley is, what it does, and how it's built. Code comments describe _what_; this folder describes _why_ and _what we're aiming at_.

## Reading order

1. [**vision.md**](./vision.md) — what Huxley is, who it's for, what it's not
2. [**concepts.md**](./concepts.md) — vocabulary: persona, skill, tool, turn, side effect, factory, voice provider, constraint, client
3. [**architecture.md**](./architecture.md) — system diagrams, state machine, sequence flows
4. [**protocol.md**](./protocol.md) — WebSocket contract between client and framework
5. [**observability.md**](./observability.md) — logging conventions and the diagnose-from-logs workflow
6. [**skills/README.md**](./skills/README.md) — how skills work, how to author one
7. [**personas/README.md**](./personas/README.md) — how to write a persona
8. [**extensibility.md**](./extensibility.md) — what kinds of skills the framework supports today, where the real limits are
9. [**roadmap.md**](./roadmap.md) — framework + persona roadmaps
10. [**decisions.md**](./decisions.md) — architectural decision log (ADRs)
11. [**verifying.md**](./verifying.md) — end-to-end smoke-test for a fresh checkout / reviewer
12. [**review-notes.md**](./review-notes.md) — honest self-assessment for a third-party reviewer: known rough edges, non-issues, how to handle feedback

## Worked examples

- [**personas/abuelos.md**](./personas/abuelos.md) — canonical persona spec (Spanish-language assistant for an elderly blind user)
- [**skills/audiobooks.md**](./skills/audiobooks.md) — first-party skill spec
- [**turns.md**](./turns.md) — turn coordinator spec

## Research notes

- [**research/sonic-ux.md**](./research/sonic-ux.md) — codified rules and synthesized framework for sonic UI / earcon design (Brewster, NPR, Karen Collins, NN/g)

## Hard rule

Any code change that invalidates a doc must update the doc in the same commit. No stale docs.

## Where this fits in the repo

- `docs/` — product + architecture + protocol (this folder)
- `/README.md` — repo entry point: what Huxley is, install, run, link to docs
- `/CLAUDE.md` — methodology and conventions for contributors and AI collaborators
- `packages/` — implementation; the _how_ lives here
- `web/` — dev client (browser mic + speaker over WebSocket)

When in doubt about where something belongs: **product or architectural _why_ → `docs/`. Repo-wide methodology → `CLAUDE.md`. Implementation detail → code comments.**
