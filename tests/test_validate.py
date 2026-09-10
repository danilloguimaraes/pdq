import subprocess
import sys

from pdq import legacy, validate


def test_validate_original_has_zero_differences(legacy_csv):
    report = validate.validate_roundtrip(legacy_csv)
    assert report.ok
    assert report.strict_diffs == []
    assert report.bytes_identical
    assert len(report.recomputed_diffs) == 20


def test_validate_detects_mutated_cell(legacy_csv, tmp_path):
    rows = legacy.read_rows(legacy_csv)
    # Presenças do jogador da linha 6 (RODRIGO) alterada: dado derivado deixa de bater
    rows[5][6] = str(int(rows[5][6]) + 5)
    mutated = tmp_path / "mutated.csv"
    mutated.write_bytes(legacy.write_rows(rows))

    report = validate.validate_roundtrip(mutated)
    assert not report.ok
    assert not report.bytes_identical
    assert [(d.row, d.col) for d in report.strict_diffs] == [(5, 6)]
    assert report.strict_diffs[0].expected == rows[5][6]


def test_validate_detects_mutated_venue_label(legacy_csv, tmp_path):
    rows = legacy.read_rows(legacy_csv)
    rows[1][6] = "Ordem?"  # rótulo fixo alterado: exportador não reproduz
    mutated = tmp_path / "mutated.csv"
    mutated.write_bytes(legacy.write_rows(rows))
    report = validate.validate_roundtrip(mutated)
    assert [(d.row, d.col) for d in report.strict_diffs] == [(1, 6)]


def test_diff_rows_handles_shape_mismatch():
    diffs = validate.diff_rows([["a", "b"]], [["a"], ["c"]])
    assert {(d.row, d.col) for d in diffs} == {(0, 1), (1, 0)}


def test_cli_validate_legacy(legacy_csv):
    proc = subprocess.run(
        [sys.executable, "-m", "pdq", "validate-legacy", str(legacy_csv)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "diferenças (modo estrito): 0" in proc.stdout
    assert "bytes idênticos (modo estrito): sim" in proc.stdout
