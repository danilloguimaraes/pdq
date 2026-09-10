from datetime import date
from pathlib import Path

import pytest

from pdq import aliases, db, exporter, postgame
from pdq.postgame import (
    ACTION_CREATE,
    ACTION_LINK,
    ACTION_REVIEW,
    ACTION_SKIP,
    NewPlayer,
    Proposal,
    ProposalEntry,
    ProposalError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"
TODAY = date(2025, 9, 9)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "pdq.db")
    c.executemany(
        "INSERT INTO player (pos, classe, posicao, legacy_id, name) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "M", "L", "2302", "RODRIGO"),
            (2, "M", "L", "2302", "DANILLO"),
            (3, "M", "L", "2302", "MIGUEL"),
            (4, "M", "L", "2303", "GUSTAVO BASTOS"),
            (5, "F", "G", "2302", "GUILHERME GK"),
            (6, "M", "L", "2303", "AMELIO"),
            (7, "F", "G", "2302", "NAALDI - GK"),
            (8, "M", "L", "2303", "VICTOR BASTOS"),
            (9, "-", "L", "2302", "DANTAS"),
            (10, "M", "L", "2302", "SAULO"),
            (11, "M", "L", "2303", "ANDRE TOME"),
            (12, "F", "L", "2302", "LAION"),
            (13, "F", "L", "2406", "GUSTAVO OLIVEIRA"),
        ],
    )
    c.executemany(
        "INSERT INTO session (ordem, date, venue) VALUES (?, ?, ?)",
        [(1, "2025-08-28", "Fair Play"), (2, "2025-08-21", "Bora Bola")],
    )
    c.execute("INSERT INTO attendance (player_id, session_id, status) VALUES (1, 1, 'X')")
    c.commit()
    yield c
    c.close()


# --- build_proposal -------------------------------------------------------------


def test_proposal_basic_all_exact(conn):
    p = postgame.build_proposal(conn, fixture("lista_basica.txt"), today=TODAY)
    assert p.date == "2025-08-28" and p.venue == "Fair Play"
    assert p.vagas_vazias == 0
    assert all(e.action == ACTION_LINK and e.status == "X" for e in p.entries)
    assert [e.player_name for e in p.entries[:3]] == ["GUILHERME GK", "NAALDI - GK", "RODRIGO"]
    assert p.counts() == {"X": 12, "F": 0, "J": 0}
    assert any("já existe sessão" in w for w in p.warnings)  # 28/08 já está no banco


def test_proposal_reservas_furos_obs(conn):
    p = postgame.build_proposal(conn, fixture("lista_com_reservas_e_obs.txt"), today=TODAY)
    assert p.date == "2025-09-11" and p.venue == "Bora Bola"
    by = {e.raw_name: e for e in p.entries}
    assert by["Miguel"].status == "F"
    assert by["Saulo"].status == "F"
    assert by["Dantas"].status == "J" and by["Andre Tome"].status == "J"
    assert by["Amelio"].status == "X" and by["Amelio"].observacao == "só o primeiro tempo"
    assert by["Guilherme GK"].observacao == "chega 20h30"
    assert p.counts() == {"X": 6, "F": 2, "J": 2}
    assert p.pending() == []


def test_proposal_new_player_and_review(conn):
    p = postgame.build_proposal(conn, fixture("lista_com_vagas_e_padrinho.txt"), today=TODAY)
    assert p.date == "2025-09-04"  # ano inferido de today
    assert p.venue == "Fair Play" and p.vagas_vazias == 3
    by = {e.raw_name: e for e in p.entries}
    joao = by["Joãozinho"]
    assert joao.action == ACTION_CREATE and joao.player_id is None
    assert joao.new_player == NewPlayer(name="JOÃOZINHO", posicao="L", padrinho="Danillo")
    assert joao.suggestions == []
    pedrao = by["Pedrão"]
    assert pedrao.new_player is not None and pedrao.new_player.padrinho == "Rodrigo"
    # sem candidato parecido no banco de teste -> criar direto
    assert pedrao.action in (ACTION_CREATE, ACTION_REVIEW)
    assert [e.raw_name for e in p.pending()] == [e.raw_name for e in p.entries if e.suggestions]


def test_proposal_review_when_ambiguous(conn):
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Gustavo\n2. Naaldi", today=TODAY)
    g, n = p.entries
    assert g.action == ACTION_REVIEW and g.player_id is None
    assert [s["name"] for s in g.suggestions] == ["GUSTAVO BASTOS", "GUSTAVO OLIVEIRA"]
    assert g.new_player == NewPlayer(name="GUSTAVO", posicao="L", padrinho="")
    assert n.action == ACTION_LINK and n.player_name == "NAALDI - GK"
    assert postgame.render_summary(p).count("REVISAR") == 1


def test_proposal_goalkeeper_new_player_posicao(conn):
    p = postgame.build_proposal(conn, "Pdq 05/09\nGoleiros\n1. Zequinha", today=TODAY)
    assert p.entries[0].new_player.posicao == "G"


def test_proposal_uses_alias_table(conn):
    aliases.learn(conn, "Gu Bastos", 4)
    conn.commit()
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Gu Bastos", today=TODAY)
    assert p.entries[0].action == ACTION_LINK and p.entries[0].player_id == 4


def test_proposal_date_rules(conn):
    assert postgame.build_proposal(conn, "1. Rodrigo", date_iso="2025-09-05").date == "2025-09-05"
    # data da lista tem prioridade menor que --date
    p = postgame.build_proposal(conn, "Pdq 01/01/2024\n1. Rodrigo", date_iso="2025-09-05")
    assert p.date == "2025-09-05"
    # sem ano e no futuro em relação a hoje -> ano anterior
    assert postgame.build_proposal(conn, "Pdq 20/12\n1. Rodrigo", today=TODAY).date == "2024-12-20"
    with pytest.raises(ProposalError, match="não traz data"):
        postgame.build_proposal(conn, "1. Rodrigo", today=TODAY)
    with pytest.raises(ProposalError, match="AAAA-MM-DD"):
        postgame.build_proposal(conn, "1. Rodrigo", date_iso="05/09/2025")
    with pytest.raises(ProposalError, match="data inválida na lista"):
        postgame.build_proposal(conn, "Pdq 31/02/2025\n1. Rodrigo", today=TODAY)


def test_proposal_venue_rules(conn):
    assert postgame.build_proposal(conn, "Pdq 05/09 bora bola\n1. A", today=TODAY).venue == (
        "Bora Bola"
    )
    assert postgame.build_proposal(conn, "Pdq 05/09\n1. A", today=TODAY).venue == "Fair Play"
    assert postgame.build_proposal(conn, "Pdq 05/09\n1. A", venue="Arena", today=TODAY).venue == (
        "Arena"
    )
    assert postgame.build_proposal(conn, "Pdq 05/09\n1. A", venue="", today=TODAY).venue == ""


def test_proposal_warns_on_duplicate_and_unparsed(conn):
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Rodrigo\nlevo a bola\n2. RODRIGO", today=TODAY)
    assert any("mesmo jogador" in w for w in p.warnings)
    assert any("levo a bola" in w for w in p.warnings)
    p = postgame.build_proposal(conn, "Pdq 05/09\n", today=TODAY)
    assert any("nenhuma linha" in w for w in p.warnings)


# --- JSON ---------------------------------------------------------------------------


def test_json_roundtrip(conn):
    p = postgame.build_proposal(conn, fixture("lista_com_vagas_e_padrinho.txt"), today=TODAY)
    text = p.to_json()
    assert '"schema": "pdq.proposal/1"' in text and "Joãozinho" in text  # ensure_ascii=False
    assert Proposal.from_json(text) == p


def test_json_rejects_garbage():
    with pytest.raises(ProposalError, match="JSON inválido"):
        Proposal.from_json("{")
    with pytest.raises(ProposalError, match="schema"):
        Proposal.from_json('{"date": "2025-01-01"}')
    with pytest.raises(ProposalError, match="campo inesperado"):
        Proposal.from_json('{"schema": "pdq.proposal/1", "date": "2025-01-01", "foo": 1}')


# --- confirm -------------------------------------------------------------------------


def test_confirm_writes_session_attendance_meta_and_aliases(conn):
    p = postgame.build_proposal(conn, fixture("lista_com_reservas_e_obs.txt"), today=TODAY)
    res = postgame.confirm(conn, p)

    assert res.date == "2025-09-11" and res.attendance == 10 and res.created_players == []
    # nada a aprender: "Naaldi GK" normaliza igual a "NAALDI - GK"
    assert res.learned_aliases == []
    sess = conn.execute("SELECT * FROM session ORDER BY ordem").fetchall()
    assert [(s["ordem"], s["date"]) for s in sess] == [
        (1, "2025-09-11"),
        (2, "2025-08-28"),
        (3, "2025-08-21"),
    ]
    assert sess[0]["venue"] == "Bora Bola"
    meta = conn.execute("SELECT * FROM match_meta WHERE session_id = ?", (res.session_id,))
    meta = meta.fetchone()
    assert meta["vagas_vazias"] == 0 and meta["raw_list"] == fixture("lista_com_reservas_e_obs.txt")
    att = {
        r["name"]: (r["status"], r["note"])
        for r in conn.execute(
            "SELECT p.name, a.status, a.note FROM attendance a JOIN player p ON p.id=a.player_id "
            "WHERE a.session_id = ?",
            (res.session_id,),
        )
    }
    assert att["MIGUEL"] == ("F", "") and att["SAULO"] == ("F", "")
    assert att["DANTAS"] == ("J", "") and att["ANDRE TOME"] == ("J", "")
    assert att["AMELIO"] == ("X", "só o primeiro tempo")
    assert att["GUILHERME GK"] == ("X", "chega 20h30")
    # a seção da lista é persistida (E2)
    sections = {
        r["name"]: r["section"]
        for r in conn.execute(
            "SELECT p.name, a.section FROM attendance a JOIN player p ON p.id=a.player_id "
            "WHERE a.session_id = ?",
            (res.session_id,),
        )
    }
    assert sections["GUILHERME GK"] == "goleiros"
    assert sections["AMELIO"] == "linha"
    assert sections["DANTAS"] == "reservas"
    # a partida anterior permanece intacta
    assert conn.execute("SELECT status FROM attendance WHERE session_id = 1").fetchone()[0] == "X"


def test_confirm_creates_new_player_with_editable_name_and_padrinho(conn):
    p = postgame.build_proposal(conn, fixture("lista_com_vagas_e_padrinho.txt"), today=TODAY)
    by = {e.raw_name: e for e in p.entries}
    by["Joãozinho"].new_player.name = "JOAO PEDRO (CONVIDADO DANILLO)"  # nome editado pelo usuário
    by["Pedrão"].action = ACTION_CREATE
    res = postgame.confirm(conn, p)

    assert res.created_players == ["JOAO PEDRO (CONVIDADO DANILLO)", "PEDRÃO"]
    rows = conn.execute("SELECT * FROM player WHERE pos > 13 ORDER BY pos").fetchall()
    assert [
        (
            r["pos"],
            r["name"],
            r["classe"],
            r["padrinho"],
            r["padrinho_id"],
            r["posicao"],
            r["legacy_id"],
        )
        for r in rows
    ] == [
        (14, "JOAO PEDRO (CONVIDADO DANILLO)", "C", "Danillo", 2, "L", "2509"),
        (15, "PEDRÃO", "C", "Rodrigo", 1, "L", "2509"),
    ]
    # apelido escrito na lista aprendido para o jogador novo
    assert aliases.Resolver.from_db(conn).exact("Joãozinho") == rows[0]["id"]
    assert res.learned_aliases == ["joaozinho"]  # "Pedrão" == "PEDRÃO" normalizado
    meta = conn.execute("SELECT vagas_vazias FROM match_meta").fetchone()
    assert meta["vagas_vazias"] == 3


def test_confirm_link_after_review_learns_alias(conn):
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Gustavo\n2. Gu Oliveira", today=TODAY)
    assert len(p.pending()) == 2
    p.entries[0].action, p.entries[0].player_id = ACTION_LINK, 4
    p.entries[1].action = ACTION_SKIP
    res = postgame.confirm(conn, p)
    assert res.attendance == 1 and res.learned_aliases == ["gustavo"]
    assert aliases.Resolver.from_db(conn).exact("GUSTAVO") == 4
    # da próxima vez casa sozinho
    p2 = postgame.build_proposal(conn, "Pdq 12/09\n1. Gustavo", today=date(2025, 9, 13))
    assert p2.entries[0].action == ACTION_LINK and p2.entries[0].player_id == 4


def test_confirm_sets_padrinho_of_existing_player_if_empty(conn):
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Laion (padrinho: Rodrigo)", today=TODAY)
    postgame.confirm(conn, p)
    assert tuple(
        conn.execute("SELECT padrinho, padrinho_id FROM player WHERE name='LAION'").fetchone()
    ) == (
        "Rodrigo",
        1,
    )


@pytest.mark.parametrize("padrinho", ["André Tomé", "Ninguém", "Gustavo"])
def test_confirm_keeps_text_and_links_only_unique_canonical_padrinho(conn, padrinho):
    p = postgame.build_proposal(conn, f"Pdq 05/09\n1. Laion (padrinho: {padrinho})", today=TODAY)
    postgame.confirm(conn, p)
    text, padrinho_id = conn.execute(
        "SELECT padrinho, padrinho_id FROM player WHERE name='LAION'"
    ).fetchone()
    assert text == padrinho
    assert padrinho_id == (11 if padrinho == "André Tomé" else None)


def test_confirm_refuses_pending_and_writes_nothing(conn):
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Gustavo\n2. Rodrigo", today=TODAY)
    with pytest.raises(ProposalError, match="pendente de revisão"):
        postgame.confirm(conn, p)
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1


def test_confirm_refuses_duplicate_session(conn):
    p = postgame.build_proposal(conn, "Pdq 28/08/2025\n1. Rodrigo", today=TODAY)
    with pytest.raises(ProposalError, match="já existe sessão"):
        postgame.confirm(conn, p)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda e: setattr(e, "status", "Z"), "não é X, F ou J"),
        (lambda e: setattr(e, "action", "fly"), "action desconhecida"),
        (lambda e: setattr(e, "player_id", 999), "não existe"),
        (lambda e: (setattr(e, "action", ACTION_CREATE), setattr(e, "new_player", None)), "vazio"),
        (
            lambda e: (
                setattr(e, "action", ACTION_CREATE),
                setattr(e, "new_player", NewPlayer(name="X", posicao="Q")),
            ),
            "posicao",
        ),
    ],
)
def test_confirm_validation_errors(conn, mutate, message):
    p = postgame.build_proposal(conn, "Pdq 05/09\n1. Rodrigo", today=TODAY)
    mutate(p.entries[0])
    with pytest.raises(ProposalError, match=message):
        postgame.confirm(conn, p)


def test_confirm_refuses_same_player_twice(conn):
    p = Proposal(
        date="2025-09-05",
        entries=[
            ProposalEntry("Rodrigo", "linha", "X", ACTION_LINK, player_id=1),
            ProposalEntry("Rod", "linha", "X", ACTION_LINK, player_id=1),
        ],
    )
    with pytest.raises(ProposalError, match="repetido"):
        postgame.confirm(conn, p)


def test_confirmed_match_appears_in_legacy_export(conn):
    p = postgame.build_proposal(conn, fixture("lista_com_reservas_e_obs.txt"), today=TODAY)
    postgame.confirm(conn, p)
    rows = exporter.build_rows(conn)
    assert rows[4][7] == "11/09/2025" and rows[0][7] == "Bora Bola"
    assert rows[1][7:] == ["1", "2", "3"]
    body = {r[4]: r for r in rows[5:]}
    assert body["MIGUEL"][7] == "F" and body["DANTAS"][7] == "X"  # J exportado como presença
    assert body["RODRIGO"][7] == "X" and body["LAION"][7] == "-"
    assert rows[2][7] == "2" and rows[3][7] == "8"  # Faltas e Presencas da sessão
