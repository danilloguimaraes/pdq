from dataclasses import FrozenInstanceError

import pytest

from pdq import db, hygiene


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.executemany(
        "INSERT INTO player (id, pos, classe, name) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "", "Zeca (padrinho: Bruno)"),
            (2, 2, "-", "Álvaro"),
            (3, 3, "", "Alvaro!"),
            (4, 4, "C", "Convidado"),
            (5, 5, "F", "Frequente"),
            (6, 6, "", "Sem presenças"),
            (7, 7, "", "Zeca - conv. Rodrigo"),
        ],
    )
    conn.executemany(
        "INSERT INTO session (id, ordem, date) VALUES (?, ?, ?)",
        [(1, 1, "2025-09-04"), (2, 2, "2025-08-28"), (3, 3, "2025-08-21")],
    )
    conn.executemany(
        "INSERT INTO attendance (player_id, session_id, status) VALUES (?, ?, ?)",
        [
            (1, 1, "X"),
            (1, 2, "J"),
            (1, 3, "F"),
            (2, 1, "X"),
            (3, 1, "X"),
            (4, 1, "X"),
            (5, 1, "J"),
            (7, 1, "-"),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def test_models_are_equal_and_immutable():
    item = hygiene.HygieneItem(1, "Zeca", 4)
    assert item == hygiene.HygieneItem(1, "Zeca", 4)
    assert hygiene.DuplicateCandidate(2, "Zéca", 1.0) == hygiene.DuplicateCandidate(2, "Zéca", 1.0)
    assert hygiene.CurationDecision(1, "promote") == hygiene.CurationDecision(1, "promote")
    with pytest.raises(FrozenInstanceError):
        item.presences = 5


def test_legacy_players_filters_counts_threshold_and_orders_normalized_name_and_id(conn):
    items = hygiene.legacy_players(conn, threshold=1)
    assert [(item.player_id, item.presences) for item in items] == [
        (1, 2),
        (2, 1),
        (3, 1),
    ]
    assert [item.player_id for item in hygiene.legacy_players(conn, threshold=2)] == [1]
    assert hygiene.legacy_players(conn, threshold=3) == []


def test_alerts_detect_padrinho_and_equivalent_names_in_deterministic_order(conn):
    items = hygiene.legacy_players(conn, threshold=1, similarity_cutoff=0.99)
    assert items[0].padrinho_annotation == "Bruno"
    candidates = items[1].duplicate_candidates
    assert [(candidate.player_id, candidate.score) for candidate in candidates] == [(3, 1.0)]
    assert hygiene.extract_padrinho_annotation("Zeca - conv. Rodrigo") == "Rodrigo"


def test_query_does_not_mutate_source_tables(conn):
    before = {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("player", "player_alias", "attendance")
    }
    hygiene.legacy_players(conn, threshold=1)
    after = {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("player", "player_alias", "attendance")
    }
    assert after == before
