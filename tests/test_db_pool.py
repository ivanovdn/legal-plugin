"""A Postgres outage must fail fast, not stall.

`psycopg_pool` defaults to a 30 s wait for a connection. Every store read and
write in the app goes through this one pool, so that default is what turned an
`app-db` outage into a half-minute hang in the Word pane — including on the two
recall reads in `skills/legal_research/context.py` that already catch their
failure and degrade correctly, but only after burning 30 s each first.

Bounding the pool is therefore the single change that fixes every Postgres
caller at once: the wrapped reads, the audit write, `save_review`, and both
feedback endpoints. It converts a stall into a fast, correct degrade.
"""
import memory.db as db


def _build_pool_with(monkeypatch, **settings_over) -> dict:
    """Build the pool against a recording fake, then drop it again.

    reset_pool() runs in a finally so the next test's `_clean_tables` fixture
    rebuilds a real pool against the testcontainers Postgres — a fake left
    installed would fail every later test in the session.
    """
    created: dict = {}

    class _FakePool:
        def close(self) -> None:
            pass

    def _factory(dsn, **kwargs):
        created["dsn"] = dsn
        created["kwargs"] = kwargs
        return _FakePool()

    monkeypatch.setattr(db, "ConnectionPool", _factory)
    for key, value in settings_over.items():
        monkeypatch.setattr(db.get_settings(), key, value, raising=False)

    db.reset_pool()
    try:
        db.get_pool()
    finally:
        db.reset_pool()
    return created


def test_pool_bounds_the_wait_for_a_connection(monkeypatch):
    """Without `timeout=`, psycopg_pool waits its 30 s default and the attorney
    watches the pane hang before anything degrades."""
    created = _build_pool_with(monkeypatch, db_pool_timeout=2.5)
    assert created["kwargs"]["timeout"] == 2.5


def test_pool_bounds_the_connect_attempt_itself(monkeypatch):
    """A refused connection fails immediately, but a blackholed host (VPN drop,
    a VM that stops answering) does not — libpq would keep waiting and the
    pool's background workers would pile up hanging attempts behind the
    bounded getconn. `connect_timeout` is an integer number of seconds and
    libpq treats 1 as 2, so it is floored at 2."""
    created = _build_pool_with(monkeypatch, db_pool_timeout=3.0)
    assert created["kwargs"]["kwargs"]["connect_timeout"] == 3

    created = _build_pool_with(monkeypatch, db_pool_timeout=0.5)
    assert created["kwargs"]["kwargs"]["connect_timeout"] == 2


def test_pool_still_opens_autocommit(monkeypatch):
    """The stores issue single-statement writes and assume autocommit — adding
    the timeout must not disturb the connection kwargs already in place."""
    created = _build_pool_with(monkeypatch, db_pool_timeout=3.0)
    assert created["kwargs"]["kwargs"]["autocommit"] is True


def test_default_timeout_is_short_enough_to_be_a_degrade(monkeypatch):
    """The default is what ships to the VM. It has to be short enough that a
    failure reads as 'memory is flaky' rather than 'the tool is broken', and
    long enough not to trip on a healthy-but-slow first connection."""
    timeout = db.get_settings().db_pool_timeout
    assert 1.0 <= timeout <= 5.0, f"{timeout}s is outside the useful band"
