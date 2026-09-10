import pytest

<<<<<<< HEAD
from pdq import db, hygiene


@pytest.mark.parametrize(
    ("name", "padrinho"),
    [
        ("Zico (BRUNO)", "BRUNO"),
        ("Zico (AMIGO Bruno)", "Bruno"),
        ("Zico (CONVIDADO Bruno)", "Bruno"),
        ("Zico AMIGO Bruno", "Bruno"),
        ("Zico CONVIDADO Bruno", "Bruno"),
    ],
)
def test_extract_padrinho_recognizes_legacy_suffixes(name, padrinho):
    assert hygiene.extract_padrinho(name) == padrinho


def test_extract_padrinho_ignores_unannotated_name():
    assert hygiene.extract_padrinho("Zico") is None


def test_links_unique_canonical_padrinho_without_changing_name(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    name = "Zico (AMIGO RODRÍGO!)"
    conn.executemany("INSERT INTO player (pos, name) VALUES (?, ?)", [(1, "Rodrigo"), (2, name)])

    report = hygiene.link_legacy_padrinhos(conn)

    assert [(link.player_name, link.padrinho, link.padrinho_id) for link in report.linked] == [
        (name, "RODRÍGO!", 1)
    ]
    assert report.pending == []
    assert tuple(
        conn.execute("SELECT name, padrinho, padrinho_id FROM player WHERE id = 2").fetchone()
    ) == (
        name,
        "",
        1,
    )
    conn.close()


@pytest.mark.parametrize(
    ("players", "reason"),
    [
        ([(1, "Zico (BRUNO)")], "ausente"),
        ([(1, "Bruno"), (2, "BRUNO"), (3, "Zico (BRUNO)")], "ambíguo"),
    ],
)
def test_leaves_missing_or_ambiguous_padrinho_pending(tmp_path, players, reason):
    conn = db.connect(tmp_path / "pdq.db")
    conn.executemany(
        "INSERT INTO player (id, pos, name) VALUES (?, ?, ?)",
        [(id, id, name) for id, name in players],
    )

    report = hygiene.link_legacy_padrinhos(conn)

    assert [
        (pending.player_name, pending.padrinho, pending.reason) for pending in report.pending
    ] == [("Zico (BRUNO)", "BRUNO", reason)]
    assert (
        conn.execute("SELECT padrinho_id FROM player WHERE name = 'Zico (BRUNO)'").fetchone()[0]
        is None
    )
    conn.close()
=======
from pdq import aliases, db, hygiene


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute(
        "INSERT INTO player (pos, name) VALUES (1, 'Carlos Silva'), (2, 'C. Silva'), (3, 'Outro')"
    )
    conn.execute("INSERT INTO session (ordem, date) VALUES (1, '2025-01-01')")
    conn.execute("INSERT INTO attendance (player_id, session_id, status) VALUES (2, 1, 'X')")
    conn.execute("INSERT INTO player_alias (alias, player_id) VALUES ('carlos', 2)")
    conn.commit()
    yield conn
    conn.close()


def test_merge_redirects_aliases_learns_names_and_preserves_attendance(conn):
    result = hygiene.merge(conn, 2, 1)

    assert result == hygiene.MergeEvidence(
        2, "C. Silva", 1, "Carlos Silva", 1, ("carlos",), ("c silva", "carlos silva")
    )
    assert conn.execute("SELECT canonical_id FROM player WHERE id = 2").fetchone()[0] == 1
    alias = conn.execute("SELECT player_id FROM player_alias WHERE alias = 'carlos'").fetchone()
    assert alias[0] == 1
    assert conn.execute("SELECT player_id FROM attendance").fetchone()[0] == 2
    assert aliases.Resolver.from_db(conn).exact("C. Silva") == 1


@pytest.mark.parametrize(
    "source, canonical, message",
    [
        (1, 1, "diferentes"),
        (99, 1, "origem player_id 99 não existe"),
        (1, 99, "canônico player_id 99 não existe"),
    ],
)
def test_merge_rejects_invalid_players_without_changes(conn, source, canonical, message):
    before = _state(conn)
    with pytest.raises(hygiene.HygieneError, match=message):
        hygiene.merge(conn, source, canonical)
    assert _state(conn) == before


def test_merge_rejects_merged_source_noncanonical_target_chains_and_cycles(conn):
    hygiene.merge(conn, 2, 1)
    before = _state(conn)
    with pytest.raises(hygiene.HygieneError, match="origem player_id 2 já foi mesclada"):
        hygiene.merge(conn, 2, 3)
    assert _state(conn) == before
    with pytest.raises(hygiene.HygieneError, match="canônico player_id 2 não é canônico"):
        hygiene.merge(conn, 3, 2)
    assert _state(conn) == before


def test_merge_does_not_overwrite_conflicting_name_alias(conn):
    conn.execute("INSERT INTO player_alias (alias, player_id) VALUES ('c silva', 3)")
    conn.commit()
    result = hygiene.merge(conn, 2, 1)
    assert result.learned_aliases == ("carlos silva",)
    alias = conn.execute("SELECT player_id FROM player_alias WHERE alias = 'c silva'").fetchone()
    assert alias[0] == 3


def test_merge_rolls_back_when_a_late_write_fails(conn):
    conn.execute(
        "CREATE TRIGGER reject_canonical_alias BEFORE INSERT ON player_alias "
        "WHEN NEW.alias = 'carlos silva' BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    conn.commit()
    before = _state(conn)
    with pytest.raises(Exception, match="blocked"):
        hygiene.merge(conn, 2, 1)
    assert _state(conn) == before


def _state(conn):
    rows = conn.execute(
        "SELECT p.id, p.canonical_id, a.alias, a.player_id, "
        "(SELECT COUNT(*) FROM attendance x WHERE x.player_id = p.id) "
        "FROM player p LEFT JOIN player_alias a ON a.player_id = p.id "
        "ORDER BY p.id, a.alias"
    )
    return tuple(tuple(row) for row in rows)
>>>>>>> 3a51ac5 (feat: implement-logical-merge: Criar `pdq.hygiene` com a operação transacional de mescla que exige or)
