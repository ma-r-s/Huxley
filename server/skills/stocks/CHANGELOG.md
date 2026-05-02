# Changelog

## 0.1.0 — 2026-05-01

Initial release. The reference third-party skill for Huxley's T1.14 marketplace v1.

### Added

- `StocksSkill` with three voice tools: `get_stock_price`, `get_watchlist_summary`, `compare_stocks`.
- Alpha Vantage provider (`AlphaVantageClient`) with classified error surface: `RateLimitError`, `AuthError`, `UnknownTickerError`, `ProviderError`.
- `config_schema` declaring `api_key` (secret), `watchlist` (array), `currency` (enum) — the three JSON-Schema shapes Huxley's PWA Skills panel will render.
- `data_schema_version = 1`.
- 40 tests (provider HTTP surface + skill setup/dispatch + config_schema invariants).
- CI workflow: ruff + ruff-format + mypy --strict + pytest.

### Notes

- Depends on `huxley-sdk>=0.1.1,<0.2` — published to PyPI alongside this skill. Inside the Huxley monorepo workspace, the dep resolves locally for dev-time iteration; in any external venv, `uv add` pulls both from PyPI.
- No automatic `data_schema_version` migration — the Huxley runtime logs `skill.schema.upgrade_needed` on bump; consult this CHANGELOG for migration steps when applicable.
