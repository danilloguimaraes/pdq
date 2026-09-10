import pytest

from pdq import aliases, db, guest_lifecycle


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.executemany(
        "INSERT INTO player (pos, classe, name, padrinho, guest_status) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "C", "Zeca", "Ana", "pending"),
            (2, "C", "Beto", "", "pending"),
            (3, "F", "Fredo", "", ""),
        ],
    )
    conn.executemany(
        "INSERT INTO session (ordem, date) VALUES (?, ?)",
        [
            (1, "2025-09-04"),
            (2, "2025-09-03"),
            (3, "2025-09-02"),
            (4, "2025-09-01"),
            (5, "2025-08-31"),
        ],
    )
    conn.executemany(
        "INSERT INTO attendance (player_id, session_id, status) VALUES (?, ?, ?)",
        [
            (1, 1, "X"),
            (1, 2, "J"),
            (1, 3, "F"),
            (1, 4, "-"),
            (2, 1, "X"),
            (2, 2, "X"),
            (2, 3, "X"),
            (2, 4, "J"),
            (2, 5, "X"),
            (3, 1, "X"),
            (3, 2, "X"),
            (3, 3, "X"),
            (3, 4, "X"),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def test_pending_guests_counts_only_x_and_j_and_sorts_deterministically(conn):
    assert guest_lifecycle.pending_guests(conn) == [
        guest_lifecycle.GuestCandidate(2, "Beto", "", 5, "2025-09-03")
    ]
    conn.execute("UPDATE attendance SET status = 'X' WHERE player_id = 1 AND session_id = 3")
    conn.execute("UPDATE attendance SET status = 'J' WHERE player_id = 1 AND session_id = 4")
    conn.commit()
    assert [c.name for c in guest_lifecycle.pending_guests(conn)] == ["Beto", "Zeca"]
    assert [c.reached_on for c in guest_lifecycle.pending_guests(conn)] == [
        "2025-09-03",
        "2025-09-04",
    ]


@pytest.mark.parametrize("classe", ("F", "M"))
def test_promote_guest(conn, classe):
    result = guest_lifecycle.promote(conn, "Beto", classe, "2025-09-10")
    assert result.classe == classe and result.guest_status == "promoted"
    row = conn.execute(
        "SELECT classe, guest_status, guest_decision_date FROM player WHERE id = 2"
    ).fetchone()
    assert tuple(row) == (classe, "promoted", "2025-09-10")
    assert guest_lifecycle.pending_guests(conn) == []


@pytest.mark.parametrize(
    "keeps_guest,status", [(True, "declined_stays"), (False, "declined_leaves")]
)
def test_decline_guest_preserves_history(conn, keeps_guest, status):
    result = guest_lifecycle.decline(conn, 2, keeps_guest, "2025-09-10")
    assert result.guest_status == status and result.classe == ("C" if keeps_guest else "-")
    assert conn.execute("SELECT COUNT(*) FROM attendance WHERE player_id = 2").fetchone()[0] == 5


def test_decisions_validate_eligibility_date_and_are_atomic(conn):
    with pytest.raises(guest_lifecycle.GuestLifecycleError, match="não é convidado pendente"):
        guest_lifecycle.promote(conn, "Zeca", "F", "2025-09-10")
    with pytest.raises(guest_lifecycle.GuestLifecycleError, match="não é F ou M"):
        guest_lifecycle.promote(conn, "Beto", "C", "2025-09-10")
    with pytest.raises(guest_lifecycle.GuestLifecycleError, match="AAAA-MM-DD"):
        guest_lifecycle.decline(conn, "Beto", True, "10/09/2025")
    assert tuple(
        conn.execute("SELECT classe, guest_status FROM player WHERE id = 2").fetchone()
    ) == (
        "C",
        "pending",
    )


def test_resolves_id_name_and_alias(conn):
    aliases.learn(conn, "Betinho", 2)
    conn.commit()
    assert guest_lifecycle.promote(conn, "Betinho", "F", "2025-09-10").player_id == 2
