"""Validação célula a célula da exportação contra a planilha original."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pdq import db, exporter, importer, legacy


@dataclass(frozen=True)
class CellDiff:
    row: int
    col: int
    expected: str
    actual: str

    def describe(self) -> str:
        return (
            f"linha {self.row + 1}, coluna {self.col + 1}: "
            f"esperado {self.expected!r}, obtido {self.actual!r}"
        )


def diff_rows(expected: list[list[str]], actual: list[list[str]]) -> list[CellDiff]:
    diffs: list[CellDiff] = []
    height = max(len(expected), len(actual))
    for i in range(height):
        e_row = expected[i] if i < len(expected) else []
        a_row = actual[i] if i < len(actual) else []
        width = max(len(e_row), len(a_row))
        for j in range(width):
            e = e_row[j] if j < len(e_row) else "<ausente>"
            a = a_row[j] if j < len(a_row) else "<ausente>"
            if e != a:
                diffs.append(CellDiff(i, j, e, a))
    return diffs


@dataclass
class ValidationReport:
    strict_diffs: list[CellDiff]
    recomputed_diffs: list[CellDiff]
    bytes_identical: bool

    @property
    def ok(self) -> bool:
        return self.bytes_identical and not self.strict_diffs


def validate_roundtrip(csv_path: str | Path) -> ValidationReport:
    """Importa o CSV em memória, exporta e compara com o original.

    - strict_diffs: exportação com a inconsistência legada reproduzida; deve ser vazia.
    - recomputed_diffs: exportação com Presenças recalculadas; isola as células
      afetadas pela fórmula desatualizada da planilha.
    """
    original_bytes = Path(csv_path).read_bytes()
    original_rows = legacy.read_rows(original_bytes)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    importer.import_legacy_csv(conn, csv_path)

    strict_bytes = exporter.export_legacy_csv(conn, strict_legacy_quirk=True)
    strict_rows = legacy.read_rows(strict_bytes)
    recomputed_rows = exporter.build_rows(conn, strict_legacy_quirk=False)

    return ValidationReport(
        strict_diffs=diff_rows(original_rows, strict_rows),
        recomputed_diffs=diff_rows(original_rows, recomputed_rows),
        bytes_identical=strict_bytes == original_bytes,
    )
