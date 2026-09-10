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
    assert {"player_alias", "match_meta", "payment"} <= tables
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert "padrinho_id" in {r["name"] for r in conn.execute("PRAGMA table_info(player)")}
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(attendance)")}
    assert {"note", "section"} <= cols
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(player)")}
    assert {
        "padrinho",
        "padrinho_id",
        "guest_status",
        "guest_decision_date",
        "canonical_player_id",
    } <= cols
    foreign_keys = {
        (r["from"], r["table"], r["to"], r["on_delete"])
        for r in conn.execute("PRAGMA foreign_key_list(player)")
    }
    assert ("canonical_player_id", "player", "id", "RESTRICT") in foreign_keys
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
    assert "padrinho_id" in {r["name"] for r in conn.execute("PRAGMA table_info(player)")}
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


def test_migrates_v2_database_adding_section_and_payment(tmp_path):
    path = tmp_path / "pdq.db"
    raw = sqlite3.connect(path)
    raw.executescript(V2_SCHEMA)
    raw.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION == 5
    assert {"guest_status", "guest_decision_date"} <= {
        r["name"] for r in conn.execute("PRAGMA table_info(player)")
    }
    rows = conn.execute("SELECT player_id, status, note, section FROM attendance ORDER BY 1")
    assert [tuple(r) for r in rows] == [(1, "X", "", ""), (2, "J", "entrou", "")]
    assert conn.execute("SELECT vagas_vazias FROM match_meta").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM player_alias").fetchone()[0] == 1
    conn.execute(
        "INSERT INTO payment (player_id, amount_cents, paid_on) VALUES (1, 1500, '2025-01-03')"
    )
    conn.commit()
    conn.close()

    conn = db.connect(path)  # reabrir é idempotente
    assert db.schema_version(conn) == 5
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 1
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


def test_payment_table_constraints_and_cascade(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'A')")
    conn.execute(
        "INSERT INTO payment (player_id, amount_cents, paid_on, ref) "
        "VALUES (1, 1500, '2025-09-05', '2025-09-04')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # valor precisa ser positivo
        conn.execute(
            "INSERT INTO payment (player_id, amount_cents, paid_on) VALUES (1, 0, '2025-09-05')"
        )
    with pytest.raises(sqlite3.IntegrityError):  # jogador precisa existir
        conn.execute(
            "INSERT INTO payment (player_id, amount_cents, paid_on) VALUES (9, 1500, '2025-09-05')"
        )
    conn.execute("DELETE FROM player WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 0
    conn.close()


V3_SCHEMA = """
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
    section TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (player_id, session_id)
);
CREATE TABLE player_alias (alias TEXT PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES player(id));
CREATE TABLE match_meta (session_id INTEGER PRIMARY KEY, vagas_vazias INTEGER NOT NULL DEFAULT 0,
    observacao TEXT NOT NULL DEFAULT '', raw_list TEXT NOT NULL DEFAULT '');
INSERT INTO player (pos, name) VALUES (1, 'A');
INSERT INTO session (ordem, date) VALUES (1, '2025-01-02');
INSERT INTO attendance VALUES (1, 1, 'J', 'obs', 'reservas');
PRAGMA user_version = 3;
"""


def test_migrates_v3_database_adding_payment(tmp_path):
    path = tmp_path / "pdq.db"
    raw = sqlite3.connect(path)
    raw.executescript(V3_SCHEMA)
    raw.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == 5
    row = conn.execute("SELECT status, note, section FROM attendance").fetchone()
    assert tuple(row) == ("J", "obs", "reservas")
    conn.execute(
        "INSERT INTO payment (player_id, amount_cents, paid_on) VALUES (1, 1500, '2025-01-03')"
    )
    conn.commit()
    conn.close()

    conn = db.connect(path)  # reabrir é idempotente
    assert db.schema_version(conn) == 5
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize("missing", ["payment", "guest"])
def test_completes_v4_database_created_by_a_single_epic(tmp_path, missing):
    """E4 e E5 nasceram em paralelo sob a versão 4: a guarda cobre um banco que só tem uma delas."""
    path = tmp_path / "pdq.db"
    raw = sqlite3.connect(path)
    raw.executescript(V3_SCHEMA)
    if missing == "payment":  # banco criado só pela E5
        raw.executescript(
            "ALTER TABLE player ADD COLUMN guest_status TEXT NOT NULL DEFAULT '';"
            "ALTER TABLE player ADD COLUMN guest_decision_date TEXT NOT NULL DEFAULT '';"
        )
    else:  # banco criado só pela E4
        raw.executescript(
            "CREATE TABLE payment (id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL "
            "REFERENCES player(id) ON DELETE CASCADE, amount_cents INTEGER NOT NULL "
            "CHECK (amount_cents > 0), paid_on TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', "
            "note TEXT NOT NULL DEFAULT '');"
        )
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == 5
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(player)")}
    assert {"guest_status", "guest_decision_date", "padrinho_id", "canonical_player_id"} <= cols
    conn.execute(
        "INSERT INTO payment (player_id, amount_cents, paid_on) VALUES (1, 1500, '2025-01-03')"
    )
    assert tuple(conn.execute("SELECT status, section FROM attendance").fetchone()) == (
        "J",
        "reservas",
    )
    conn.close()


V4_SCHEMA = (
    V3_SCHEMA
    + """
CREATE TABLE payment (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    paid_on TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
ALTER TABLE player ADD COLUMN guest_status TEXT NOT NULL DEFAULT '';
ALTER TABLE player ADD COLUMN guest_decision_date TEXT NOT NULL DEFAULT '';
INSERT INTO player_alias VALUES ('a', 1);
INSERT INTO payment (player_id, amount_cents, paid_on) VALUES (1, 1500, '2025-01-03');
PRAGMA user_version = 4;
"""
)


def test_migrates_v4_database_preserving_related_records(tmp_path):
    path = tmp_path / "pdq.db"
    raw = sqlite3.connect(path)
    raw.executescript(V4_SCHEMA)
    raw.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == 5
    assert [tuple(r) for r in conn.execute("SELECT * FROM attendance")] == [
        (1, 1, "J", "obs", "reservas")
    ]
    assert [tuple(r) for r in conn.execute("SELECT * FROM player_alias")] == [("a", 1)]
    payments = conn.execute("SELECT player_id, amount_cents, paid_on FROM payment")
    assert [tuple(r) for r in payments] == [(1, 1500, "2025-01-03")]
    assert conn.execute("SELECT canonical_player_id FROM player WHERE id = 1").fetchone()[0] is None
    conn.close()

    conn = db.connect(path)
    assert db.schema_version(conn) == 5
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM player_alias").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 1
    conn.close()


def test_canonical_player_foreign_key_restricts_deletion(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'Original'), (2, 'Mesclado')")
    conn.execute("UPDATE player SET canonical_player_id = 1 WHERE id = 2")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM player WHERE id = 1")
    conn.close()


def test_resolves_canonical_identity_and_diagnostics():
    conn = db.connect(":memory:")
    conn.execute(
        "INSERT INTO player (id, pos, name, canonical_player_id) VALUES "
        "(1, 1, 'Canônico', NULL), (2, 2, 'Direto', 1), (3, 3, 'Cadeia', 2), "
        "(4, 4, 'Ciclo A', 5), (5, 5, 'Ciclo B', 4), (6, 6, 'Próprio', 6)"
    )
    conn.commit()

    assert db.resolve_canonical_player(conn, 1) == db.CanonicalIdentity(1, 1, (1,))
    assert db.resolve_canonical_player(conn, 2) == db.CanonicalIdentity(2, 1, (2, 1))
    assert db.resolve_canonical_player(conn, 3) == db.CanonicalIdentity(3, 1, (3, 2, 1))
    assert db.resolve_canonical_player(conn, 4) == db.CanonicalIdentity(4, None, (4, 5, 4), "cycle")
    assert db.resolve_canonical_player(conn, 6) == db.CanonicalIdentity(
        6, None, (6, 6), "self_reference"
    )

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO player (id, pos, name, canonical_player_id) VALUES (7, 7, 'Inválido', 99)"
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    assert db.resolve_canonical_player(conn, 7) == db.CanonicalIdentity(
        7, None, (7, 99), "invalid_reference"
    )
    assert db.canonical_player_id(conn, 3) == 1
    assert db.canonical_player_id(conn, 4) is None
    assert [d.player_id for d in db.identity_diagnostics(conn)] == [2, 3, 4, 5, 6, 7]
    conn.close()
