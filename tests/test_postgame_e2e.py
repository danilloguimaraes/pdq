"""Ponta a ponta sobre a planilha legada real: importar -> propor -> confirmar -> exportar.

Garante que uma partida nova registrada pela lista do WhatsApp entra na planilha
regenerada como uma coluna a mais, sem alterar as 127 sessões históricas.
"""

from datetime import date
from pathlib import Path

from pdq import aliases, backup, db, exporter, importer, legacy, postgame, validate

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"


def test_full_cycle_over_legacy_database(imported_db, legacy_csv):
    conn, db_path = imported_db
    original = legacy.read_rows(legacy_csv)
    before = exporter.build_rows(conn)  # Presenças recalculadas, sem a inconsistência legada

    # 1) lista com reservas, furos e observações (11/09/2025)
    text = (FIXTURES / "lista_com_reservas_e_obs.txt").read_text(encoding="utf-8")
    proposal = postgame.build_proposal(conn, text, today=date(2025, 9, 12))
    assert proposal.pending() == [], postgame.render_summary(proposal)
    assert all(e.action == postgame.ACTION_LINK for e in proposal.entries)

    # ida e volta pelo JSON, como o usuário faria editando o arquivo
    proposal = postgame.Proposal.from_json(proposal.to_json())
    res = postgame.confirm(conn, proposal)
    assert res.attendance == 10 and res.created_players == []

    rows = exporter.build_rows(conn)
    assert len(rows) == len(original)  # nenhum jogador novo
    assert len(rows[0]) == len(original[0]) + 1  # uma sessão a mais
    assert rows[4][7] == "11/09/2025" and rows[0][7] == "Bora Bola"
    assert rows[1][7:10] == ["1", "2", "3"]
    # histórico intacto: sem a coluna nova, só mudam Ordem (deslocada em 1) e os
    # totais por jogador (Faltas/Presenças recalculados com a partida nova)
    stripped = [r[:7] + r[8:] for r in rows]
    diffs = validate.diff_rows(original, stripped)
    assert all(d.row == 1 or d.col in (5, 6) for d in diffs), diffs[:5]
    faltas = [d for d in diffs if d.col == 5]
    assert {original[d.row][4] for d in faltas} == {"MIGUEL", "SAULO"}  # os dois furos
    assert all(int(d.actual) == int(d.expected) + 1 for d in diffs if d.row == 1)
    body = {r[4]: r for r in rows[5:]}
    assert body["MIGUEL"][7] == "F" and body["SAULO"][7] == "F"
    assert body["DANTAS"][7] == "X" and body["ANDRE TOME"][7] == "X"  # J -> X na planilha
    assert rows[2][7] == "2" and rows[3][7] == "8"
    assert int(body["RODRIGO"][6]) == int(before[5][6]) + 1
    assert int(body["MIGUEL"][5]) == int(before[7][5]) + 1

    # 2) lista com vagas vazias e convidado novo (04/09/2025)
    text = (FIXTURES / "lista_com_vagas_e_padrinho.txt").read_text(encoding="utf-8")
    proposal = postgame.build_proposal(conn, text, today=date(2025, 9, 12))
    by = {e.raw_name: e for e in proposal.entries}
    assert by["Joãozinho"].action == postgame.ACTION_CREATE
    assert by["Pedrão"].action == postgame.ACTION_REVIEW  # parecido com "JOAO PEDRO - GK"
    by["Pedrão"].action = postgame.ACTION_CREATE
    res = postgame.confirm(conn, proposal)
    assert res.created_players == ["JOÃOZINHO", "PEDRÃO"]

    rows = exporter.build_rows(conn)
    assert len(rows) == len(original) + 2
    assert rows[4][7:10] == ["11/09/2025", "04/09/2025", "28/08/2025"]  # ordem por data
    assert [r[0] for r in rows[-2:]] == ["147", "148"]
    assert rows[-2][4] == "JOÃOZINHO" and rows[-2][3] == "2509" and rows[-2][8] == "X"
    assert rows[-2][7] == "-"  # não estava na lista de 11/09
    meta = conn.execute(
        "SELECT vagas_vazias FROM match_meta m JOIN session s ON s.id = m.session_id "
        "WHERE s.date = '2025-09-04'"
    ).fetchone()
    assert meta["vagas_vazias"] == 3
    assert aliases.Resolver.from_db(conn).exact("joaozinho") is not None

    # 3) a exportação continua um CSV bem formado e reimportável
    data = exporter.export_legacy_csv(conn)
    sheet = legacy.parse_sheet(legacy.read_rows(data))
    assert len(sheet.sessions) == 129 and len(sheet.players) == 148

    # 4) backup do banco migrado continua íntegro
    conn.commit()
    target = backup.create_backup(db_path.parent, db_path.parent.parent / "backups")
    assert backup.verify_backup(target) == []


def test_reimport_legacy_resets_postgame_tables(imported_db):
    conn, _ = imported_db
    conn.execute("INSERT INTO player_alias VALUES ('x', 1)")
    conn.execute("INSERT INTO match_meta (session_id) VALUES (1)")
    conn.commit()
    importer.import_legacy_csv(conn, Path("legacy/Pdq - Frequencia - Historico.csv"))
    assert conn.execute("SELECT COUNT(*) FROM player_alias").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM match_meta").fetchone()[0] == 0
    assert db.schema_version(conn) == db.SCHEMA_VERSION
