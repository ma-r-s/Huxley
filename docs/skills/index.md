# Skill directory

Curated list of known `huxley-skill-*` packages. Use this to discover skills you can install into a Huxley persona.

> **The framework ships empty.** Every skill — first-party or community — is an independent PyPI package installed via `uv add`. The Huxley repo's workspace is a development convenience for iterating on the SDK + first-party skills together; the canonical distribution path is identical for everyone.

> **Want to add your skill?** Open a PR adding a row below. Include all five fields per the [Per-entry metadata](#per-entry-metadata) spec; Mario reviews before merging. Inclusion bar: package installs cleanly, declares `config_schema` if it has user-tunable fields, has a public docs page with at least the install command and one example voice intent.
>
> v2's structured registry now lives at [`ma-r-s/huxley-registry`](https://github.com/ma-r-s/huxley-registry) — a separate repo with [`index.json`](https://github.com/ma-r-s/huxley-registry/blob/main/index.json), [JSON Schema](https://github.com/ma-r-s/huxley-registry/blob/main/schema.json), and PR-driven curation. Clients (the PWA Marketplace tab, when it lands) fetch the canonical feed from there. This static markdown page stays as a human-readable mirror.

## Skills

| Name                      | Description                                                                                                                              | Install                          | Docs                                                                                   | Tier        |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | -------------------------------------------------------------------------------------- | ----------- |
| `huxley-skill-audiobooks` | Local-library audiobook playback with resume-position persistence.                                                                       | `uv add huxley-skill-audiobooks` | [audiobooks.md](audiobooks.md)                                                         | first-party |
| `huxley-skill-news`       | Headlines from Google News RSS + Open-Meteo weather card.                                                                                | `uv add huxley-skill-news`       | [news.md](news.md)                                                                     | first-party |
| `huxley-skill-radio`      | HTTP / Icecast radio streaming via ffmpeg.                                                                                               | `uv add huxley-skill-radio`      | [radio.md](radio.md)                                                                   | first-party |
| `huxley-skill-reminders`  | Persistent scheduled reminders with retry escalation.                                                                                    | `uv add huxley-skill-reminders`  | [reminders.md](reminders.md)                                                           | first-party |
| `huxley-skill-search`     | Open-web search via DuckDuckGo (no API key).                                                                                             | `uv add huxley-skill-search`     | [search.md](search.md)                                                                 | first-party |
| `huxley-skill-system`     | Volume control + current time.                                                                                                           | `uv add huxley-skill-system`     | —                                                                                      | first-party |
| `huxley-skill-telegram`   | Full-duplex Telegram voice calls + text messaging.                                                                                       | `uv add huxley-skill-telegram`   | [telegram.md](telegram.md)                                                             | first-party |
| `huxley-skill-timers`     | One-shot relative timers ("remind me in 5 minutes").                                                                                     | `uv add huxley-skill-timers`     | [timers.md](timers.md)                                                                 | first-party |
| `huxley-skill-stocks`     | Voice-controlled stock quotes via Alpha Vantage. The reference third-party skill — see [authoring.md](authoring.md) for the walkthrough. | `uv add huxley-skill-stocks`     | [github.com/ma-r-s/huxley-skill-stocks](https://github.com/ma-r-s/huxley-skill-stocks) | community   |

The `tier` distinction is curation-only: `first-party` skills are maintained in the Huxley repo's workspace by the framework's authors; `community` skills are maintained elsewhere. Both install identically.

## Per-entry metadata

Each row is required to have:

| Field         | Type                                                                | Example                                            |
| ------------- | ------------------------------------------------------------------- | -------------------------------------------------- |
| `name`        | PyPI package name                                                   | `huxley-skill-stocks`                              |
| `description` | One sentence (under 120 chars)                                      | "Voice-controlled stock quotes via Alpha Vantage." |
| `install`     | The exact `uv add` command (code-formatted)                         | `uv add huxley-skill-stocks`                       |
| `docs_url`    | Link to a public docs page (the package's README on GitHub is fine) | `https://github.com/ma-r-s/huxley-skill-stocks`    |
| `tier`        | Either `first-party` (workspace-maintained) or `community`          | `community`                                        |

## Submitting your skill

1. Build and publish your package to PyPI (or self-host on `git+https://...`).
2. Make sure your README has the install command and at least one example voice intent.
3. Open a PR to [Huxley](https://github.com/ma-r-s/Huxley) adding a row to the table above. Include all five fields from the metadata schema.

The author of [`huxley-skill-stocks`](https://github.com/ma-r-s/huxley-skill-stocks) — the canonical reference skill — is the worked example. If your skill follows the same project layout and conventions (see [authoring.md](authoring.md)), the PR review is a quick read.

## See also

- [authoring.md](authoring.md) — the build-your-first-skill walkthrough.
- [installing.md](installing.md) — the operator-side install + smoke-test recipe.
- [README.md](README.md) — full SDK API reference for skill authors.
- [`docs/skill-marketplace.md`](../skill-marketplace.md) — architectural contract; how the marketplace will evolve in v2.
