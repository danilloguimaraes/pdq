import pytest

from pdq import correction, db, exporter
from pdq.correction import CorrectionError


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "pdq.db")
    c.executemany(
        "INSERT INTO player (pos, classe, posicao, legacy_id, name) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "M", "L", "2302", "RODRIGO"),
            (2, "M", "L", "2302", "DANILLO"),
            (3, "M", "L", "2303", "GUSTAVO BASTOS"),
            (4, "F", "L", "2406", "GUSTAVO OLIVEIRA"),
            (5, "F", "G", "2302", "GUILHERME GK"),
            (6, "M", "L", "2302", "SAULO"),
        ],
    )
    c.executemany(
        "INSERT INTO session (ordem, date, venue) VALUES (?, ?, ?)",
        [(1, "2025-09-04", "Fair Play"), (2, "2025-08-28", "Fair Play")],
    )
    c.executemany(
        "INSERT INTO attendance (player_id, session_id, status, note, section) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (5, 1, "X", "", "goleiros"),
            (1, 1, "X", "", "linha"),
            (2, 1, "F", "", "linha"),
            (3, 1, "X", "chegou tarde", "linha"),
            (6, 1, "J", "", "reservas"),
            (1, 2, "X", "", ""),
        ],
    )
    c.execute("INSERT INTO match_meta (session_id, vagas_vazias, raw_list) VALUES (1, 2, 'lista')")
    c.execute("INSERT INTO player_alias VALUES ('gustavo', 3)")
    c.commit()
    yield c
    c.close()


def attendance(conn, date):
    return {
        r["name"]: (r["status"], r["section"], r["note"])
        for r in conn.execute(
            "SELECT p.name, a.status, a.section, a.note FROM attendance a "
            "JOIN player p ON p.id = a.player_id JOIN session s ON s.id = a.session_id "
            "WHERE s.date = ?",
            (date,),
        )
    }


# --- localização ----------------------------------------------------------------


def test_find_session_and_errors(conn):
    assert correction.find_session(conn, "2025-09-04")["id"] == 1
    with pytest.raises(CorrectionError, match="não existe sessão"):
        correction.find_session(conn, "2025-01-01")
    with pytest.raises(CorrectionError, match="AAAA-MM-DD"):
        correction.find_session(conn, "04/09/2025")


def test_find_player_by_id_name_alias_and_ambiguity(conn):
    assert correction.find_player(conn, 2)["name"] == "DANILLO"
    assert correction.find_player(conn, "2")["name"] == "DANILLO"
    assert correction.find_player(conn, "danillo")["id"] == 2
    assert correction.find_player(conn, "Guilherme Gk")["id"] == 5
    assert correction.find_player(conn, "gustavo")["id"] == 3  # alias aprendido
    with pytest.raises(CorrectionError, match="não existe"):
        correction.find_player(conn, 99)
    with pytest.raises(CorrectionError, match="não encontrado"):
        correction.find_player(conn, "Zico")
    conn.execute("INSERT INTO player (pos, name) VALUES (7, 'Danillo')")
    with pytest.raises(CorrectionError, match="ambíguo"):
        correction.find_player(conn, "danillo")


def test_show_session_orders_by_section(conn):
    view = correction.show_session(conn, "2025-09-04")
    assert view.ordem == 1 and view.venue == "Fair Play" and view.vagas_vazias == 2
    assert [(e.name, e.status, e.section) for e in view.entries] == [
        ("GUILHERME GK", "X", "goleiros"),
        ("DANILLO", "F", "linha"),
        ("GUSTAVO BASTOS", "X", "linha"),
        ("RODRIGO", "X", "linha"),
        ("SAULO", "J", "reservas"),
    ]
    text = correction.render_session(view)
    assert "Partida 2025-09-04 @ Fair Play" in text
    assert "3 presença, 1 furo, 1 jogou, 0 não jogou, 2 vagas vazias" in text
    assert "# chegou tarde" in text


# --- relink ----------------------------------------------------------------------


def test_relink_moves_attendance_and_learns_alias(conn):
    res = correction.relink(conn, "2025-09-04", "Gustavo Bastos", 4, alias="Gustavo")
    assert (res.old_player, res.new_player, res.learned_alias) == (
        "GUSTAVO BASTOS",
        "GUSTAVO OLIVEIRA",
        "gustavo",
    )
    att = attendance(conn, "2025-09-04")
    assert "GUSTAVO BASTOS" not in att
    assert att["GUSTAVO OLIVEIRA"] == ("X", "linha", "chegou tarde")  # tudo preservado
    alias = conn.execute("SELECT player_id FROM player_alias WHERE alias='gustavo'").fetchone()
    assert alias[0] == 4
    # a outra sessão não é afetada
    assert attendance(conn, "2025-08-28") == {"RODRIGO": ("X", "", "")}


def test_relink_rejects_same_target_and_duplicates(conn):
    with pytest.raises(CorrectionError, match="já é o jogador"):
        correction.relink(conn, "2025-09-04", 1, 1)
    with pytest.raises(CorrectionError, match="já tem registro"):
        correction.relink(conn, "2025-09-04", 1, 2)
    with pytest.raises(CorrectionError, match="não tem registro"):
        correction.relink(conn, "2025-09-04", 4, 1)
    assert attendance(conn, "2025-09-04")["RODRIGO"] == ("X", "linha", "")


# --- status / seção -------------------------------------------------------------


def test_set_status_toggles_and_supports_not_played(conn):
    res = correction.set_status(conn, "2025-09-04", "Danillo", "X")
    assert (res.old_status, res.new_status) == ("F", "X")
    res = correction.set_status(conn, "2025-09-04", "Saulo", "-")  # reserva que não jogou
    assert (res.old_status, res.new_status) == ("J", "-")
    att = attendance(conn, "2025-09-04")
    assert att["DANILLO"] == ("X", "linha", "")
    assert att["SAULO"] == ("-", "reservas", "")  # linha preservada
    with pytest.raises(CorrectionError, match="não é X, F, J ou -"):
        correction.set_status(conn, "2025-09-04", "Saulo", "Z")
    with pytest.raises(CorrectionError, match="não tem registro"):
        correction.set_status(conn, "2025-09-04", "Gustavo Oliveira", "X")


def test_not_played_is_invisible_in_legacy_export(conn):
    correction.set_status(conn, "2025-09-04", "Saulo", "-")
    correction.set_section(conn, "2025-09-04", "Rodrigo", "goleiros")
    data = exporter.export_legacy_csv(conn).decode("utf-8")
    saulo = next(line for line in data.splitlines() if "SAULO" in line)
    assert ";X;" not in saulo and ";J;" not in saulo


def test_set_section(conn):
    res = correction.set_section(conn, "2025-09-04", "Saulo", "linha")
    assert (res.old_section, res.new_section) == ("reservas", "linha")
    assert attendance(conn, "2025-09-04")["SAULO"] == ("J", "linha", "")
    with pytest.raises(CorrectionError, match="seção"):
        correction.set_section(conn, "2025-09-04", "Saulo", "banco")


# --- data / local -----------------------------------------------------------------


def test_set_date_renumbers(conn):
    res = correction.set_date(conn, "2025-09-04", "2025-08-21")  # vira a mais antiga
    assert (res.old_date, res.new_date) == ("2025-09-04", "2025-08-21")
    rows = conn.execute("SELECT ordem, date FROM session ORDER BY ordem").fetchall()
    assert [tuple(r) for r in rows] == [(1, "2025-08-28"), (2, "2025-08-21")]
    assert attendance(conn, "2025-08-21")["SAULO"] == ("J", "reservas", "")
    with pytest.raises(CorrectionError, match="já existe sessão"):
        correction.set_date(conn, "2025-08-21", "2025-08-28")
    with pytest.raises(CorrectionError, match="já está em"):
        correction.set_date(conn, "2025-08-21", "2025-08-21")
    with pytest.raises(CorrectionError, match="nova data inválida"):
        correction.set_date(conn, "2025-08-21", "21/08/2025")


def test_set_venue(conn):
    res = correction.set_venue(conn, "2025-09-04", "  Bora Bola ")
    assert (res.old_venue, res.new_venue) == ("Fair Play", "Bora Bola")
    assert conn.execute("SELECT venue FROM session WHERE id = 1").fetchone()[0] == "Bora Bola"


# --- exclusão ---------------------------------------------------------------------


def test_delete_session_requires_confirmation(conn):
    with pytest.raises(CorrectionError, match="exige confirmação"):
        correction.delete_session(conn, "2025-09-04")
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 2

    res = correction.delete_session(conn, "2025-09-04", confirm=True)
    assert (res.date, res.attendance) == ("2025-09-04", 5)
    rows = conn.execute("SELECT ordem, date FROM session").fetchall()
    assert [tuple(r) for r in rows] == [(1, "2025-08-28")]
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM match_meta").fetchone()[0] == 0
    # jogadores e aliases sobrevivem
    assert conn.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM player_alias").fetchone()[0] == 1
