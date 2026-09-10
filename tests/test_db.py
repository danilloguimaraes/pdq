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
        conn.execute(
            "INSERT OR REPLACE INTO attendance (player_id, session_id, status) VALUES (1, 1, ?)",
            (ok,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT OR REPLACE INTO attendance (player_id, session_id, status) VALUES (1, 1, 'Z')"
        )
    conn.close()


def test_foreign_keys_enforced(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO attendance (player_id, session_id, status) VALUES (99, 99, 'X')")
    conn.close()


def test_clear_all(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'A')")
    db.clear_all(conn)
    assert conn.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 0
    conn.close()


def test_schema_version_and_new_tables(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"player_alias", "match_meta"} <= tables
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(attendance)")}
    assert {"note", "section"} <= cols
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(player)")}
    assert {"padrinho", "guest_status", "guest_decision_date"} <= cols
    conn.close()


V1_SCHEMA = """
CREATE TABLE player (
    id INTEGER PRIMARY KEY, pos INTEGER NOT NULL UNIQUE, classe TEXT NOT NULL DEFAULT '',
    posicao TEXT NOT NULL DEFAULT '', legacy_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL
);
CREATE TABLE session (
    id INTEGER PRIMARY KEY, ordem INTEGER NOT NULL UNIQUE, date TEXT NOT NULL UNIQUE,
    venue TEXT NOT NULL DEFAULT ''
);
CREATE TABLE attendance (
    player_id INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('X', 'F', '-')),
    PRIMARY KEY (player_id, session_id)
);
CREATE INDEX idx_attendance_session ON attendance(session_id);
INSERT INTO player (pos, name) VALUES (1, 'A'), (2, 'B');
INSERT INTO session (ordem, date, venue) VALUES (1, '2025-01-02', 'Fair Play'),
                                               (2, '2025-01-01', 'Fair Play');
INSERT INTO attendance VALUES (1, 1, 'X'), (2, 1, 'F'), (1, 2, '-');
"""


def test_migrates_v1_database_preserving_data(tmp_path):
    path = tmp_path / "pdq.db"
    raw = sqlite3.connect(path)
    raw.executescript(V1_SCHEMA)
    raw.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    rows = conn.execute(
        "SELECT player_id, session_id, status, note, section FROM attendance ORDER BY 1, 2"
    )
    assert [tuple(r) for r in rows] == [
        (1, 1, "X", "", ""),
        (1, 2, "-", "", ""),
        (2, 1, "F", "", ""),
    ]
    conn.execute("INSERT INTO attendance VALUES (2, 2, 'J', 'entrou no 2º tempo', 'reservas')")
    conn.execute("UPDATE player SET padrinho = 'A' WHERE id = 2")
    conn.execute("INSERT INTO player_alias VALUES ('bezinho', 2)")
    conn.execute("INSERT INTO match_meta (session_id, vagas_vazias) VALUES (1, 2)")
    conn.commit()
    conn.close()

    # reabrir não pode migrar de novo nem perder nada
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 4
    assert (
        conn.execute("SELECT player_id FROM player_alias WHERE alias='bezinho'").fetchone()[0] == 2
    )
    conn.close()


V2_SCHEMA = """
CREATE TABLE player (
    id INTEGER PRIMARY KEY, pos INTEGER NOT NULL UNIQUE, classe TEXT NOT NULL DEFAULT '',
    posicao TEXT NOT NULL DEFAULT '', legacy_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
    padrinho TEXT NOT NULL DEFAULT ''
);
CREATE TABLE session (
    id INTEGER PRIMARY KEY, ordem INTEGER NOT NULL UNIQUE, date TEXT NOT NULL UNIQUE,
    venue TEXT NOT NULL DEFAULT ''
);
CREATE TABLE attendance (
    player_id INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('X', 'F', '-', 'J')),
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (player_id, session_id)
);
CREATE INDEX idx_attendance_session ON attendance(session_id);
CREATE TABLE player_alias (
    alias TEXT PRIMARY KEY, player_id INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE
);
CREATE TABLE match_meta (
    session_id INTEGER PRIMARY KEY REFERENCES session(id) ON DELETE CASCADE,
    vagas_vazias INTEGER NOT NULL DEFAULT 0, observacao TEXT NOT NULL DEFAULT '',
    raw_list TEXT NOT NULL DEFAULT ''
);
INSERT INTO player (pos, name) VALUES (1, 'A'), (2, 'B');
INSERT INTO session (ordem, date, venue) VALUES (1, '2025-01-02', 'Fair Play');
INSERT INTO attendance VALUES (1, 1, 'X', ''), (2, 1, 'J', 'entrou');
INSERT INTO player_alias VALUES ('bezinho', 2);
INSERT INTO match_meta (session_id, vagas_vazias) VALUES (1, 2);
PRAGMA user_version = 2;
"""


def test_migrates_v2_database_adding_section(tmp_path):
    path = tmp_path / "pdq.db"
    raw = sqlite3.connect(path)
    raw.executescript(V2_SCHEMA)
    raw.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == 4
    rows = conn.execute("SELECT player_id, status, note, section FROM attendance ORDER BY 1")
    assert [tuple(r) for r in rows] == [(1, "X", "", ""), (2, "J", "entrou", "")]
    assert conn.execute("SELECT vagas_vazias FROM match_meta").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM player_alias").fetchone()[0] == 1
    conn.close()
    conn = db.connect(path)  # reabrir é idempotente
    assert db.schema_version(conn) == 4
    conn.close()


def test_renumber_sessions_orders_by_date_desc(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO session (ordem, date) VALUES (1, '2025-01-10')")
    conn.execute("INSERT INTO session (ordem, date) VALUES (2, '2025-01-03')")
    conn.execute("INSERT INTO session (ordem, date) VALUES (3, '2025-01-17')")  # nova, mais recente
    db.renumber_sessions(conn)
    rows = conn.execute("SELECT ordem, date FROM session ORDER BY ordem").fetchall()
    assert [tuple(r) for r in rows] == [(1, "2025-01-17"), (2, "2025-01-10"), (3, "2025-01-03")]
    conn.close()


def test_alias_cascades_on_player_delete(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'A')")
    conn.execute("INSERT INTO player_alias VALUES ('a', 1)")
    conn.execute("DELETE FROM player WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM player_alias").fetchone()[0] == 0
    conn.close()
