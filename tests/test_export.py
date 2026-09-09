import subprocess
import sys

from pdq import exporter, legacy
from pdq.validate import diff_rows


def test_export_strict_quirk_is_byte_identical(imported_db, legacy_csv):
    conn, _ = imported_db
    exported = exporter.export_legacy_csv(conn, strict_legacy_quirk=True)
    assert exported == legacy_csv.read_bytes()


def test_export_recomputed_differs_only_in_stale_presencas(imported_db, legacy_csv):
    conn, _ = imported_db
    original = legacy.read_rows(legacy_csv)
    recomputed = exporter.build_rows(conn, strict_legacy_quirk=False)
    diffs = diff_rows(original, recomputed)
    assert len(diffs) == 20
    presencas_col = legacy.FIXED_COLUMNS - 1
    for d in diffs:
        assert d.col == presencas_col, d
        assert d.row >= legacy.HEADER_ROWS, d
        assert int(d.actual) == int(d.expected) + 1, d
        # todos os afetados estiveram presentes em 21/08/2025
        idx = original[4].index("21/08/2025")
        assert original[d.row][idx] == "X"


def test_export_layout(imported_db):
    conn, _ = imported_db
    rows = exporter.build_rows(conn)
    assert len(rows) == 151
    assert {len(r) for r in rows} == {134}
    assert rows[1][6] == "Ordem" and rows[2][6] == "Faltas" and rows[3][6] == "Presencas"
    assert rows[4][:7] == [*legacy.COLUMN_HEADER, legacy.PRESENCAS_HEADER_LABEL]
    assert rows[4][7] == "28/08/2025" and rows[4][-1] == "23/02/2023"


def test_cli_export_to_file(imported_db, tmp_path, legacy_csv):
    _, db_path = imported_db
    out = tmp_path / "out.csv"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pdq",
            "export-legacy",
            "--db",
            str(db_path),
            "--strict-legacy-quirk",
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == legacy_csv.read_bytes()
