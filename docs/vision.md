# Huxley

> The voice agent framework. Built first for one grandfather. Now anyone's.

## What Huxley is

Huxley is an open-source platform for building voice-first AI agents. You give it a **persona** (who the agent is) and a set of **skills** (what the agent can do), and it does the rest: listens, understands, reasons, acts, speaks back.

A persona could be a companion for a grandparent who's losing their sight. A tutor for a kid. A hands-free assistant for a delivery driver. A nurse's clinical sidekick. Each is configuration, not a different product.

A skill could be playing audiobooks, controlling lights, sending messages, looking up a recipe, calling someone. Each is a small Python package; anyone can write one.

The dream: **adding a capability to your voice agent should be as easy as `pip install huxley-skill-lights` plus one line in your persona file.**

## What Huxley is not

- **Not a chatbot.** Voice-first means the conversation runs in real time, with interruption, with side effects (audio playback, notifications), not turn-by-turn text.
- **Not Alexa.** No walled garden, no certification fees, no centralized cloud lock-in. Skills are open code; personas are your config.
- **Not multi-user.** One person talks to one agent. Multi-tenant SaaS is a different product, out of scope.
- **Not a model.** Huxley uses OpenAI's Realtime API today; the architecture leaves room for other providers, but Huxley itself doesn't train or serve models.

## Who it's for

Three audiences, three different journeys:

**Persona owners** (most users): you want a voice assistant tailored to someone in your life. You write a `persona.yaml`, pick the skills they need, run the server. No Python required for most of this.

**Skill authors** (a smaller, growing community): you want your voice agent to do something new — turn off the porch light, read your unread emails, narrate the weather. You write a Python package using the Huxley SDK. Anyone whose persona enables your skill can use it.

**Framework contributors** (a small core): you make Huxley itself better — better audio handling, more provider integrations, better DX for the other two groups.

## Origin

Huxley exists because Mario's 90-year-old grandfather in Villavicencio, Colombia is blind, and the technology marketed to "elderly users" treats him like a problem to manage rather than a person to help. There was no off-the-shelf product that let him hold a button, ask for the audiobook from last night, and have it just play — in his llanero Spanish, without ever needing to figure out a screen.

So Mario built one. The first persona — **AbuelOS**, a Spanish-language companion built around the rule "never tell the user no" — is named after him. Its full spec lives in [`personas/abuelos.md`](./personas/abuelos.md).

Huxley the framework grew out of that work. Anyone else's persona is just a different config.

## Status

Huxley is pre-1.0. The framework runs end-to-end for the AbuelOS persona on a browser dev client. ESP32 hardware support is planned. The skill SDK is being extracted from the framework into its own package; once that lands, third-party skills become first-class.

What works today, what's in flight, and what's deferred lives in [`roadmap.md`](./roadmap.md).
