import pytest

from pdq import db, hygiene


@pytest.fixture
def curated_conn(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.executemany(
        "INSERT INTO player (id, pos, name) VALUES (?, ?, ?)",
        [(1, 1, "Rodrigo"), (2, 2, "Álvaro"), (3, 3, "Outro"), (4, 4, "Quarto")],
    )
    conn.execute("INSERT INTO player_alias (alias, player_id) VALUES ('rodriguinho', 1)")
    conn.commit()
    yield conn
    conn.close()


def _curation_state(conn):
    return {
        table: tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"))
        for table in ("player", "player_alias", "attendance", "payment")
    }


@pytest.mark.parametrize(
    ("reference", "expected"),
    [(1, 1), ("1", 1), ("rODRÍGO!", 1), ("RODRIGUINHO", 1)],
)
def test_resolve_player_reference_accepts_id_name_and_alias(curated_conn, reference, expected):
    assert hygiene.resolve_player_reference(curated_conn, reference) == expected


@pytest.mark.parametrize("reference", ["", "   ", 99, "99", "ninguém"])
def test_resolve_player_reference_rejects_empty_or_missing_without_writes(curated_conn, reference):
    before = _curation_state(curated_conn)
    with pytest.raises(hygiene.HygieneError):
        hygiene.resolve_player_reference(curated_conn, reference)
    assert _curation_state(curated_conn) == before


def test_resolve_player_reference_rejects_name_alias_collision_without_writes(curated_conn):
    curated_conn.execute("INSERT INTO player_alias (alias, player_id) VALUES ('alvaro', 1)")
    curated_conn.commit()
    before = _curation_state(curated_conn)
    with pytest.raises(hygiene.HygieneError, match="ambígua"):
        hygiene.resolve_player_reference(curated_conn, "Álvaro")
    assert _curation_state(curated_conn) == before


def test_resolve_player_reference_rejects_alias_alias_collision_without_writes(curated_conn):
    curated_conn.execute("INSERT INTO player_alias (alias, player_id) VALUES ('apelido', 1)")
    curated_conn.execute("INSERT INTO player_alias (alias, player_id) VALUES ('apelido!', 2)")
    curated_conn.commit()
    before = _curation_state(curated_conn)
    with pytest.raises(hygiene.HygieneError, match="ambígua"):
        hygiene.resolve_player_reference(curated_conn, "APELIDO")
    assert _curation_state(curated_conn) == before


def test_preview_resolves_all_decisions_without_writes(curated_conn):
    before = _curation_state(curated_conn)
    result = hygiene.preview(
        curated_conn,
        [
            hygiene.ReclassifyDecision("ÁLVARO", "F"),
            hygiene.SetPadrinhoDecision("Outro", "rodriguinho"),
            hygiene.MergeDecision("Quarto", "Rodrigo"),
        ],
    )
    assert result == hygiene.HygienePreview(
        (hygiene.Reclassification(2, "F"),),
        (hygiene.PadrinhoLink(3, "Outro", "", 1),),
        (hygiene.Merge(4, 1),),
    )
    assert _curation_state(curated_conn) == before


@pytest.mark.parametrize(
    "decisions",
    [
        [hygiene.MergeDecision(1, 1)],
        [hygiene.MergeDecision(1, 2), hygiene.MergeDecision(1, 3)],
        [hygiene.MergeDecision(1, 2), hygiene.MergeDecision(3, 2)],
        [hygiene.MergeDecision(1, 2), hygiene.MergeDecision(2, 3)],
        [hygiene.ReclassifyDecision(1, "F"), hygiene.MergeDecision(1, 2)],
        [hygiene.ReclassifyDecision(1, "F"), hygiene.ReclassifyDecision(1, "M")],
        [hygiene.SetPadrinhoDecision(1, 2), hygiene.MergeDecision(1, 3)],
        [hygiene.SetPadrinhoDecision(1, 2), hygiene.MergeDecision(2, 3)],
        [hygiene.SetPadrinhoDecision(1, 2), hygiene.SetPadrinhoDecision(1, 3)],
        [hygiene.SetPadrinhoDecision(1, 1)],
    ],
)
def test_preview_rejects_incompatible_decisions_without_writes(curated_conn, decisions):
    before = _curation_state(curated_conn)
    with pytest.raises(hygiene.HygieneError):
        hygiene.preview(curated_conn, decisions)
    assert _curation_state(curated_conn) == before


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
