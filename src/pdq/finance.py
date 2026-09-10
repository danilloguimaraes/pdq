"""Financeiro: cobranças derivadas de presença + classe e pagamentos registrados.

Regras (ver docs/adr/0001-diaria-do-convidado.md):

- Diária (R$ 15) por partida para quem jogou (status X ou J) sem ser mensalista:
  frequentes (classe F) e convidados (classe C, ou '-'/vazia herdada da planilha).
  A diária do convidado é devida pelo próprio convidado; o padrinho sai só como
  referência.
- Mensalidade por mês com partida para mensalistas (classe M), a partir do mês do
  primeiro registro (X/F/J) do jogador. Mensalista sem registro não é cobrado.
- Cobranças não são armazenadas: são recalculadas a partir do banco. Só os
  pagamentos (tabela payment) são gravados.
- Saldo = cobrado − pago; positivo é pendência, negativo é crédito.

Valores em centavos (int) para evitar arredondamento.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from pdq import aliases, db

DIARIA_CENTAVOS = 1500
MENSALIDADE_CENTAVOS = 6000  # padrão; ajustável na CLI (--mensalidade)

CLASSE_MENSALISTA = db.CLASS_MONTHLY
CLASSE_FREQUENTE = db.CLASS_FREQUENT
CLASSES_CONVIDADO = (db.CLASS_GUEST, db.CLASS_INACTIVE, "")

KIND_DIARIA = "diaria"
KIND_MENSALIDADE = "mensalidade"

CHARGEABLE_STATUSES = (db.STATUS_PRESENT, db.STATUS_PLAYED)
RECORDED_STATUSES = (db.STATUS_PRESENT, db.STATUS_ABSENT, db.STATUS_PLAYED)


class FinanceError(ValueError):
    """Entrada inválida (jogador, valor ou data); nada foi gravado."""


def is_mensalista(classe: str) -> bool:
    return classe == CLASSE_MENSALISTA


def is_frequente(classe: str) -> bool:
    return classe == CLASSE_FREQUENTE


def is_convidado(classe: str) -> bool:
    return classe in CLASSES_CONVIDADO


def classe_label(classe: str) -> str:
    if is_mensalista(classe):
        return "mensalista"
    if is_frequente(classe):
        return "frequente"
    return "convidado"


@dataclass(frozen=True)
class Charge:
    player_id: int
    player_name: str
    classe: str
    kind: str  # diaria | mensalidade
    ref: str  # AAAA-MM-DD (diária) ou AAAA-MM (mensalidade)
    amount_cents: int
    padrinho: str = ""  # referência de cobrança do convidado (ADR 0001)


@dataclass(frozen=True)
class Payment:
    id: int
    player_id: int
    player_name: str
    amount_cents: int
    paid_on: str
    ref: str
    note: str


@dataclass(frozen=True)
class Balance:
    player_id: int
    player_name: str
    classe: str
    charged_cents: int
    paid_cents: int

    @property
    def saldo_cents(self) -> int:
        """Positivo: pendência. Negativo: crédito."""
        return self.charged_cents - self.paid_cents


# --- valores --------------------------------------------------------------------


def fmt_brl(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}R$ {cents // 100},{cents % 100:02d}"


def parse_brl(text: str) -> int:
    """'15', '15,50', 'R$ 15.50' -> centavos. Recusa zero e negativos."""
    raw = text.strip().upper().replace("R$", "").replace(" ", "").replace(",", ".")
    try:
        whole, sep, frac = raw.partition(".")
        if not whole.isdigit() or (sep and not frac.isdigit()) or len(frac) > 2:
            raise ValueError
        cents = int(whole) * 100 + (int(frac.ljust(2, "0")) if frac else 0)
    except ValueError:
        raise FinanceError(f"valor inválido: {text!r} (use 15 ou 15,50)") from None
    if cents <= 0:
        raise FinanceError("valor precisa ser positivo")
    return cents


def _check_iso_date(text: str, what: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise FinanceError(f"{what} inválida: {text!r} (use AAAA-MM-DD)") from None


def _check_month(text: str) -> str:
    if len(text) == 7 and text[4] == "-":
        _check_iso_date(text + "-01", "mês")
        return text
    raise FinanceError(f"mês inválido: {text!r} (use AAAA-MM)")


# --- cobranças ------------------------------------------------------------------


def charges_for_session(conn: sqlite3.Connection, session_date: str) -> list[Charge]:
    """Diárias devidas na partida: frequentes e convidados com status X ou J."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.classe, p.padrinho
          FROM attendance a
          JOIN player p ON p.id = a.player_id
          JOIN session s ON s.id = a.session_id
         WHERE s.date = ? AND a.status IN ('X', 'J') AND p.classe <> 'M'
         ORDER BY p.pos
        """,
        (session_date,),
    ).fetchall()
    charges = []
    charged_ids = set()
    for row in rows:
        canonical_id = db.canonical_player_id(conn, row["id"])
        if canonical_id is None or canonical_id in charged_ids:
            continue
        player = conn.execute(
            "SELECT id, name, classe, padrinho FROM player WHERE id = ?", (canonical_id,)
        ).fetchone()
        if player["classe"] == CLASSE_MENSALISTA:
            continue
        charged_ids.add(canonical_id)
        charges.append(
            Charge(
                player["id"],
                player["name"],
                player["classe"],
                KIND_DIARIA,
                session_date,
                DIARIA_CENTAVOS,
                player["padrinho"] if is_convidado(player["classe"]) else "",
            )
        )
    return charges


def _months_with_sessions(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0] for r in conn.execute("SELECT DISTINCT substr(date, 1, 7) FROM session ORDER BY 1")
    ]


def _mensalistas_first_month(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Mensalistas com o mês (AAAA-MM) do primeiro registro X/F/J."""
    first_months = {}
    for row in conn.execute(
        """
        SELECT a.player_id, MIN(substr(s.date, 1, 7)) AS first_month
          FROM attendance a JOIN session s ON s.id = a.session_id
         WHERE a.status IN ('X', 'F', 'J') GROUP BY a.player_id
        """
    ):
        canonical_id = db.canonical_player_id(conn, row["player_id"])
        if canonical_id is not None:
            first_months[canonical_id] = min(
                first_months.get(canonical_id, row["first_month"]), row["first_month"]
            )
    return [
        (player, first_months[player["id"]])
        for player in conn.execute(
            "SELECT id, name, classe FROM player WHERE classe = 'M' ORDER BY pos"
        )
        if player["id"] in first_months
    ]


def charges_for_month(
    conn: sqlite3.Connection, month: str, mensalidade_cents: int = MENSALIDADE_CENTAVOS
) -> list[Charge]:
    """Mensalidades devidas no mês (AAAA-MM), se houve partida nele."""
    month = _check_month(month)
    if month not in _months_with_sessions(conn):
        return []
    return [
        Charge(
            player["id"],
            player["name"],
            player["classe"],
            KIND_MENSALIDADE,
            month,
            mensalidade_cents,
        )
        for player, first_month in _mensalistas_first_month(conn)
        if first_month <= month
    ]


def all_charges(
    conn: sqlite3.Connection,
    mensalidade_cents: int = MENSALIDADE_CENTAVOS,
    since: str | None = None,
) -> list[Charge]:
    """Todas as cobranças do histórico: diárias por sessão e mensalidades por mês.

    `since` (AAAA-MM) ignora o que veio antes: início da contabilidade.
    """
    since = _check_month(since) if since else ""
    charges: list[Charge] = []
    for r in conn.execute("SELECT date FROM session WHERE date >= ? ORDER BY date", (since,)):
        charges.extend(charges_for_session(conn, r["date"]))
    mensalistas = _mensalistas_first_month(conn)
    for month in _months_with_sessions(conn):
        if month < since:
            continue
        charges.extend(
            Charge(
                player["id"],
                player["name"],
                player["classe"],
                KIND_MENSALIDADE,
                month,
                mensalidade_cents,
            )
            for player, first_month in mensalistas
            if first_month <= month
        )
    return charges


def latest_session_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT date FROM session ORDER BY date DESC LIMIT 1").fetchone()
    return row[0] if row else None


# --- jogadores ------------------------------------------------------------------


def find_player(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    """Localiza jogador por id, nome exato (sem acento/caixa) ou alias aprendido."""
    if key.isdigit():
        row = conn.execute("SELECT * FROM player WHERE id = ?", (int(key),)).fetchone()
        if row is None:
            raise FinanceError(f"jogador id {key} não existe")
        canonical_id = db.canonical_player_id(conn, row["id"])
        return conn.execute("SELECT * FROM player WHERE id = ?", (canonical_id,)).fetchone()
    norm = aliases.normalize(key)
    row = conn.execute(
        "SELECT p.* FROM player_alias a JOIN player p ON p.id = a.player_id WHERE a.alias = ?",
        (norm,),
    ).fetchone()
    if row is not None:
        canonical_id = db.canonical_player_id(conn, row["id"])
        return conn.execute("SELECT * FROM player WHERE id = ?", (canonical_id,)).fetchone()
    matches = [
        p for p in conn.execute("SELECT * FROM player") if aliases.normalize(p["name"]) == norm
    ]
    if len(matches) == 1:
        canonical_id = db.canonical_player_id(conn, matches[0]["id"])
        return conn.execute("SELECT * FROM player WHERE id = ?", (canonical_id,)).fetchone()
    if not matches:
        raise FinanceError(f"jogador não encontrado: {key!r} (use o id ou o nome da planilha)")
    ids = ", ".join(str(p["id"]) for p in matches)
    raise FinanceError(f"nome ambíguo: {key!r} (ids {ids}); use o id")


# --- pagamentos -----------------------------------------------------------------


def record_payment(
    conn: sqlite3.Connection,
    player_id: int,
    amount_cents: int,
    paid_on: str,
    ref: str = "",
    note: str = "",
) -> Payment:
    if amount_cents <= 0:
        raise FinanceError("valor precisa ser positivo")
    paid_on = _check_iso_date(paid_on, "data do pagamento")
    if ref:
        ref = _check_month(ref) if len(ref) == 7 else _check_iso_date(ref, "referência")
    player = conn.execute("SELECT name FROM player WHERE id = ?", (player_id,)).fetchone()
    if player is None:
        raise FinanceError(f"jogador id {player_id} não existe")
    cur = conn.execute(
        "INSERT INTO payment (player_id, amount_cents, paid_on, ref, note) VALUES (?, ?, ?, ?, ?)",
        (player_id, amount_cents, paid_on, ref, note),
    )
    conn.commit()
    return Payment(cur.lastrowid, player_id, player["name"], amount_cents, paid_on, ref, note)


def payments(conn: sqlite3.Connection, player_id: int | None = None) -> list[Payment]:
    sql = (
        "SELECT y.id, y.player_id, p.name, y.amount_cents, y.paid_on, y.ref, y.note "
        "FROM payment y JOIN player p ON p.id = y.player_id"
    )
    params: tuple = ()
    if player_id is not None:
        sql += " WHERE y.player_id = ?"
        params = (player_id,)
    sql += " ORDER BY y.paid_on, y.id"
    return [Payment(*r) for r in conn.execute(sql, params)]


# --- saldo ----------------------------------------------------------------------


def balances(
    conn: sqlite3.Connection,
    mensalidade_cents: int = MENSALIDADE_CENTAVOS,
    only_pending: bool = False,
    since: str | None = None,
) -> list[Balance]:
    """Saldo por jogador (cobrado − pago), na ordem da planilha."""
    charged: dict[int, int] = {}
    for c in all_charges(conn, mensalidade_cents, since):
        charged[c.player_id] = charged.get(c.player_id, 0) + c.amount_cents
    paid: dict[int, int] = {
        r[0]: r[1]
        for r in conn.execute("SELECT player_id, SUM(amount_cents) FROM payment GROUP BY player_id")
    }
    out = []
    for p in conn.execute("SELECT id, name, classe FROM player ORDER BY pos"):
        b = Balance(p["id"], p["name"], p["classe"], charged.get(p["id"], 0), paid.get(p["id"], 0))
        if b.charged_cents == 0 and b.paid_cents == 0:
            continue
        if only_pending and b.saldo_cents <= 0:
            continue
        out.append(b)
    return out


def balance_of(
    conn: sqlite3.Connection,
    player_id: int,
    mensalidade_cents: int = MENSALIDADE_CENTAVOS,
    since: str | None = None,
) -> Balance:
    for b in balances(conn, mensalidade_cents, since=since):
        if b.player_id == player_id:
            return b
    p = conn.execute("SELECT id, name, classe FROM player WHERE id = ?", (player_id,)).fetchone()
    if p is None:
        raise FinanceError(f"jogador id {player_id} não existe")
    return Balance(p["id"], p["name"], p["classe"], 0, 0)
