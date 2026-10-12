"""The watcher's settings: one TOML file, every key optional, plus a
secrets file (mode 600) for the Telegram bot, the dead-man ping URL and
the OpenAI admin key.

Lookup: `--config PATH`, else `$HUXLEY_WATCH_CONFIG`, else
`~/.config/huxley-watch/watch.toml` if it exists, else all defaults.
Relative paths in the file resolve against the file's directory. With no
file, paths default to this checkout (`server/runtime/logs`,
`server/personas/abuelos`), which is how the Mac and the Pi run it.

The file is read again every cycle, so a new OpenAI limit or threshold
applies without a restart (the bind address needs one).
"""

from __future__ import annotations

import ipaddress
import math
import os
import stat
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

REPO_SERVER = Path(__file__).resolve().parents[3]  # .../server
DEFAULT_CONFIG = Path("~/.config/huxley-watch/watch.toml").expanduser()
DEFAULT_SECRETS = Path("~/.config/huxley-watch/secrets.toml").expanduser()
DEFAULT_STATE = Path("~/.local/state/huxley-watch").expanduser()


@dataclass(frozen=True)
class Thresholds:
    """Every rule's knobs. Defaults are the tested ones (tests/test_rules.py)."""

    # Walkie: offline this many minutes of daytime, or this many days at all.
    offline_minutes: float = 15.0
    not_seen_days: float = 2.0
    day_start: time = time(7, 0)
    day_end: time = time(22, 0)
    # With nothing from the walkie's connection for this long, it is not
    # online whatever the connection lines say (a server killed hard
    # writes no disconnect).
    silent_minutes: float = 10.0
    # Audio cutting up: gaps as a share of playing time over the window,
    # once at least this much has played.
    gap_share_pct: float = 5.0
    gap_window_hours: float = 3.0
    gap_min_played_s: float = 120.0
    # Server: this many starts within the window is a restart loop;
    # stopped this long is down.
    restarts_max: int = 3
    restarts_window_minutes: float = 60.0
    down_minutes: float = 5.0
    # OpenAI: this many failed connects in a row.
    openai_failures: int = 3
    # Credits: alert when either is reached.
    balance_low_usd: float = 5.0
    balance_low_days: float = 7.0
    # Audiobook time left this month.
    book_hours_low: float = 2.0
    # Soloist (Spotify's player) unreachable this long.
    spotify_down_minutes: float = 10.0
    # A start with no stop before it stays an item this long.
    unclean_window_minutes: float = 60.0
    # The box's disk.
    disk_pct: float = 80.0
    # Walkie battery: warn under low, critical under crit, cleared at clear.
    battery_low_pct: float = 20.0
    battery_crit_pct: float = 10.0
    battery_clear_pct: float = 30.0
    # Alert deliveries failed in a row before the page says so.
    alert_failures: int = 3


@dataclass(frozen=True)
class Config:
    logs_dir: Path
    persona_dir: Path
    db_path: Path
    state_dir: Path
    timezone: ZoneInfo
    # The recorder's Ogg files (T1.43 W3): <persona data dir>/recordings.
    # The archive serves files from here only.
    recordings_dir: Path | None = None
    walkie_mac: str | None = None
    interval_s: float = 300.0
    lookback_days: float = 40.0
    bind_host: str = "127.0.0.1"
    bind_port: int = 8780
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    balance_usd: float | None = None
    balance_at: datetime | None = None
    # The month's OpenAI budget when the Admin API can't say: the hard
    # spend limit set on platform.openai.com, USD a calendar month (UTC).
    monthly_limit_usd: float | None = None
    openai_project_id: str | None = None  # read this project's limit and spend too
    # An sk-admin-... key: billed spend and the limits (openai_admin.py).
    # Never logged, never served; repr=False keeps it out of any dump.
    openai_admin_key: str | None = field(default=None, repr=False)
    book_hours_per_month: float | None = None
    # The audiobook allowance renews on this day of the month (Spotify's
    # billing day); None: calendar months, as the skill's own counter.
    book_renews_day: int | None = None
    book_since: date | None = None  # when the allowance began (first cycle only)
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    ping_url: str | None = None
    health_url: str | None = None  # the server's /health, once it has one (T1.43 W1)
    check_pid: bool = True  # read the server's instance lock (same box only)
    server_port: int = 8765  # whose lock: logs/huxley-<port>.lock
    thresholds: Thresholds = field(default_factory=Thresholds)
    source: Path | None = None  # the config file read, if any
    notes: tuple[tuple[str, str], ...] = ()  # (event, detail) to log once

    @property
    def log_file(self) -> Path:
        return self.state_dir / "huxley-watch.log"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "alerts.json"

    @property
    def admin_file(self) -> Path:
        """The Admin API's last answer (openai_admin.py); never the key."""
        return self.state_dir / "openai_admin.json"


class ConfigError(Exception):
    """The config file exists but can't be used (bad TOML or a bad value)."""


def parse_hhmm(value: str) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except ValueError as exc:
        raise ConfigError(f"not a HH:MM time: {value!r}") from exc


def parse_range(value: str) -> tuple[time, time]:
    try:
        start, end = value.split("-")
    except ValueError as exc:
        raise ConfigError(f"not a HH:MM-HH:MM range: {value!r}") from exc
    return parse_hhmm(start), parse_hhmm(end)


def parse_bind(value: str) -> tuple[str, int]:
    """'host:port', '[v6]:port' or a bare v6 address with a port after the
    last colon. An empty host is the loopback."""
    host, sep, port = value.strip().rpartition(":")
    if not sep:
        raise ConfigError(f"watch.bind: not host:port: {value!r}")
    try:
        number = int(port)
    except ValueError as exc:
        raise ConfigError(f"watch.bind: not host:port: {value!r}") from exc
    if not 0 <= number <= 65535:
        raise ConfigError(f"watch.bind: no such port: {value!r}")
    return host.strip("[]") or "127.0.0.1", number


def is_any_address(host: str) -> bool:
    """Every address, in any spelling (0.0.0.0, 0, ::, ::0, 0:0:...)."""
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return host in ("0", "*")


def in_range(t: time, start: time, end: time) -> bool:
    """True if t is in [start, end), wrapping midnight when start > end."""
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def read_persona(persona_dir: Path) -> dict[str, Any]:
    """persona.yaml as a dict, or {} if missing or unreadable."""
    try:
        data = yaml.safe_load((persona_dir / "persona.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _path(value: object, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _float(section: dict[str, Any], key: str) -> float | None:
    value = section.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number, got {value!r}")
    return float(value)


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    section = raw.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] must be a table")
    return section


def _read_secrets(path: Path) -> tuple[dict[str, Any], tuple[str, str] | None]:
    """The secrets file, refused (with a note) unless only its owner can
    read it. Missing is fine: Telegram and the ping are simply off."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return {}, ("watch.secrets_missing", str(path))
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return {}, ("watch.secrets_insecure", f"{path} is mode {oct(mode & 0o777)}; chmod 600 it")
    try:
        with path.open("rb") as f:
            return tomllib.load(f), None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # The type only: a TOML error quotes the line, which holds a secret.
        return {}, ("watch.secrets_unreadable", f"{path}: {type(exc).__name__}")


def _timezone(name: object, fallback: str, notes: list[tuple[str, str]]) -> ZoneInfo:
    for candidate in (name, fallback):
        if isinstance(candidate, str) and candidate and candidate != "UTC":
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                notes.append(("watch.timezone_unknown", candidate))
                continue
    if name != "UTC":
        notes.append(("watch.timezone_fallback", "no timezone in watch.toml or persona.yaml: UTC"))
    return ZoneInfo("UTC")


def _secret(secrets: dict[str, Any], key: str) -> str | None:
    """A secret, stripped (a pasted token often carries a newline)."""
    value = secrets.get(key)
    text = str(value).strip() if value is not None else ""
    return text or None


def find_config(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    env = os.environ.get("HUXLEY_WATCH_CONFIG")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None


def load_config(path: Path | None) -> Config:
    """Read the config (path None = defaults). Raises ConfigError for a
    file that exists but is broken; a missing explicit path is an error
    too (a typo must not silently mean "all defaults")."""
    try:
        return _load(path)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _load(path: Path | None) -> Config:
    raw: dict[str, Any] = {}
    base = REPO_SERVER
    if path is not None:
        try:
            with path.open("rb") as f:
                raw = tomllib.load(f)
        except FileNotFoundError as exc:
            raise ConfigError(f"no config file at {path}") from exc
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        base = path.resolve().parent

    huxley = _table(raw, "huxley")
    watch = _table(raw, "watch")
    openai = _table(raw, "openai")
    books = _table(raw, "books")
    thresholds_raw = _table(raw, "thresholds")
    notes: list[tuple[str, str]] = []

    server_dir = _path(huxley.get("server_dir", REPO_SERVER / "runtime"), base)
    logs_dir = _path(huxley["logs_dir"], base) if "logs_dir" in huxley else server_dir / "logs"
    persona_dir = _path(huxley.get("persona_dir", REPO_SERVER / "personas" / "abuelos"), base)
    db_path = (
        _path(huxley["db"], base)
        if "db" in huxley
        else persona_dir / "data" / f"{persona_dir.name}.db"
    )
    recordings_dir = (
        _path(huxley["recordings_dir"], base)
        if "recordings_dir" in huxley
        else db_path.parent / "recordings"
    )
    persona = read_persona(persona_dir)
    tz = _timezone(watch.get("timezone"), str(persona.get("timezone") or ""), notes)

    secrets_path = _path(watch.get("secrets", DEFAULT_SECRETS), base)
    secrets, note = _read_secrets(secrets_path)
    if note is not None:
        notes.append(note)

    balance_at: datetime | None = None
    if openai.get("balance_at") is not None:
        value = openai["balance_at"]
        if isinstance(value, datetime):
            balance_at = value
        else:
            try:
                balance_at = datetime.fromisoformat(str(value))
            except ValueError as exc:
                raise ConfigError(f"openai.balance_at: not a date and time: {value!r}") from exc
        balance_at = (
            balance_at.replace(tzinfo=tz) if balance_at.tzinfo is None else balance_at
        ).astimezone(UTC)
    balance_usd = _float(openai, "balance_usd")
    if balance_usd is not None and balance_at is None:
        raise ConfigError("openai.balance_usd needs openai.balance_at (when you read it)")
    monthly_limit = _float(openai, "monthly_limit_usd")
    if monthly_limit is not None and not (0 < monthly_limit < math.inf):
        raise ConfigError("openai.monthly_limit_usd must be a number more than 0")
    project_id = _secret(openai, "project_id")

    hours = _float(books, "hours_per_month")
    if hours is None:
        skills = persona.get("skills") if isinstance(persona.get("skills"), dict) else {}
        spotify = skills.get("spotify") if isinstance(skills, dict) else None
        if isinstance(spotify, dict) and spotify.get("book_hours_per_month"):
            try:
                hours = float(spotify["book_hours_per_month"])
            except (TypeError, ValueError):
                hours = None

    renews_day: int | None = None
    if books.get("renews_day") is not None:
        try:
            renews_day = int(books["renews_day"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"books.renews_day: not a day: {books['renews_day']!r}") from exc
        if not 1 <= renews_day <= 31:
            raise ConfigError(f"books.renews_day: 1-31, got {renews_day}")
    book_since: date | None = None
    if books.get("since") is not None:
        value = books["since"]
        if isinstance(value, date):
            book_since = value
        else:
            try:
                book_since = date.fromisoformat(str(value))
            except ValueError as exc:
                raise ConfigError(f"books.since: not a date: {value!r}") from exc

    known = {f for f in Thresholds.__dataclass_fields__}
    th_kwargs: dict[str, Any] = {}
    for key, value in thresholds_raw.items():
        if key not in known:
            raise ConfigError(f"unknown threshold {key!r}")
        if key in ("day_start", "day_end"):
            th_kwargs[key] = parse_hhmm(str(value))
        elif key in ("restarts_max", "openai_failures", "alert_failures"):
            th_kwargs[key] = int(value)
        else:
            number = _float(thresholds_raw, key)
            th_kwargs[key] = number
    if th_kwargs.get("gap_window_hours", 0) > 12:
        raise ConfigError("thresholds.gap_window_hours can be at most 12 (board lines kept)")
    for key, value in th_kwargs.items():
        if key not in ("day_start", "day_end") and value is not None and value <= 0:
            raise ConfigError(f"thresholds.{key} must be more than 0")
    quiet_start, quiet_end = parse_range(str(watch.get("quiet_hours", "22:00-07:00")))
    host, bind_port = parse_bind(str(watch.get("bind", "127.0.0.1:8780")))
    if is_any_address(host) and watch.get("allow_any_address") is not True:
        raise ConfigError(
            "watch.bind on every address shows his calls to anyone who can reach the box: "
            "bind the Tailscale address, or set watch.allow_any_address = true"
        )
    interval = _float(watch, "interval_minutes")
    if interval is not None and interval <= 0:
        raise ConfigError("watch.interval_minutes must be more than 0")
    lookback = _float(watch, "lookback_days")
    if lookback is not None and lookback <= 0:
        raise ConfigError("watch.lookback_days must be more than 0")

    ping = _secret(secrets, "healthchecks_url") or _secret(watch, "healthchecks_url")
    mac = huxley.get("walkie_mac")
    return Config(
        logs_dir=logs_dir,
        persona_dir=persona_dir,
        db_path=db_path,
        state_dir=_path(watch.get("state_dir", DEFAULT_STATE), base),
        timezone=tz,
        recordings_dir=recordings_dir,
        walkie_mac=str(mac).lower() if mac else None,
        interval_s=60.0 * (interval or 5.0),
        lookback_days=lookback or 40.0,
        bind_host=host,
        bind_port=bind_port,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        balance_usd=balance_usd,
        balance_at=balance_at,
        monthly_limit_usd=monthly_limit,
        openai_project_id=project_id,
        openai_admin_key=_secret(secrets, "openai_admin_key"),
        book_hours_per_month=hours,
        book_renews_day=renews_day,
        book_since=book_since,
        telegram_token=_secret(secrets, "telegram_bot_token"),
        telegram_chat_id=_secret(secrets, "telegram_chat_id"),
        ping_url=ping,
        health_url=_secret(huxley, "health_url"),
        check_pid=huxley.get("check_pid", True) is not False,
        server_port=int(huxley.get("port", 8765)),
        thresholds=Thresholds(**th_kwargs),
        source=path,
        notes=tuple(notes),
    )
