"""Importa a planilha legada para o banco SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pdq import db, legacy


@dataclass
class ImportResult:
    players: int
    sessions: int
    attendance: int


def import_legacy_csv(conn: sqlite3.Connection, csv_path: str | Path) -> ImportResult:
    """Substitui todo o conteúdo do banco pelo da planilha (importação completa)."""
    sheet = legacy.parse_sheet(legacy.read_rows(csv_path))
    return import_sheet(conn, sheet)


def import_sheet(conn: sqlite3.Connection, sheet: legacy.LegacySheet) -> ImportResult:
    db.clear_all(conn)
    with conn:
        session_ids = {}
        for s in sheet.sessions:
            cur = conn.execute(
                "INSERT INTO session (ordem, date, venue) VALUES (?, ?, ?)",
                (s.ordem, s.date.isoformat(), s.venue),
            )
            session_ids[s.ordem] = cur.lastrowid

        rows = []
        for p in sheet.players:
            cur = conn.execute(
                "INSERT INTO player (pos, classe, posicao, legacy_id, name) VALUES (?, ?, ?, ?, ?)",
                (p.pos, p.classe, p.posicao, p.legacy_id, p.name),
            )
            pid = cur.lastrowid
            for s, cell in zip(sheet.sessions, p.cells, strict=True):
                rows.append((pid, session_ids[s.ordem], cell))
        conn.executemany(
            "INSERT INTO attendance (player_id, session_id, status) VALUES (?, ?, ?)", rows
        )
    return ImportResult(
        players=len(sheet.players), sessions=len(sheet.sessions), attendance=len(rows)
    )
