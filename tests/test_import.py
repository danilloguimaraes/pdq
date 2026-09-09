import subprocess
import sys

import pytest

from pdq import legacy


def test_parse_sheet_shape(legacy_csv):
    sheet = legacy.parse_sheet(legacy.read_rows(legacy_csv))
    assert len(sheet.players) == 146
    assert len(sheet.sessions) == 127
    assert sheet.sessions[0].ordem == 1  # planilha começa pela mais recente
    assert sheet.sessions[-1].ordem == 127


def test_parse_sheet_rejects_invalid_cell(legacy_csv):
    rows = legacy.read_rows(legacy_csv)
    rows[5][7] = "Z"
    with pytest.raises(ValueError, match="células inválidas"):
        legacy.parse_sheet(rows)


def test_import_counts(imported_db):
    conn, _ = imported_db
    assert conn.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 146
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 127
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 146 * 127
    counts = dict(
        conn.execute("SELECT status, COUNT(*) FROM attendance GROUP BY status").fetchall()
    )
    assert counts == {"X": 2371, "F": 16, "-": 16155}


def test_import_session_metadata(imported_db):
    conn, _ = imported_db
    row = conn.execute("SELECT venue, ordem FROM session WHERE date = '2025-08-28'").fetchone()
    assert row["venue"] == "Fair Play"
    assert row["ordem"] == 1
    first = conn.execute("SELECT date, venue FROM session WHERE ordem = 127").fetchone()
    assert first["date"] == "2023-02-23"
    assert first["venue"] == "Bora Bola"


def test_import_preserves_player_text(imported_db):
    conn, _ = imported_db
    row = conn.execute("SELECT * FROM player WHERE pos = 2").fetchone()
    assert (row["name"], row["classe"], row["posicao"], row["legacy_id"]) == (
        "DANILLO",
        "M",
        "L",
        "2302",
    )
    # espaços finais e nomes vazios são preservados
    assert conn.execute("SELECT name FROM player WHERE pos = 142").fetchone()[0].endswith(" ")
    assert conn.execute("SELECT name FROM player WHERE pos = 146").fetchone()[0] == ""


def test_import_is_full_replace(imported_db, legacy_csv):
    from pdq import importer

    conn, _ = imported_db
    importer.import_legacy_csv(conn, legacy_csv)
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 146 * 127


def test_cli_import_legacy(tmp_path, legacy_csv):
    db_path = tmp_path / "t.db"
    proc = subprocess.run(
        [sys.executable, "-m", "pdq", "import-legacy", str(legacy_csv), "--db", str(db_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "146 jogadores" in proc.stdout
    assert db_path.is_file()
