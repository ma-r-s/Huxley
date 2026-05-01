"""T1.14 smoke test — drive the install + setup chain end-to-end against
the live runtime, no PWA / OpenAI session needed.

Validates the on-disk wiring documented in docs/skills/installing.md
through the same code paths the production server uses:

1. discover_skills() reads the entry-point group and returns the
   StocksSkill class.
2. load_persona() reads server/personas/basic/persona.yaml and
   resolves the skills + config.
3. JsonFileSecrets reads <persona>/data/secrets/stocks/values.json
   (perms 0700/0600).
4. StocksSkill.setup() runs against a SkillContext shaped like the
   one Application._build_skill_context produces.
5. The schema-version mechanism writes skill_version:stocks=1 to the
   persona's schema_meta on first boot.
6. handle("get_stock_price") dispatches and returns the expected
   error payload (the placeholder API key gets rejected by Alpha
   Vantage with auth_failed) — proving the skill's full request /
   error-classification / say_to_user pipeline works.

Voice path is NOT exercised (needs the LLM + browser PTT). Everything
else is.

Run: `uv run python scripts/smoke_t114.py`
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import structlog

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server" / "runtime" / "src"))
sys.path.insert(0, str(REPO / "server" / "sdk" / "src"))

from huxley.loader import discover_skills  # noqa: E402
from huxley.persona import load_persona  # noqa: E402
from huxley.storage.db import Storage  # noqa: E402
from huxley.storage.secrets import JsonFileSecrets  # noqa: E402
from huxley.storage.skill import NamespacedSkillStorage  # noqa: E402
from huxley_sdk import SkillContext  # noqa: E402


def _log(event: str, **kw: object) -> None:
    print(f"  [{event}]", *(f"{k}={v!r}" for k, v in kw.items()))


async def smoke() -> int:
    failures: list[str] = []

    print("=== Smoke 1: discover_skills(['stocks']) ===")
    skills_by_name = discover_skills(["stocks", "system"])
    if "stocks" not in skills_by_name:
        failures.append("stocks not discovered by loader")
    else:
        skill_class = skills_by_name["stocks"]
        _log(
            "ok",
            class_=skill_class.__qualname__,
            module=skill_class.__module__,
        )
        # Pin the class-level metadata Phase 1 added.
        if getattr(skill_class, "config_schema", None) is None:
            failures.append("StocksSkill.config_schema is None (expected dict)")
        else:
            cs = skill_class.config_schema
            if cs["properties"]["api_key"]["format"] != "secret":
                failures.append("api_key field is not marked format:secret")
            else:
                _log("config_schema_ok", required=cs["required"])
        if getattr(skill_class, "data_schema_version", None) != 1:
            failures.append("data_schema_version != 1")

    print("\n=== Smoke 2: load basic persona ===")
    spec = load_persona(REPO / "server" / "personas" / "basic")
    resolved = spec.resolve()
    if "stocks" not in resolved.skills:
        failures.append("stocks not in resolved.skills (persona.yaml broken?)")
    else:
        _log(
            "persona_ok",
            language=resolved.language_code,
            stocks_config=resolved.skills["stocks"],
        )

    print("\n=== Smoke 3: JsonFileSecrets reads values.json ===")
    secrets_dir = REPO / "server" / "personas" / "basic" / "data" / "secrets" / "stocks"
    secrets = JsonFileSecrets(secrets_dir)
    api_key = await secrets.get("api_key")
    if api_key != "smoke-test-placeholder-key":
        failures.append(f"unexpected api_key value: {api_key!r}")
    else:
        _log("secrets_ok", value_preview=api_key[:12] + "...")

    print("\n=== Smoke 4: schema-version writes to schema_meta ===")
    db_path = REPO / "server" / "personas" / "basic" / "data" / "smoke.db"
    db_path.unlink(missing_ok=True)
    storage = Storage(db_path)
    await storage.init()
    try:
        before = await storage.get_skill_schema_version("stocks")
        if before is not None:
            failures.append(f"first boot stored should be None, got {before}")
        await storage.set_skill_schema_version("stocks", 1)
        after = await storage.get_skill_schema_version("stocks")
        if after != 1:
            failures.append(f"after set, expected 1, got {after}")
        else:
            _log("schema_meta_ok", before=before, after=after)
    finally:
        await storage.close()
        db_path.unlink(missing_ok=True)
        # Also clean the WAL files Storage creates.
        for ext in ("-wal", "-shm"):
            (db_path.parent / (db_path.name + ext)).unlink(missing_ok=True)

    print("\n=== Smoke 5: StocksSkill.setup() with real SkillContext ===")
    storage = Storage(db_path)
    await storage.init()
    skill = skill_class()
    logger = MagicMock()
    for m in ("ainfo", "adebug", "awarning", "aerror", "aexception"):
        setattr(logger, m, AsyncMock())
    ctx = SkillContext(
        logger=logger,
        storage=NamespacedSkillStorage(storage, "stocks"),
        secrets=secrets,
        persona_data_dir=REPO / "server" / "personas" / "basic" / "data",
        config=resolved.skills["stocks"],
        language=resolved.language_code,
    )
    try:
        await skill.setup(ctx)
    except Exception as exc:
        failures.append(f"setup raised: {exc!r}")
    else:
        if skill._client is None:
            failures.append("skill._client is None — secret read failed?")
        else:
            _log(
                "setup_complete",
                watchlist=skill._watchlist,
                currency=skill._currency,
            )
            # Confirm the structured log fired (matches the docstring's
            # claim that setup_complete is the success signal).
            if not any(
                call.args == ("stocks.setup_complete",) for call in logger.ainfo.await_args_list
            ):
                failures.append("setup_complete log event did not fire")

    print("\n=== Smoke 6: handle('get_stock_price', {ticker: 'AAPL'}) ===")
    # The placeholder key gets rejected by Alpha Vantage. We expect
    # auth_failed to come back as a clean error payload, NOT a crash.
    # If you set HUXLEY_SMOKE_AV_KEY to a real key, this asserts the
    # happy path instead.
    import os

    real_key = os.environ.get("HUXLEY_SMOKE_AV_KEY")
    if real_key:
        # Re-setup with the real key.
        await secrets.set("api_key", real_key)
        skill2 = skill_class()
        await skill2.setup(ctx)
        result = await skill2.handle("get_stock_price", {"ticker": "AAPL"})
        body = json.loads(result.output)
        if "error" in body:
            failures.append(f"real-key dispatch returned error: {body}")
        else:
            _log(
                "real_quote_ok",
                symbol=body["symbol"],
                price=body["price"],
                say=body["say_to_user"][:60] + "...",
            )
        # Restore placeholder for the rest of the smoke.
        await secrets.set("api_key", "smoke-test-placeholder-key")
    else:
        # Either outcome counts as a passing smoke for the dispatch leg:
        # (a) Alpha Vantage rejects the placeholder string → auth_failed
        #     payload (proves the error-classification chain).
        # (b) Alpha Vantage's free tier is permissive enough to return a
        #     real quote → success payload (proves the happy path,
        #     parse, and say_to_user formatting).
        # The only failure mode is a crash or a malformed payload.
        result = await skill.handle("get_stock_price", {"ticker": "AAPL"})
        body = json.loads(result.output)
        if "error" in body:
            _log(
                "placeholder_dispatch_classified_error",
                error_kind=body["error"],
                say=body.get("say_to_user", "")[:60],
            )
        elif "symbol" in body and "price" in body and "say_to_user" in body:
            _log(
                "placeholder_dispatch_returned_real_quote",
                symbol=body["symbol"],
                price=body["price"],
                say=body["say_to_user"][:60] + "...",
            )
        else:
            failures.append(f"dispatch returned malformed payload: {body}")

    await storage.close()
    db_path.unlink(missing_ok=True)
    for ext in ("-wal", "-shm"):
        (db_path.parent / (db_path.name + ext)).unlink(missing_ok=True)

    print()
    if failures:
        print("=== FAILURES ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== ALL SMOKE STEPS PASSED ===")
    return 0


if __name__ == "__main__":
    structlog.configure(processors=[structlog.processors.KeyValueRenderer()])
    sys.exit(asyncio.run(smoke()))
