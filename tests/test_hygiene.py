import pytest

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
