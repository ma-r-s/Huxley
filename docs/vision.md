# Vision

## Who this is for

One user: **Mario's grandfather**. 90 years old, blind, lives in Villavicencio (Colombia). Speaks only Spanish with a heavy llanero register. Zero technical literacy and does not care what an "AI assistant" is. He needs a helper, not a product.

**Other impairments**: none — only blindness.

**What he cares about**: being heard, being helped, not being told _no_.

## Why existing assistants fail him

1. **Wake-word rigidity** — _"Hey Google" / "Alexa"_ require precise enunciation and timing. He has neither the patience nor the precision.
2. **Exact-phrase brittleness** — if you don't say the command the way the system expects, it fails. Elderly users don't adapt to systems; systems should adapt to them.
3. **English bias** — Spanish is second-class in most assistants; llanero idiom is unsupported.
4. **Dead-end "no"** — _"Lo siento, no puedo ayudar con eso"_ is the worst possible response for a blind, isolated user. It feels like rejection from the one thing that's supposed to help.

AbuelOS exists because no off-the-shelf product works for him.

## The "nunca decir no" contract

**This is the hardest rule in the project. It applies to every skill, every tool, every error message, and the system prompt itself.**

1. **No dead-end negatives.** A tool must never return just _"not available" / "not found" / "error."_ Every negative response must include an alternative, a clarifying question, or an offer to relay to Mario.

2. **Unknown asks get warm acknowledgement, never silence.** If he asks for something no skill handles (_"quiero desayuno"_), the assistant must respond with something like _"No puedo ayudarle con eso todavía, don. ¿Quiere que le avise a Mario?"_ — never _"comando no reconocido."_

3. **Errors wrapped in plain Spanish.** He never hears "error 500" or any technical word. Failures become _"Algo no funcionó. Déjeme intentarlo de nuevo."_

4. **Silence is a bug.** The system must always produce audio when expected. For a blind user, silence = the device is broken. Any backend delay must have audible feedback.

### How the contract is enforced

- **Skill layer**: every `ToolResult.output` JSON includes a `message` field phrased as an action, not a failure. See [`skills/README.md`](./skills/README.md#the-nunca-decir-no-contract--skill-author-rules) for the author rules.
- **LLM layer**: the system prompt teaches the model to turn unknown asks into _"todavía no puedo, pero…"_ style responses and to forward bigger things to Mario.
- **Client layer**: the browser/ESP32 client must play a status cue ("procesando…" or similar) while waiting for a response. Dead air = bug.

## Persona

| Attribute   | Value                                                                                  |
| ----------- | -------------------------------------------------------------------------------------- |
| Tratamiento | _usted_, formal but warm                                                               |
| Ritmo       | pausado, claro                                                                         |
| Tono        | cálido, paciente, nunca condescendiente                                                |
| Registro    | español colombiano; modismos llaneros bienvenidos, nunca forzados                      |
| Nombre      | ninguno por ahora — abierto, baja prioridad                                            |
| Auto-imagen | _"soy un ayudante"_, nunca _"soy una inteligencia artificial"_ a menos que él pregunte |

The full system prompt lives in [`server/src/abuel_os/config.py`](../server/src/abuel_os/config.py) as `Settings.system_prompt`. Any change to the persona above must land in the system prompt in the same commit.

## Success criteria for v1

From Mario, verbatim:

> _"The moment I can speak to the assistant and it helps me find a book, listen to it, and move forward, backwards, stop and resume another time — that's v1 done."_

Concretely, v1 ships when **all** of these work end-to-end via voice only, with no technical help:

- [ ] Search for a book by natural phrase (_"busca el libro de García Márquez sobre el coronel"_)
- [ ] Start playback from a search result, or have the LLM pick the obvious top match
- [ ] Pause / resume mid-sentence
- [ ] Navigate forward / backward (by seconds, minutes, or chapters)
- [ ] Stop playback
- [ ] Resume later (_"sigue con el libro"_) — persists across sessions and app restarts
- [ ] Every negative response offers an alternative (see the contract above)

The full v1 checklist including implementation gaps is in [`roadmap.md`](./roadmap.md#v1--the-mvp-marios-bar) and [`skills/audiobooks.md`](./skills/audiobooks.md#gaps--todo-for-v1).

## Non-goals for v1

- Wake word / always-on listening — PTT only
- Proactive / unprompted speech — strictly turn-based
- Multi-user / multi-client
- Languages other than Spanish
- Religious content — explicitly excluded
- ESP32 hardware — browser is the v1 client; ESP32 is v∞
- Offline mode
- Privacy / no-log mode
- Error recovery as a P0 concern (handled in v2)
