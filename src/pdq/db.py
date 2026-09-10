"""Acesso ao banco SQLite do Pdq.

O banco guarda apenas dados brutos (jogadores, sessões e presenças).
Colunas derivadas da planilha (Faltas, Presenças) são recalculadas na exportação.

Versões do schema (PRAGMA user_version):

- 1: fundação E0 (player, session, attendance com X/F/-).
- 2: registro pós-jogo E1 (status J, attendance.note, player.padrinho,
     player_alias, match_meta). Migração aditiva e idempotente.
- 3: correção de partida E2 (attendance.section: seção da lista em que o
     jogador estava). Aditiva; linhas antigas ficam com seção vazia.
- 4: financeiro E4 (tabela payment) e ciclo de vida E5 (classe C,
     player.guest_status e guest_decision_date). Aditiva e idempotente; a
     guarda de migração verifica as duas épicas, pois foram desenvolvidas em
     paralelo sob o mesmo número.
- 5: identidade canônica (player.padrinho_id e player.canonical_player_id).
     Referências canônicas preservam os registros originais de player e
     attendance.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "pdq.db"

STATUS_PRESENT = "X"
STATUS_ABSENT = "F"  # furo: confirmou e não foi
STATUS_NONE = "-"
STATUS_PLAYED = "J"  # jogou: estava na reserva e entrou
STATUSES = (STATUS_PRESENT, STATUS_ABSENT, STATUS_NONE, STATUS_PLAYED)
# Na planilha legada só existem X/F/-; "J" conta como presença ao exportar.
LEGACY_STATUS = {STATUS_PLAYED: STATUS_PRESENT}

SECTION_GOALKEEPERS = "goleiros"
SECTION_FIELD = "linha"
SECTION_RESERVES = "reservas"
SECTIONS = (SECTION_GOALKEEPERS, SECTION_FIELD, SECTION_RESERVES)

SCHEMA_VERSION = 5

CLASS_GUEST = "C"
CLASS_FREQUENT = "F"
CLASS_MONTHLY = "M"
CLASS_INACTIVE = "-"
GUEST_PENDING = "pending"
GUEST_PROMOTED = "promoted"
GUEST_DECLINED_STAYS = "declined_stays"
GUEST_DECLINED_LEAVES = "declined_leaves"
GUEST_STATUSES = (
    GUEST_PENDING,
    GUEST_PROMOTED,
    GUEST_DECLINED_STAYS,
    GUEST_DECLINED_LEAVES,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id       INTEGER PRIMARY KEY,
    pos      INTEGER NOT NULL UNIQUE,      -- ordem da linha na planilha (POS)
    classe   TEXT    NOT NULL DEFAULT '',  -- CLASSE (M, F, -, '')
    posicao  TEXT    NOT NULL DEFAULT '',  -- POSICAO (L, G, '')
    legacy_id TEXT   NOT NULL DEFAULT '',  -- ID da planilha (não único)
    name     TEXT    NOT NULL,             -- JOGADORES (preservado byte a byte)
    padrinho TEXT    NOT NULL DEFAULT '',  -- texto histórico de quem apresentou
    padrinho_id INTEGER REFERENCES player(id) ON DELETE SET NULL,
    canonical_player_id INTEGER REFERENCES player(id) ON DELETE RESTRICT,
    guest_status TEXT NOT NULL DEFAULT '' CHECK (guest_status IN
        ('', 'pending', 'promoted', 'declined_stays', 'declined_leaves')),
    guest_decision_date TEXT NOT NULL DEFAULT ''
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
    status     TEXT NOT NULL CHECK (status IN ('X', 'F', '-', 'J')),
    note       TEXT NOT NULL DEFAULT '',   -- observação da linha da lista
    section    TEXT NOT NULL DEFAULT '',   -- goleiros | linha | reservas | '' (legado)
    PRIMARY KEY (player_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance(session_id);

CREATE TABLE IF NOT EXISTS player_alias (
    alias     TEXT    PRIMARY KEY,         -- normalizado (ver pdq.aliases.normalize)
    player_id INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_player_alias_player ON player_alias(player_id);

CREATE TABLE IF NOT EXISTS match_meta (
    session_id   INTEGER PRIMARY KEY REFERENCES session(id) ON DELETE CASCADE,
    vagas_vazias INTEGER NOT NULL DEFAULT 0,
    observacao   TEXT    NOT NULL DEFAULT '',
    raw_list     TEXT    NOT NULL DEFAULT ''  -- texto colado do WhatsApp
);

CREATE TABLE IF NOT EXISTS payment (
    id           INTEGER PRIMARY KEY,
    player_id    INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),  -- valor em centavos
    paid_on      TEXT    NOT NULL,             -- ISO 8601 (YYYY-MM-DD)
    ref          TEXT    NOT NULL DEFAULT '',  -- a que se refere: AAAA-MM-DD, AAAA-MM ou ''
    note         TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_payment_player ON payment(player_id);
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
    """Cria as tabelas e aplica migrações pendentes. Idempotente."""
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else ""


def migrate(conn: sqlite3.Connection) -> None:
    """Leva bancos de versões anteriores ao schema atual sem perder dados."""
    if (
        schema_version(conn) >= SCHEMA_VERSION
        and "'J'" in _table_sql(conn, "attendance")
        and "section" in _columns(conn, "attendance")
        and _table_sql(conn, "payment")
        and {"guest_status", "guest_decision_date"} <= _columns(conn, "player")
        and "padrinho_id" in _columns(conn, "player")
        and "canonical_player_id" in _columns(conn, "player")
    ):
        return

    # E4: a tabela payment já foi criada por SCHEMA (CREATE TABLE IF NOT EXISTS).

    if "padrinho" not in _columns(conn, "player"):
        conn.execute("ALTER TABLE player ADD COLUMN padrinho TEXT NOT NULL DEFAULT ''")
    if "padrinho_id" not in _columns(conn, "player"):
        conn.execute(
            "ALTER TABLE player ADD COLUMN padrinho_id INTEGER "
            "REFERENCES player(id) ON DELETE SET NULL"
        )
    if "canonical_player_id" not in _columns(conn, "player"):
        conn.execute(
            "ALTER TABLE player ADD COLUMN canonical_player_id "
            "INTEGER REFERENCES player(id) ON DELETE RESTRICT"
        )

    if "'J'" not in _table_sql(conn, "attendance") or "note" not in _columns(conn, "attendance"):
        # SQLite não altera CHECK: recria a tabela copiando os dados.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            CREATE TABLE attendance_new (
                player_id  INTEGER NOT NULL REFERENCES player(id) ON DELETE CASCADE,
                session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
                status     TEXT NOT NULL CHECK (status IN ('X', 'F', '-', 'J')),
                note       TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (player_id, session_id)
            );
            INSERT INTO attendance_new (player_id, session_id, status)
                SELECT player_id, session_id, status FROM attendance;
            DROP TABLE attendance;
            ALTER TABLE attendance_new RENAME TO attendance;
            CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance(session_id);
            """
        )
        conn.execute("PRAGMA foreign_keys = ON")

    if "section" not in _columns(conn, "attendance"):
        conn.execute("ALTER TABLE attendance ADD COLUMN section TEXT NOT NULL DEFAULT ''")

    if "guest_status" not in _columns(conn, "player"):
        conn.execute(
            "ALTER TABLE player ADD COLUMN guest_status TEXT NOT NULL DEFAULT '' "
            "CHECK (guest_status IN ('', 'pending', 'promoted', 'declined_stays', "
            "'declined_leaves'))"
        )
    if "guest_decision_date" not in _columns(conn, "player"):
        conn.execute("ALTER TABLE player ADD COLUMN guest_decision_date TEXT NOT NULL DEFAULT ''")

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def canonical_player_id(conn: sqlite3.Connection, player_id: int) -> int | None:
    """Retorna a raiz canônica, ou None para ciclos e referências inválidas."""
    seen: set[int] = set()
    current = player_id
    while current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT canonical_player_id FROM player WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            return None
        if row["canonical_player_id"] is None:
            return current
        current = row["canonical_player_id"]
    return None


def clear_all(conn: sqlite3.Connection) -> None:
    """Remove todos os dados (usado por importações completas)."""
    conn.execute("DELETE FROM payment")
    conn.execute("DELETE FROM match_meta")
    conn.execute("DELETE FROM player_alias")
    conn.execute("DELETE FROM attendance")
    conn.execute("DELETE FROM session")
    conn.execute("DELETE FROM player")
    conn.commit()


def renumber_sessions(conn: sqlite3.Connection) -> None:
    """Recalcula `ordem` (1 = data mais recente) após inserir uma sessão."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM session ORDER BY date DESC, id DESC")]
    # UNIQUE(ordem) impede renumerar em um único UPDATE; passa pelo negativo.
    conn.execute("UPDATE session SET ordem = -ordem")
    conn.executemany(
        "UPDATE session SET ordem = ? WHERE id = ?", [(i, sid) for i, sid in enumerate(ids, 1)]
    )
