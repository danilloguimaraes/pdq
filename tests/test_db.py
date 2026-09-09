import sqlite3

import pytest

from pdq import db


def test_schema_creates_idempotently(tmp_path):
    path = tmp_path / "sub" / "pdq.db"
    conn = db.connect(path)
    db.init_schema(conn)  # segunda chamada não pode falhar
    conn.close()
    conn = db.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"player", "session", "attendance"} <= tables


def test_check_constraint_rejects_invalid_status(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'A')")
    conn.execute("INSERT INTO session (ordem, date, venue) VALUES (1, '2025-01-01', 'Fair Play')")
    for ok in db.STATUSES:
        conn.execute("INSERT OR REPLACE INTO attendance VALUES (1, 1, ?)", (ok,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT OR REPLACE INTO attendance VALUES (1, 1, 'Z')")
    conn.close()


def test_foreign_keys_enforced(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO attendance VALUES (99, 99, 'X')")
    conn.close()


def test_clear_all(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'A')")
    db.clear_all(conn)
    assert conn.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 0
    conn.close()
