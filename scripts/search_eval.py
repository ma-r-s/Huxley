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
hits real search engines and sites (about 3 minutes, one question every
couple of seconds). Expected facts are as of 2026-10-13; they rot, so
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


CASES: tuple[Case, ...] = (
    Case(
        "match",
        "¿Cómo quedó la selección femenina contra Venezuela?",
        "search_the_web",
        {"query": "resultado Colombia femenina Venezuela"},
        "Colombia won 2-1 (friendly in Venezuela, 13 Oct 2026)",
        r"2\s*-\s*1\b|2 a 1|volvi[oó] a ganarle a Venezuela",
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
        r"81 a[ñn]os|7 de octubre",
    ),
    Case(
        "holiday",
        "¿Cuándo es el próximo festivo?",
        "search_the_web",
        {"query": "próximo festivo en Colombia"},
        "Monday 2 Nov 2026 (Todos los Santos)",
        r"2 de noviembre",
    ),
    Case(
        "tv",
        "¿A qué hora juega Millonarios hoy y por qué canal lo pasan?",
        "search_the_web",
        {"query": "Millonarios Once Caldas hora canal"},
        "Kick-off 8:30 p.m. Tue 13 Oct at El Campín (Caracol Radio and La Patria agree); "
        "the channel (Win Sports) counts as a bonus, not required",
        r"8:30",
    ),
    Case(
        "recipe",
        "¿Cómo se prepara un ajiaco?",
        "search_the_web",
        {"query": "receta ajiaco santafereño"},
        "Chicken, three potatoes incl. papa criolla, guascas, corn",
        r"guascas",
    ),
    Case(
        "health",
        "¿Qué es bueno para el dolor de estómago?",
        "search_the_web",
        {"query": "remedios para el dolor de estómago"},
        "General advice (fluids, bland food) and see a doctor if strong or persistent",
        r"m[ée]dico|consult",
    ),
    Case(
        "medication",
        "¿Puedo tomar ibuprofeno si tomo losartán?",
        "search_the_web",
        {"query": "ibuprofeno y losartán interacción"},
        "Interaction: ibuprofen can weaken losartan and strain the kidneys; ask the doctor",
        r"ri[ñn][oó]n|renal|presi[oó]n arterial|riesgo",
    ),
    Case(
        "pope",
        "¿Cómo sigue el papa?",
        "search_the_web",
        {"query": "salud papa León XIV"},
        "Diagnosed with lung cancer after lung surgery (13 Oct 2026)",
        r"c[áa]ncer",
    ),
    Case(
        "quake",
        "¿Hubo un temblor hoy?",
        "search_the_web",
        {"query": "temblor hoy Colombia"},
        "Magnitude 5.0 on 13 Oct 2026, felt in Bucaramanga and other cities",
        r"5[,.]0",
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
        {"query": "Nairo Quintana retiro"},
        "No: he said goodbye to pro cycling (farewell event, Oct 2026)",
        r"despidi|retir[oó]|[úu]ltima carrera|adi[oó]s",
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
    await search.setup(make_test_context(config=dict(SEARCH_CONFIG), language="es"))
    news = NewsSkill()
    await news.setup(make_test_context(config=dict(NEWS_CONFIG), language="es"))

    rows: list[dict[str, Any]] = []
    for case in CASES:
        if only and case.id not in only:
            continue
        skill = search if case.tool == "search_the_web" else news
        t0 = time.perf_counter()
        result = await skill.handle(case.tool, dict(case.args))
        latency = time.perf_counter() - t0
        data = json.loads(result.output)
        text = json.dumps(_strip_urls(data), ensure_ascii=False)
        hit = bool(re.search(case.pattern, text, re.IGNORECASE))
        rows.append(
            {
                "id": case.id,
                "tool": case.tool,
                "latency_s": round(latency, 2),
                "hit": hit,
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


def report(paths: list[Path], judged: dict[str, dict[str, bool]]) -> None:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    by_id = [{r["id"]: r for r in run["rows"]} for run in runs]
    labels = [run["label"] for run in runs]
    header = "| case | expected | " + " | ".join(f"{lb} hit | {lb} s" for lb in labels) + " |"
    print(header)
    print("|" + "---|" * (2 + 2 * len(labels)))
    for case in CASES:
        cells = [case.id, case.expected]
        for label, rows in zip(labels, by_id, strict=True):
            row = rows.get(case.id)
            if row is None:
                cells += ["-", "-"]
                continue
            hit = judged.get(label, {}).get(case.id, row["hit"])
            mark = "yes" if hit else ("FAIL" if row["failure"] else "no")
            cells += [mark, f"{row['latency_s']:.1f}"]
        print("| " + " | ".join(cells) + " |")
    for label, rows in zip(labels, by_id, strict=True):
        search_rows = [r for r in rows.values() if r["tool"] == "search_the_web"]
        hits = sum(judged.get(label, {}).get(r["id"], r["hit"]) for r in search_rows)
        lat = [r["latency_s"] for r in search_rows]
        median = statistics.median(lat) if lat else 0.0
        chars = statistics.median([r["chars"] for r in search_rows]) if search_rows else 0
        print(
            f"\n{label}: search hits {hits}/{len(search_rows)}, median latency {median:.1f} s, "
            f"max {max(lat, default=0):.1f} s, median output {chars:.0f} chars"
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
        help='JSON {"<label>": {"<case>": true|false}} overriding the regex after reading',
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
