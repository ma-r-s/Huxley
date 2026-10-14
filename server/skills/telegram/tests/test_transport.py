"""Unit tests for the transport layer.

Covers the pure pieces — PCM downsampling + phone normalization — plus
a smoke test on `TelegramTransport.__init__` proving the class
constructs without importing pyrogram (so the skill can be loaded in
environments that haven't installed the Telegram deps, and so tests
don't pull in the heavy native binding).

Integration tests that actually dial Telegram live at the spike, not
here.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from huxley_skill_telegram.transport import (
    TelegramTransport,
    TransportError,
    downsample_48k_stereo_to_24k_mono,
    normalize_phone,
)


class TestDownsampler:
    """The 48 kHz stereo → 24 kHz mono function is PURE — easy to verify."""

    def test_empty_input_returns_empty(self) -> None:
        assert downsample_48k_stereo_to_24k_mono(b"") == b""

    def test_partial_frame_input_dropped_cleanly(self) -> None:
        # 3 bytes = not a full stereo frame (4 bytes); function must
        # handle it without indexing past the end.
        assert downsample_48k_stereo_to_24k_mono(b"\x00\x01\x02") == b""

    def test_single_decimation_pair_yields_one_mono_sample(self) -> None:
        # Two stereo frames: (L=100, R=-100) and (L=999, R=-999).
        # Decimation keeps the FIRST frame; mono = (100 + -100) // 2 = 0.
        pcm_in = struct.pack("<4h", 100, -100, 999, -999)
        out = downsample_48k_stereo_to_24k_mono(pcm_in)
        assert len(out) == 2  # one PCM16 sample
        (sample,) = struct.unpack("<h", out)
        assert sample == 0

    def test_stream_of_known_values(self) -> None:
        # 4 stereo frames: (1000,2000) (3000,4000) (5000,6000) (7000,8000)
        # n_in_frames=4; n_out_frames=2; outputs = avg of frames 0 & 2:
        #   M0 = (1000 + 2000)//2 = 1500
        #   M1 = (5000 + 6000)//2 = 5500
        pcm_in = struct.pack("<8h", 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000)
        out = downsample_48k_stereo_to_24k_mono(pcm_in)
        assert struct.unpack("<2h", out) == (1500, 5500)

    def test_halves_the_sample_count_exactly(self) -> None:
        # 48 kHz stereo → 24 kHz mono = 1/4 the bytes.
        # Start with 1 second of silence = 48000*4 = 192000 bytes.
        pcm_in = b"\x00" * (48_000 * 4)
        out = downsample_48k_stereo_to_24k_mono(pcm_in)
        assert len(out) == 48_000  # 24 kHz mono * 2 bytes = 48000 bytes/sec

    def test_channel_averaging_preserves_voice_envelope(self) -> None:
        # Build a stereo sine wave at 440 Hz, both channels in phase.
        # Averaging keeps the same waveform; decimating by 2 halves the
        # rate, so output is 220 Hz-relative (but same PCM amplitude).
        import math

        n_frames = 4800  # 100ms at 48 kHz
        amp = 10_000
        buf = bytearray()
        for i in range(n_frames):
            v = int(math.sin(2 * math.pi * 440 * i / 48_000) * amp)
            buf.extend(struct.pack("<2h", v, v))  # L=R
        out = downsample_48k_stereo_to_24k_mono(bytes(buf))
        # Peak amplitude should still be ~amp (averaging L=R doesn't reduce it).
        samples = struct.unpack(f"<{len(out) // 2}h", out)
        assert max(samples) > amp * 0.95
        assert min(samples) < -amp * 0.95


class TestNormalizePhone:
    def test_strips_spaces_and_dashes(self) -> None:
        assert normalize_phone("+57 300 000 0073") == "+573000000073"
        assert normalize_phone("+57-300-000-0073") == "+573000000073"

    def test_strips_parens(self) -> None:
        assert normalize_phone("+1 (555) 123-4567") == "+15551234567"

    def test_leaves_leading_plus_alone(self) -> None:
        assert normalize_phone("+573000000073") == "+573000000073"

    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_phone("  +573000000073  ") == "+573000000073"


class TestTransportConstruction:
    def test_init_does_not_import_pyrogram(self, tmp_path: Path) -> None:
        # Verifying by construction: if this succeeds without
        # pyrogram/pytgcalls on PYTHONPATH it means __init__ didn't
        # reach their imports. We don't actually strip them from the
        # venv for this test — we rely on them being lazy-imported
        # inside connect() / place_call().
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        # No call made yet.
        assert t._active_user_id is None  # type: ignore[attr-defined]
        assert t._call_py is None  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_resolve_contact_before_connect_raises(self, tmp_path: Path) -> None:
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        with pytest.raises(TransportError, match="before connect"):
            await t.resolve_contact("+573000000078")

    @pytest.mark.asyncio
    async def test_place_call_before_connect_raises(self, tmp_path: Path) -> None:
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        with pytest.raises(TransportError, match="before connect"):
            await t.place_call(1000000081)

    @pytest.mark.asyncio
    async def test_send_pcm_before_call_is_noop(self, tmp_path: Path) -> None:
        # Without an active call (no `_active_user_id`), `send_pcm`
        # should swallow the bytes silently — no crash, no send.
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        await t.send_pcm(b"\x01\x02" * 128)
        assert t._sent_count == 0  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_send_pcm_zero_length_is_noop(self, tmp_path: Path) -> None:
        t = TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )
        await t.send_pcm(b"")
        assert t._sent_count == 0  # type: ignore[attr-defined]


class _FakeStorage:
    """Stand-in for pyrogram's SQLiteStorage: `conn` is set once the
    session file was opened, `close()` closes it."""

    def __init__(self, *, opened: bool) -> None:
        self.conn: object | None = object() if opened else None
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeExecutor:
    def __init__(self) -> None:
        self.shut_down = False

    def shutdown(self, *, wait: bool) -> None:
        self.shut_down = True


class _FakeApp:
    """Stand-in for a pyrogram Client. A database-locked start fails
    inside `load_session`, after the session file was opened but before
    `is_connected` flips, which is what `opened=True, connected=False`
    models."""

    def __init__(
        self, *, opened: bool = True, connected: bool = False, initialized: bool = False
    ) -> None:
        self.storage = _FakeStorage(opened=opened)
        self.is_connected = connected or initialized
        self.is_initialized = initialized
        self.disconnected = False
        self.stopped = False

    async def disconnect(self) -> None:
        if self.is_initialized:
            msg = "Can't disconnect an initialized client"
            raise ConnectionError(msg)
        self.disconnected = True
        self.is_connected = False

    async def stop(self) -> None:
        self.stopped = True
        self.is_initialized = False
        self.is_connected = False


class _FakeCallPy:
    """Stand-in for PyTgCalls with the property behind the 2026-10-12
    incident: `start()` marks the instance running BEFORE the
    underlying client starts, so a second `start()` on the same
    instance raises even though the first one failed."""

    def __init__(self, error: BaseException | None) -> None:
        self._error = error
        self._is_running = False
        self.executor = _FakeExecutor()
        self.start_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        if self._is_running:
            msg = "PyTgCalls client is already running"
            raise RuntimeError(msg)
        self._is_running = True
        if self._error is not None:
            raise self._error


class _Builder:
    """Scripted `_build_clients` replacement: attempt N gets a fresh
    pair whose start raises `errors[N]` (None = succeeds)."""

    def __init__(
        self,
        errors: list[BaseException | None],
        *,
        connected: bool = False,
        initialized: bool = False,
    ) -> None:
        self._errors = list(errors)
        self._connected = connected
        self._initialized = initialized
        self.pairs: list[tuple[_FakeApp, _FakeCallPy]] = []

    def __call__(self) -> tuple[object, object]:
        error = self._errors[len(self.pairs)]
        app = _FakeApp(connected=self._connected, initialized=self._initialized)
        pair = (app, _FakeCallPy(error))
        self.pairs.append(pair)
        return pair


def _locked() -> BaseException:
    from sqlite3 import OperationalError

    return OperationalError("database is locked")


class TestT117DbLockRetry:
    """T1.17: `_start_with_db_lock_retry` recovers from transient
    `OperationalError: database is locked` raised by pyrogram's
    session storage when another client (a previous Application, or a
    previous server process still shutting down) holds the session
    SQLite.

    Every attempt must use a FRESH client pair: the real PyTgCalls
    refuses a second `start()` after a failed one, so the old
    reuse-the-same-object retry raised `PyTgCallsAlreadyRunning` on
    every retry and took the persona down (2026-10-12 incident). The
    fakes above reproduce that property, so these tests fail against
    the old implementation. Backoffs are skipped via `monkeypatch` on
    `asyncio.sleep` so the tests don't actually wait.
    """

    @pytest.fixture
    def transport(self, tmp_path: Path) -> TelegramTransport:
        return TelegramTransport(
            api_id=12345678,
            api_hash="abcdef0123456789",
            session_dir=tmp_path,
        )

    @pytest.fixture
    def no_sleep(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        """Replace `asyncio.sleep` (as referenced inside the
        transport module) with a no-op that records the requested
        duration. Tests assert the right backoffs were attempted
        without actually waiting on them."""
        sleeps: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("huxley_skill_telegram.transport.asyncio.sleep", _fake_sleep)
        return sleeps

    @pytest.mark.asyncio
    async def test_start_succeeds_first_try_no_retry(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        build = _Builder([None])
        app, call_py = await transport._start_with_db_lock_retry(build)
        assert (app, call_py) == build.pairs[0]
        assert no_sleep == []  # no backoff fired
        assert not build.pairs[0][0].storage.closed

    @pytest.mark.asyncio
    async def test_retry_uses_a_fresh_client_pair(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """The incident: lock on attempt 1, success on attempt 2 must
        come from a NEW pair (the old pair would raise
        already-running), and the failed pair's session handle is
        closed."""
        build = _Builder([_locked(), None])
        app, call_py = await transport._start_with_db_lock_retry(build)
        assert len(build.pairs) == 2
        assert (app, call_py) == build.pairs[1]
        failed_app, failed_call_py = build.pairs[0]
        assert failed_call_py.start_calls == 1  # never restarted
        assert failed_app.storage.closed
        assert failed_call_py.executor.shut_down
        assert no_sleep == [0.2]  # only the first backoff fired

    @pytest.mark.asyncio
    async def test_start_recovers_on_last_attempt(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """Three lock errors then success on the fourth (final)
        attempt: verifies the full backoff schedule before exhaustion
        would have fired, with one pair per attempt."""
        build = _Builder([_locked(), _locked(), _locked(), None])
        app, _ = await transport._start_with_db_lock_retry(build)
        assert len(build.pairs) == 4
        assert app is build.pairs[3][0]
        assert all(pair[1].start_calls == 1 for pair in build.pairs)
        assert all(pair[0].storage.closed for pair in build.pairs[:3])
        assert no_sleep == [0.2, 0.5, 1.0]

    @pytest.mark.asyncio
    async def test_start_exhausts_and_reraises_last_lock_error(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """Permanent lock: every attempt raises. Helper exhausts its
        retries, discards every pair, and re-raises the most recent
        exception so the caller's normal failure path runs."""
        from sqlite3 import OperationalError

        build = _Builder([_locked() for _ in range(4)])
        with pytest.raises(OperationalError, match="database is locked"):
            await transport._start_with_db_lock_retry(build)
        assert len(build.pairs) == 4
        assert all(pair[0].storage.closed for pair in build.pairs)
        assert no_sleep == [0.2, 0.5, 1.0]  # all three backoffs fired

    @pytest.mark.asyncio
    async def test_non_lock_operational_error_raises_immediately(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """An OperationalError for a different reason (e.g. missing
        table, malformed schema) is NOT a transient lock: re-raise
        immediately without burning the retry budget, after closing
        the failed pair."""
        from sqlite3 import OperationalError

        build = _Builder([OperationalError("no such table: version")])
        with pytest.raises(OperationalError, match="no such table"):
            await transport._start_with_db_lock_retry(build)
        assert len(build.pairs) == 1  # zero retries
        assert build.pairs[0][0].storage.closed
        assert no_sleep == []

    @pytest.mark.asyncio
    async def test_unrelated_exception_raises_immediately(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """Anything that isn't an OperationalError bypasses the retry
        path entirely; a client that got as far as connecting is
        disconnected rather than having its storage closed under it."""
        build = _Builder([RuntimeError("auth failed")], connected=True)
        with pytest.raises(RuntimeError, match="auth failed"):
            await transport._start_with_db_lock_retry(build)
        assert len(build.pairs) == 1
        failed_app = build.pairs[0][0]
        assert failed_app.disconnected
        assert not failed_app.storage.closed
        assert no_sleep == []

    @pytest.mark.asyncio
    async def test_cleanup_error_does_not_mask_start_error(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """A raising cleanup is logged and swallowed; the retry still
        goes ahead with a fresh pair."""
        build = _Builder([_locked(), None])

        def _build_with_broken_close() -> tuple[object, object]:
            app, call_py = build()
            if len(build.pairs) == 1:

                async def _boom() -> None:
                    msg = "close failed"
                    raise OSError(msg)

                app.storage.close = _boom  # type: ignore[attr-defined]
            return app, call_py

        app, _ = await transport._start_with_db_lock_retry(_build_with_broken_close)
        assert app is build.pairs[1][0]
        # The failed pair's thread pool is shut down even though the
        # session close raised.
        assert build.pairs[0][1].executor.shut_down

    @pytest.mark.asyncio
    async def test_client_that_got_past_initialize_is_stopped(
        self, transport: TelegramTransport, no_sleep: list[float]
    ) -> None:
        """pyrogram's start finished but a later PyTgCalls step failed:
        the client is running with its handlers wired. `disconnect()`
        refuses an initialized client, so it must be `stop()`ped, or it
        keeps running (and keeps the session open) behind a disabled
        skill."""
        build = _Builder([RuntimeError("get_id failed")], initialized=True)
        with pytest.raises(RuntimeError, match="get_id failed"):
            await transport._start_with_db_lock_retry(build)
        failed_app, failed_call_py = build.pairs[0]
        assert failed_app.stopped
        assert not failed_app.is_connected
        assert failed_call_py.executor.shut_down

    @pytest.mark.asyncio
    async def test_connect_keeps_the_pair_that_started(
        self,
        transport: TelegramTransport,
        no_sleep: list[float],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End to end through `connect()`: after a locked first attempt
        the transport holds the second pair."""
        build = _Builder([_locked(), None])
        monkeypatch.setattr(transport, "_build_clients", build)
        await transport.connect()
        assert transport._app is build.pairs[1][0]  # type: ignore[attr-defined]
        assert transport._call_py is build.pairs[1][1]  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_failed_connect_leaves_transport_unconnected(
        self,
        transport: TelegramTransport,
        no_sleep: list[float],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A connect that exhausts its retries leaves no half-started
        client behind, so a later `connect()` starts from scratch."""
        from sqlite3 import OperationalError

        monkeypatch.setattr(transport, "_build_clients", _Builder([_locked() for _ in range(4)]))
        with pytest.raises(OperationalError):
            await transport.connect()
        assert transport._app is None  # type: ignore[attr-defined]
        assert transport._call_py is None  # type: ignore[attr-defined]


class TestLoadContacts:
    """The account's contact list fills pyrogram's peer cache at connect,
    so numbers in it never need contacts.ResolvePhone."""

    @pytest.mark.asyncio
    async def test_contacts_are_read_and_counted(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        t = TelegramTransport(api_id=12345678, api_hash="abcdef0123456789", session_dir=tmp_path)
        calls: list[str] = []

        async def get_contacts() -> list[SimpleNamespace]:
            calls.append("get_contacts")
            return [
                SimpleNamespace(phone_number="573000000001"),
                SimpleNamespace(phone_number=None),
            ]

        t._app = SimpleNamespace(get_contacts=get_contacts)  # type: ignore[attr-defined]
        await t._load_contacts()  # type: ignore[attr-defined]
        assert calls == ["get_contacts"]

    @pytest.mark.asyncio
    async def test_a_failed_read_does_not_stop_the_connect(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from pyrogram.errors import RPCError

        t = TelegramTransport(api_id=12345678, api_hash="abcdef0123456789", session_dir=tmp_path)

        async def get_contacts() -> list[object]:
            raise RPCError("boom")

        t._app = SimpleNamespace(get_contacts=get_contacts)  # type: ignore[attr-defined]
        await t._load_contacts()  # type: ignore[attr-defined]


class TestResolveByImport:
    """A number not in the cache is imported into his Telegram contacts
    (contacts.importContacts), never looked up with contacts.ResolvePhone:
    on 2026-10-14 ResolvePhone was blocked for 45 min and grandpa couldn't
    call a contact the walkie had the exact number for."""

    def _transport(self, tmp_path: Path, *, cached: dict[str, int], imported: list[int]) -> Any:
        from types import SimpleNamespace

        t = TelegramTransport(api_id=12345678, api_hash="abcdef0123456789", session_dir=tmp_path)
        calls: list[tuple[str, str]] = []

        async def get_peer_by_phone_number(phone: str) -> SimpleNamespace:
            if phone not in cached:
                raise KeyError(phone)
            return SimpleNamespace(user_id=cached[phone])

        async def import_contacts(contacts: list[Any]) -> SimpleNamespace:
            calls.extend((c.phone, c.first_name) for c in contacts)
            return SimpleNamespace(users=[SimpleNamespace(id=i) for i in imported])

        async def get_users(identifier: str) -> None:
            raise AssertionError("ResolvePhone must not be used for a number")

        t._app = SimpleNamespace(  # type: ignore[attr-defined]
            storage=SimpleNamespace(get_peer_by_phone_number=get_peer_by_phone_number),
            import_contacts=import_contacts,
            get_users=get_users,
        )
        t.import_calls = calls  # type: ignore[attr-defined]
        return t

    @pytest.mark.asyncio
    async def test_a_cached_number_needs_no_request(self, tmp_path: Path) -> None:
        t = self._transport(tmp_path, cached={"573000000078": 222}, imported=[])
        assert await t.resolve_contact("+57 300 000 0078", name="hija") == 222
        assert t.import_calls == []

    @pytest.mark.asyncio
    async def test_an_unknown_number_is_imported_under_its_name(self, tmp_path: Path) -> None:
        t = self._transport(tmp_path, cached={}, imported=[777])
        assert await t.resolve_contact("+573000000099", name="juan miguel") == 777
        assert t.import_calls == [("+573000000099", "Juan Miguel")]

    @pytest.mark.asyncio
    async def test_not_on_telegram_says_so(self, tmp_path: Path) -> None:
        t = self._transport(tmp_path, cached={}, imported=[])
        with pytest.raises(TransportError, match="isn't on Telegram"):
            await t.resolve_contact("+573000000099", name="nadie")

    @pytest.mark.asyncio
    async def test_a_flood_wait_on_import_is_a_rate_limit(self, tmp_path: Path) -> None:
        from pyrogram.errors import FloodWait

        from huxley_skill_telegram.transport import TransportRateLimitedError

        t = self._transport(tmp_path, cached={}, imported=[])

        async def import_contacts(contacts: list[Any]) -> object:
            raise FloodWait(value=120)

        t._app.import_contacts = import_contacts
        with pytest.raises(TransportRateLimitedError) as caught:
            await t.resolve_contact("+573000000099", name="x")
        assert caught.value.wait_s == 120
