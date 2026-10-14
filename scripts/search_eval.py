"""T1.48 eval: does search (and news/weather) return the fact grandpa asked for?

Live network, no LLM. Each case is a question as grandpa would say it,
the query a model would plausibly send, and the expected fact, written
out so a reader can check it, with a regex that finds it in the tool
output. The script runs whichever `huxley_skill_search` /
`huxley_skill_news` is importable, so the same file measures the old
code and the new one:

    # new code (this checkout)
    uv run --package huxley-skill-search --with huxley-skill-news \\
        python scripts/search_eval.py run --label new --out new.json
    # old code: a checkout of the previous commit on PYTHONPATH
    git worktree add /tmp/old <old-sha>
    PYTHONPATH=/tmp/old/server/skills/search/src:/tmp/old/server/skills/news/src \\
        python scripts/search_eval.py run --label old --out old.json
    # side by side
    python scripts/search_eval.py report old.json new.json

It is a script, not a test: pytest never collects `scripts/`, and it
hits real search engines and sites (about 2 minutes, one question every
couple of seconds). 15 search questions were used while building T1.48;
3 more (`held_out`) were added after, and nothing was tuned on them.
Engines answer differently from one run to the next, so compare several
runs, not one. Expected facts are as of 2026-10-13; they rot, so
re-check them before trusting an old number. The regex is a first pass;
the report marks what was judged by reading (see the `judged` notes in
docs/triage.md T1.48).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Abuelo's config (server/personas/abuelos/persona.yaml), copied so the
# eval doesn't need the runtime. Old code ignores the keys it doesn't know.
SEARCH_CONFIG: dict[str, Any] = {
    "safesearch": "moderate",
    "region": "co-es",
    "timezone": "America/Bogota",
    "preferred_domains": [
        "eltiempo.com",
        "elespectador.com",
        "semana.com",
        "caracol.com.co",
        "noticiascaracol.com",
        "bluradio.com",
        "noticiasrcn.com",
        "lasillavacia.com",
        "larepublica.co",
        "portafolio.co",
        "elcolombiano.com",
        "infobae.com",
        "bbc.com",
        "cnnespanol.cnn.com",
        "es.wikipedia.org",
    ],
}
NEWS_CONFIG: dict[str, Any] = {
    "location": "Villavicencio",
    "latitude": 4.142,
    "longitude": -73.626,
    "country_code": "CO",
    "language_code": "es",
    "units": "metric",
    "interests": ["politica", "local"],
    "max_items": 8,
    "local_query": 'Villavicencio OR "del Meta"',
}


@dataclass(frozen=True)
class Case:
    id: str
    question: str  # what grandpa says
    tool: str  # search_the_web | get_weather | get_news
    args: dict[str, Any]
    expected: str  # the fact, for a human reader
    pattern: str  # regex (case-insensitive) that finds it in the output
    # Regex for half the answer (the winner without the score): "partial".
    partial: str | None = None
    # False when the persona says to answer without searching (a recipe).
    needs_search: bool = True
    # Added after tuning (critic, T1.48): nothing was tuned on these.
    held_out: bool = False


CASES: tuple[Case, ...] = (
    Case(
        "match",
        "¿Cómo quedó la selección femenina contra Venezuela?",
        "search_the_web",
        {"query": "resultado Colombia femenina Venezuela"},
        "Colombia won 2-1 (friendly in Venezuela, 13 Oct 2026)",
        r"\b2\s*-\s*1\b|\b2 a 1\b",
        partial=r"volvi[oó] a ganarle|venci[oó] a Venezuela|gan[oó] a Venezuela",
    ),
    Case(
        "president",
        "¿Quién es el presidente de Colombia?",
        "search_the_web",
        {"query": "presidente de Colombia actual"},
        "Abelardo de la Espriella (in office since 7 Aug 2026)",
        r"Espriella",
    ),
    Case(
        "dollar",
        "¿A cómo está el dólar hoy?",
        "search_the_web",
        {"query": "precio del dólar hoy Colombia"},
        "TRM for Tue 13 Oct 2026: about $3,194 (dolar-colombia.com: 3.194,44)",
        r"3[.,]?19[0-9][.,]\d",
    ),
    Case(
        "age",
        "¿Cuántos años tiene Shakira?",
        "search_the_web",
        {"query": "edad de Shakira"},
        "49 (born 2 Feb 1977)",
        r"49 a[ñn]os|2 de febrero de 1977",
    ),
    Case(
        "death",
        "¿Es verdad que se murió Piero, el de 'Mi viejo'?",
        "search_the_web",
        {"query": "murió Piero cantante Mi viejo"},
        "Yes: Piero died on 7 Oct 2026 in Buenos Aires, aged 81",
        r"\b81 a[ñn]os|\b7 de octubre",
    ),
    Case(
        "holiday",
        "¿Cuándo es el próximo festivo?",
        "search_the_web",
        {"query": "próximo festivo en Colombia"},
        "Monday 2 Nov 2026 (Todos los Santos)",
        r"\b2 de noviembre",
    ),
    Case(
        "tv",
        "¿A qué hora juega Millonarios hoy y por qué canal lo pasan?",
        "search_the_web",
        # No opponent in the query: the model doesn't know who they play.
        {"query": "a qué hora juega Millonarios hoy canal"},
        "Kick-off 8:30 p.m. Tue 13 Oct at El Campín (Caracol Radio and La Patria agree); "
        "the channel (Win Sports) counts as a bonus, not required",
        r"\b8:30\s*(p\.?\s?m|de la noche)",
    ),
    Case(
        "recipe",
        "¿Cómo se prepara un ajiaco?",
        "search_the_web",
        {"query": "receta ajiaco santafereño"},
        "Chicken, three potatoes incl. papa criolla, guascas, corn",
        r"guascas",
        needs_search=False,  # the persona answers recipes from memory
    ),
    Case(
        "health",
        "¿Qué es bueno para el dolor de estómago?",
        "search_the_web",
        {"query": "remedios para el dolor de estómago"},
        "General advice (fluids, bland food) and see a doctor if strong or persistent",
        r"(consult|acud|visit|ir |ve )\w* ?(a|al) (un |tu |su )?m[ée]dico|atenci[oó]n m[ée]dica",
    ),
    Case(
        "medication",
        "¿Puedo tomar ibuprofeno si tomo losartán?",
        "search_the_web",
        {"query": "ibuprofeno y losartán interacción"},
        "Interaction: ibuprofen can weaken losartan and strain the kidneys; ask the doctor",
        r"ri[ñn][oó]n|renal|resta efecto|reduc\w* (el )?efecto|interacci[oó]n cl[ií]nicamente "
        r"relevante|es arriesgado",
    ),
    Case(
        "pope",
        "¿Cómo sigue el papa?",
        "search_the_web",
        # No name in the query: the voice model may not know the new pope.
        {"query": "cómo sigue la salud del papa"},
        "Diagnosed with lung cancer after lung surgery (13 Oct 2026)",
        r"c[áa]ncer",
    ),
    Case(
        "quake",
        "¿Hubo un temblor hoy?",
        "search_the_web",
        {"query": "temblor hoy Colombia"},
        "Magnitude 5.0 on 13 Oct 2026, felt in Bucaramanga and other cities",
        r"magnitud (de )?5[,.]0\b|\b5[,.]0 (de magnitud|grados)",
    ),
    Case(
        "cinema",
        "¿Qué películas están dando en el cine en Villavicencio?",
        "search_the_web",
        {"query": "cartelera cine Villavicencio hoy"},
        "At least one film title showing today at a Villavicencio cinema",
        r"(?!x)x",  # judged by reading: no single string marks a film title
    ),
    Case(
        "plates",
        "¿Hay pico y placa hoy en Villavicencio?",
        "search_the_web",
        {"query": "pico y placa Villavicencio hoy"},
        "Tuesday: private cars ending in 7 and 8 (Infobae, 13 Oct, citing the 2026 decree)",
        r"martes:? 7 y 8|7 y 8",
    ),
    Case(
        "status",
        "¿Nairo Quintana sigue corriendo?",
        "search_the_web",
        {"query": "Nairo Quintana sigue corriendo"},
        "No: he said goodbye to pro cycling (farewell event, Oct 2026)",
        r"despedid|despidi|retir[oó]|punto final|[úu]ltima carrera|adi[oó]s",
    ),
    Case(
        "euro",
        "¿Y el euro a cómo está?",
        "search_the_web",
        {"query": "precio del euro hoy Colombia"},
        "The euro in pesos for 13 Oct 2026, from a dated source (judged by reading)",
        r"(?!x)x",
        held_out=True,
    ),
    Case(
        "mayor",
        "¿Quién es el alcalde de Villavicencio?",
        "search_the_web",
        {"query": "alcalde de Villavicencio"},
        "Alexander Baquero (elected Oct 2023, term 2024-2027)",
        r"Baquero",
        held_out=True,
    ),
    Case(
        "fuel",
        "¿Cuánto vale el galón de gasolina?",
        "search_the_web",
        {"query": "precio galón gasolina Colombia hoy"},
        "The price per gallon in October 2026, from a dated source (judged by reading)",
        r"(?!x)x",
        held_out=True,
    ),
    Case(
        "weather_tomorrow",
        "¿Va a llover mañana?",
        "get_weather",
        {},
        "Tomorrow's forecast with a rain chance (routes to get_weather, not search)",
        r"\"tomorrow\"",
    ),
    Case(
        "news_today",
        "¿Qué pasó hoy en las noticias?",
        "get_news",
        {},
        "Today's top stories (routes to get_news): e.g. the Pope's diagnosis, the quake",
        r"papa|temblor|sismo",
    ),
)


def _strip_urls(value: Any) -> Any:
    """Drop `url` fields so a URL can't satisfy the regex."""
    if isinstance(value, dict):
        return {k: _strip_urls(v) for k, v in value.items() if k != "url"}
    if isinstance(value, list):
        return [_strip_urls(v) for v in value]
    return value


def _sources(data: dict[str, Any]) -> list[str]:
    out = [h.get("source", "") for h in data.get("headlines", [])]
    out += [r.get("source", "") for r in data.get("results", [])]
    out += [i.get("source", "") for i in data.get("items", [])]
    return [s for s in out if s][:6]


async def run(label: str, out_path: Path, only: set[str] | None) -> None:
    from huxley_sdk.testing import make_test_context
    from huxley_skill_news.skill import NewsSkill
    from huxley_skill_search.skill import SearchSkill

    search = SearchSkill()
    search_ctx = make_test_context(config=dict(SEARCH_CONFIG), language="es")
    await search.setup(search_ctx)
    # make_test_context's logger is a mock; its calls are the skill's log.
    log_calls: list[Any] = search_ctx.logger.ainfo.await_args_list  # type: ignore[attr-defined]
    news = NewsSkill()
    await news.setup(make_test_context(config=dict(NEWS_CONFIG), language="es"))

    rows: list[dict[str, Any]] = []
    for case in CASES:
        if only and case.id not in only:
            continue
        skill = search if case.tool == "search_the_web" else news
        logged_before = len(log_calls)
        t0 = time.perf_counter()
        result = await skill.handle(case.tool, dict(case.args))
        latency = time.perf_counter() - t0
        data = json.loads(result.output)
        text = json.dumps(_strip_urls(data), ensure_ascii=False)
        hit = bool(re.search(case.pattern, text, re.IGNORECASE))
        partial = bool(not hit and case.partial and re.search(case.partial, text, re.IGNORECASE))
        # The skill's own log lines for this call (backend, pages, lanes),
        # so a miss can be explained without re-running.
        events = [
            {"event": c.args[0], **{k: v for k, v in c.kwargs.items() if k != "query_hash"}}
            for c in log_calls[logged_before:]
            if c.args and str(c.args[0]).startswith("search.")
        ]
        rows.append(
            {
                "id": case.id,
                "events": events,
                "tool": case.tool,
                "latency_s": round(latency, 2),
                "hit": hit,
                "partial": partial,
                "chars": len(result.output),
                "failure": data.get("say_to_user"),
                "sources": _sources(data),
                "output": data,
            }
        )
        print(
            f"{case.id:17s} {latency:5.2f}s hit={hit!s:5s} chars={len(result.output):5d} "
            f"{_sources(data)[:4]}",
            flush=True,
        )
        await asyncio.sleep(2.0)  # be polite to the engines

    await search.teardown()
    await news.teardown()
    out_path.write_text(
        json.dumps({"label": label, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _verdict(row: dict[str, Any], label: str, judged: dict[str, dict[str, Any]]) -> str:
    """'yes' | 'partial' | 'no' | 'FAIL', a reading override first."""
    override = judged.get(label, {}).get(row["id"])
    if override is not None:
        return override if isinstance(override, str) else ("yes" if override else "no")
    if row["hit"]:
        return "yes"
    if row.get("partial"):
        return "partial"
    return "FAIL" if row["failure"] else "no"


def _p75(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.75 * (len(ordered) - 1)))] if ordered else 0.0


def report(paths: list[Path], judged: dict[str, dict[str, Any]]) -> None:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    by_id = [{r["id"]: r for r in run["rows"]} for run in runs]
    labels = [run["label"] for run in runs]
    header = "| case | expected | " + " | ".join(f"{lb} | {lb} s" for lb in labels) + " |"
    print(header)
    print("|" + "---|" * (2 + 2 * len(labels)))
    for case in CASES:
        name = case.id + (" (held out)" if case.held_out else "")
        name += "" if case.needs_search else " (no search needed)"
        cells = [name, case.expected]
        for label, rows in zip(labels, by_id, strict=True):
            row = rows.get(case.id)
            if row is None:
                cells += ["-", "-"]
                continue
            cells += [_verdict(row, label, judged), f"{row['latency_s']:.1f}"]
        print("| " + " | ".join(cells) + " |")
    held = {c.id for c in CASES if c.held_out}
    tuned_search = {c.id for c in CASES if c.tool == "search_the_web" and not c.held_out}
    for label, rows in zip(labels, by_id, strict=True):
        for title, ids in (("tuned set", tuned_search), ("held out", held)):
            picked = [r for r in rows.values() if r["id"] in ids]
            if not picked:
                continue
            verdicts = [_verdict(r, label, judged) for r in picked]
            lat = [r["latency_s"] for r in picked]
            chars = statistics.median([r["chars"] for r in picked])
            print(
                f"\n{label} {title}: yes {verdicts.count('yes')}/{len(picked)}, "
                f"partial {verdicts.count('partial')}; latency mean "
                f"{statistics.mean(lat):.2f} s, median {statistics.median(lat):.2f} s, "
                f"p75 {_p75(lat):.2f} s, max {max(lat):.2f} s; median output {chars:.0f} chars"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--label", required=True)
    run_p.add_argument("--out", type=Path, required=True)
    run_p.add_argument("--only", default="", help="comma-separated case ids")
    rep_p = sub.add_parser("report")
    rep_p.add_argument("paths", type=Path, nargs="+")
    rep_p.add_argument(
        "--judged",
        type=Path,
        help='JSON {"<label>": {"<case>": "yes"|"partial"|"no"}} overriding the regex after reading',
    )
    args = parser.parse_args()
    if args.cmd == "run":
        only = {s for s in args.only.split(",") if s} or None
        asyncio.run(run(args.label, args.out, only))
    else:
        judged = json.loads(args.judged.read_text()) if args.judged else {}
        report(args.paths, judged)


if __name__ == "__main__":
    main()
