import pytest

from pdq import aliases, db, hygiene


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "pdq.db")
    connection.executemany(
        "INSERT INTO player (id, pos, classe, name) VALUES (?, ?, ?, ?)",
        [(1, 1, "F", "Carlos Silva"), (2, 2, "F", "C. Silva"), (3, 3, "F", "Outro")],
    )
    connection.execute("INSERT INTO session (ordem, date) VALUES (1, '2025-01-01')")
    connection.execute("INSERT INTO attendance (player_id, session_id, status) VALUES (2, 1, 'X')")
    connection.execute("INSERT INTO player_alias (alias, player_id) VALUES ('carlos', 2)")
    connection.commit()
    yield connection
    connection.close()


def _state(conn):
    return tuple(
        tuple(row)
        for row in conn.execute(
            "SELECT p.id, p.canonical_player_id, a.alias, a.player_id "
            "FROM player p LEFT JOIN player_alias a ON a.player_id = p.id ORDER BY p.id, a.alias"
        )
    )


def test_merge_preserves_legacy_attendance_and_redirects_aliases(conn):
    result = hygiene.merge(conn, 2, 1)

    assert result == hygiene.MergeEvidence(
        2, "C. Silva", 1, "Carlos Silva", 1, ("carlos",), ("c silva", "carlos silva")
    )
    assert conn.execute("SELECT canonical_player_id FROM player WHERE id = 2").fetchone()[0] == 1
    assert conn.execute("SELECT player_id FROM attendance").fetchone()[0] == 2
    assert aliases.Resolver.from_db(conn).exact("C. Silva") == 1


@pytest.mark.parametrize("source, target", [(1, 1), (99, 1), (1, 99)])
def test_merge_rejects_invalid_decisions_without_writes(conn, source, target):
    before = _state(conn)
    with pytest.raises(hygiene.HygieneError):
        hygiene.merge(conn, source, target)
    assert _state(conn) == before


def test_merge_is_atomic_when_alias_write_fails(conn):
    conn.execute(
        "CREATE TRIGGER reject_alias BEFORE INSERT ON player_alias "
        "WHEN NEW.alias = 'carlos silva' BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    conn.commit()
    before = _state(conn)
    with pytest.raises(Exception, match="blocked"):
        hygiene.merge(conn, 2, 1)
    assert _state(conn) == before


@pytest.mark.parametrize(
    ("name", "padrinho"),
    [("Zico (BRUNO)", "BRUNO"), ("Zico AMIGO Bruno", "Bruno")],
)
def test_extract_padrinho_recognizes_legacy_suffixes(name, padrinho):
    assert hygiene.extract_padrinho(name) == padrinho


def test_links_unique_padrinho_without_changing_name(tmp_path):
    connection = db.connect(tmp_path / "pdq.db")
    name = "Zico (AMIGO RODRÍGO!)"
    connection.executemany(
        "INSERT INTO player (pos, name) VALUES (?, ?)", [(1, "Rodrigo"), (2, name)]
    )
    report = hygiene.link_legacy_padrinhos(connection)
    assert [(link.player_name, link.padrinho_id) for link in report.linked] == [(name, 1)]
    assert tuple(
        connection.execute("SELECT name, padrinho_id FROM player WHERE id = 2").fetchone()
    ) == (name, 1)
    connection.close()
