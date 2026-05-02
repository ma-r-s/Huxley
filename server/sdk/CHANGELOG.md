# Changelog

## 0.1.1 — 2026-05-02

### Fixed

- `SkillSecrets` is now exported from `huxley_sdk.__init__` (it was added to `huxley_sdk.types` in 0.1.0 but never re-exported, so `from huxley_sdk import SkillSecrets` raised `ImportError`). Caught by the TestPyPI smoke install. No other behavior changed.

## 0.1.0 — 2026-05-02

First public release. The SDK every Huxley skill builds against.

### Added

- `Skill` Protocol — the structural-typing contract a skill class implements (name, tools, handle; optional setup, reconfigure, teardown, prompt_context).
- `SkillContext` — framework dependency injection at setup time (logger, storage, secrets, persona_data_dir, language, inject_turn, inject_turn_and_wait, background_task, start_input_claim, cancel_active_claim, subscribe_client_event, emit_server_event, language).
- `ToolDefinition` — OpenAI function-call schema for skill tools.
- `ToolResult` — structured return shape skills produce; supports JSON-serialized payload + optional side effect.
- `SkillSecrets` — async `get/set/delete/keys` over per-persona JSON file at `<persona>/data/secrets/<skill>/values.json`. Flat `dict[str, str]` shape; skills JSON-encode nested OAuth blobs into a single key.
- `SkillStorage` — namespaced async KV adapter for persistent per-skill state.
- Side-effect types: `AudioStream` (streamed PCM playback), `PlaySound` (one-shot earcon), `InputClaim` (mic/speaker takeover for full-duplex audio), `CancelMedia`, `SetVolume`.
- `InjectTurn` / `InjectTurnAndWait` — proactive speech with priority + dedup-key.
- `ClaimEndReason`, `ClaimHandle`, `ClaimBusyError` — input-claim lifecycle.
- `Catalog` — personal-content fuzzy matcher (used by the audiobooks skill).
- `huxley_sdk.audio.load_pcm_palette` — helper for skills that load WAV-format earcons.
- `huxley_sdk.testing` — `make_test_context()` builder for unit tests + `FakeSkill` for framework tests.
- `SkillRegistry` — collects tool definitions, dispatches by tool name; the framework wires this up but skill authors don't usually touch it directly.

### Class-level metadata (T1.14)

- `Skill.config_schema: ClassVar[dict | None]` — optional JSON Schema 2020-12 the v2 PWA renders forms from. Two custom extensions: `"format": "secret"` routes a string field to `ctx.secrets`; `"x-huxley:help"` carries markdown help text.
- `Skill.data_schema_version: ClassVar[int]` — bumps when persisted shape changes; framework warns on mismatch.

### Compatibility

- Python 3.13+ (matches Huxley runtime).
- Single dependency: `structlog>=24.4` (logger Protocol is satisfied by `structlog.BoundLogger`).
