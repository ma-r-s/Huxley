# Skill directory

Curated list of known `huxley-skill-*` packages. Use this to discover skills you can install into a Huxley persona.

> **Want to add your skill?** Open a PR adding a row below. Include all five fields per the [Per-entry metadata](#per-entry-metadata) spec; Mario reviews before merging. Inclusion bar: package installs cleanly, declares `config_schema` if it has user-tunable fields, has a public docs page with at least the install command and one example voice intent.

## Skills

| Name                      | Description                                                                                                                              | Install                      | Docs                                                                                   | Tier        |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | -------------------------------------------------------------------------------------- | ----------- |
| `huxley-skill-audiobooks` | Local-library audiobook playback with resume-position persistence.                                                                       | bundled                      | [audiobooks.md](audiobooks.md)                                                         | first-party |
| `huxley-skill-news`       | Headlines from Google News RSS + Open-Meteo weather card.                                                                                | bundled                      | [news.md](news.md)                                                                     | first-party |
| `huxley-skill-radio`      | HTTP / Icecast radio streaming via ffmpeg.                                                                                               | bundled                      | [radio.md](radio.md)                                                                   | first-party |
| `huxley-skill-reminders`  | Persistent scheduled reminders with retry escalation.                                                                                    | bundled                      | [reminders.md](reminders.md)                                                           | first-party |
| `huxley-skill-search`     | Open-web search via DuckDuckGo (no API key).                                                                                             | bundled                      | [search.md](search.md)                                                                 | first-party |
| `huxley-skill-system`     | Volume control + current time.                                                                                                           | bundled                      | —                                                                                      | first-party |
| `huxley-skill-telegram`   | Full-duplex Telegram voice calls + text messaging.                                                                                       | bundled                      | [telegram.md](telegram.md)                                                             | first-party |
| `huxley-skill-timers`     | One-shot relative timers ("remind me in 5 minutes").                                                                                     | bundled                      | [timers.md](timers.md)                                                                 | first-party |
| `huxley-skill-stocks`     | Voice-controlled stock quotes via Alpha Vantage. The reference third-party skill — see [authoring.md](authoring.md) for the walkthrough. | `uv add huxley-skill-stocks` | [github.com/ma-r-s/huxley-skill-stocks](https://github.com/ma-r-s/huxley-skill-stocks) | community   |

## Per-entry metadata

Each row is required to have:

| Field         | Type                                                                | Example                                            |
| ------------- | ------------------------------------------------------------------- | -------------------------------------------------- |
| `name`        | PyPI package name                                                   | `huxley-skill-stocks`                              |
| `description` | One sentence (under 120 chars)                                      | "Voice-controlled stock quotes via Alpha Vantage." |
| `install`     | Code-formatted command, or `bundled` if first-party                 | `uv add huxley-skill-stocks`                       |
| `docs_url`    | Link to a public docs page (the package's README on GitHub is fine) | `https://github.com/ma-r-s/huxley-skill-stocks`    |
| `tier`        | Either `first-party` (in this repo) or `community` (anyone else)    | `community`                                        |

The directory is a static page for v1. v2 will promote it to a structured registry (a separate `huxley/skills` GitHub repo with `index.json`, JSON Schema, CI, and a Marketplace tab in the PWA). Until then, this page is the registry.

## Submitting your skill

1. Build and publish your package to PyPI (or self-host on `git+https://...`).
2. Make sure your README has the install command and at least one example voice intent.
3. Open a PR to [Huxley](https://github.com/ma-r-s/Huxley) adding a row to the table above. Include all five fields from the metadata schema.

The author of [`huxley-skill-stocks`](https://github.com/ma-r-s/huxley-skill-stocks) — the canonical reference skill — is the worked example. If your skill follows the same project layout and conventions (see [authoring.md](authoring.md)), the PR review is a quick read.

## See also

- [authoring.md](authoring.md) — the build-your-first-skill walkthrough.
- [README.md](README.md) — full SDK API reference for skill authors.
- [`docs/skill-marketplace.md`](../skill-marketplace.md) — architectural contract; how the marketplace will evolve in v2.
