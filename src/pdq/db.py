"""Acesso ao banco SQLite do Pdq.

O banco guarda apenas dados brutos (jogadores, sessões e presenças).
Colunas derivadas da planilha (Faltas, Presenças) são recalculadas na exportação.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "pdq.db"

STATUS_PRESENT = "X"
STATUS_ABSENT = "F"
STATUS_NONE = "-"
STATUSES = (STATUS_PRESENT, STATUS_ABSENT, STATUS_NONE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id       INTEGER PRIMARY KEY,
    pos      INTEGER NOT NULL UNIQUE,      -- ordem da linha na planilha (POS)
    classe   TEXT    NOT NULL DEFAULT '',  -- CLASSE (M, F, -, '')
    posicao  TEXT    NOT NULL DEFAULT '',  -- POSICAO (L, G, '')
    legacy_id TEXT   NOT NULL DEFAULT '',  -- ID da planilha (não único)
    name     TEXT    NOT NULL              -- JOGADORES (preservado byte a byte)
);

CREATE TABLE IF NOT EXISTS session (
    id     INTEGER PRIMARY KEY,
    ordem  INTEGER NOT NULL UNIQUE,        -- coluna na planilha: 1 = mais recente
    date   TEXT    NOT NULL UNIQUE,        -- ISO 8601 (YYYY-MM-DD)
    venue  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS attendance (
    player_id  INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN ('X', 'F', '-')),
    PRIMARY KEY (player_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance(session_id);
"""


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Abre (criando se preciso) o banco e garante o schema."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Cria as tabelas. Idempotente."""
    conn.executescript(SCHEMA)
    conn.commit()


def clear_all(conn: sqlite3.Connection) -> None:
    """Remove todos os dados (usado por importações completas)."""
    conn.execute("DELETE FROM attendance")
    conn.execute("DELETE FROM session")
    conn.execute("DELETE FROM player")
    conn.commit()
