"""Regenera a planilha legada a partir do banco."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from pdq import db, legacy
from pdq.legacy import (
    COLUMN_HEADER,
    FIXED_COLUMNS,
    PRESENCAS_HEADER_LABEL,
    STALE_PRESENCAS_EXCLUDED_DATE,
)


def build_rows(conn: sqlite3.Connection, *, strict_legacy_quirk: bool = False) -> list[list[str]]:
    """Monta a matriz de células no layout da planilha.

    strict_legacy_quirk: reproduz a fórmula desatualizada de Presenças por
    jogador (exclui a sessão de 21/08/2025), gerando bytes idênticos ao original.
    """
    sessions = conn.execute("SELECT id, ordem, date, venue FROM session ORDER BY ordem").fetchall()
    players = conn.execute(
        "SELECT id, pos, classe, posicao, legacy_id, name FROM player ORDER BY pos"
    ).fetchall()
    att = {
        (r["player_id"], r["session_id"]): r["status"]
        for r in conn.execute("SELECT player_id, session_id, status FROM attendance")
    }

    excluded_ids = set()
    if strict_legacy_quirk:
        excluded_ids = {
            s["id"]
            for s in sessions
            if date.fromisoformat(s["date"]) == STALE_PRESENCAS_EXCLUDED_DATE
        }

    pad = [""] * (FIXED_COLUMNS - 1)
    body = []
    session_f = [0] * len(sessions)
    session_x = [0] * len(sessions)
    for p in players:
        cells = []
        faltas = presencas = 0
        for j, s in enumerate(sessions):
            status = att.get((p["id"], s["id"]), "-")
            status = db.LEGACY_STATUS.get(status, status)  # "J" (jogou) vira "X"
            cells.append(status)
            if status == "F":
                faltas += 1
                session_f[j] += 1
            elif status == "X":
                session_x[j] += 1
                if s["id"] not in excluded_ids:
                    presencas += 1
        body.append(
            [
                str(p["pos"]),
                p["classe"],
                p["posicao"],
                p["legacy_id"],
                p["name"],
                str(faltas),
                str(presencas),
                *cells,
            ]
        )

    rows = [
        ["", " ", *pad[:-1], *(s["venue"] for s in sessions)],
        [*pad, "Ordem", *(str(s["ordem"]) for s in sessions)],
        [*pad, "Faltas", *(str(n) for n in session_f)],
        [*pad, "Presencas", *(str(n) for n in session_x)],
        [
            *COLUMN_HEADER,
            PRESENCAS_HEADER_LABEL,
            *(legacy.format_date(date.fromisoformat(s["date"])) for s in sessions),
        ],
        *body,
    ]
    return rows


def export_legacy_csv(
    conn: sqlite3.Connection,
    out_path: str | Path | None = None,
    *,
    strict_legacy_quirk: bool = False,
) -> bytes:
    data = legacy.write_rows(build_rows(conn, strict_legacy_quirk=strict_legacy_quirk))
    if out_path is not None:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(data)
    return data
