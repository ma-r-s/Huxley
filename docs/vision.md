# Huxley

> Voice agent framework. Personas declare who the agent is; skills declare what it does.

## What Huxley is

Huxley is an open-source Python framework for building voice-first AI agents. You give it a **persona** (a YAML file with name, voice, language, personality, and a list of enabled skills) and a set of **skills** (Python packages that expose tools to the LLM), and it does the rest: listens, understands, reasons, acts, speaks back.

A persona could be a companion for an elderly user, a tutor for a kid, a hands-free assistant for a delivery driver, a nurse's clinical sidekick. Each is configuration, not a different product.

A skill could be playing audiobooks, controlling lights, sending messages, looking up a recipe, dialling a phone. Each is a small Python package; anyone can write one.

The dream: **adding a capability to your voice agent should be as easy as `pip install huxley-skill-lights` plus one line in your persona file.**

**Audio-first by default.** The framework guarantees that every meaningful event the agent produces has an audible trail — earcons before proactive speech, confirmations on every state change, explicit acknowledgments of failures. No visual-only failure modes. A blind user should never be in a state where something happened and they don't know.

## What Huxley is not

- **Not a chatbot.** Voice-first means the conversation runs in real time, with interruption, with side effects (audio playback, notifications), not turn-by-turn text.
- **Not Alexa.** No walled garden, no certification fees, no centralized cloud lock-in. Skills are open code; personas are your config.
- **Not multi-user.** One person talks to one agent. Multi-tenant SaaS is a different product, out of scope.
- **Not a model.** Huxley wraps OpenAI's Realtime API today; the architecture leaves room for other providers, but Huxley itself doesn't train or serve models.

## Who it's for

Three audiences, three different journeys:

**Persona owners** (most users): you want a voice assistant tailored to a specific use case. You write a `persona.yaml`, pick the skills you need, run the server. No Python required for most of this.

**Skill authors** (a smaller, growing community): you want your voice agent to do something new — turn off the porch light, read your unread emails, narrate the weather. You write a Python package using the Huxley SDK. Anyone whose persona enables your skill can use it.

**Framework contributors** (a small core): you make Huxley itself better — better audio handling, more provider integrations, better DX for the other two groups.

## Status

Huxley is pre-1.0. The framework runs end-to-end against a browser dev client. ESP32 hardware support is planned. Skills are being extracted into their own installable packages; the persona YAML loader and constraint registry are next. See [`roadmap.md`](./roadmap.md) for what works today, what's in flight, and what's deferred.

The first persona shipped on Huxley is **Abuelo** — a Spanish-language assistant for an elderly blind user, built around a "never say no" behavioral constraint. Its spec lives in [`server/personas/abuelos.md`](./personas/abuelos.md) and serves as the worked example for everything in this repo.
