"""Avaliação e decisão sobre convidados após quatro presenças."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from pdq import aliases, db


class GuestLifecycleError(ValueError):
    """Operação inválida no ciclo de vida do convidado; nada foi alterado."""


@dataclass(frozen=True)
class GuestCandidate:
    player_id: int
    name: str
    padrinho: str
    presences: int
    reached_on: str


@dataclass(frozen=True)
class GuestDecision:
    player_id: int
    name: str
    classe: str
    guest_status: str
    decision_date: str


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as e:
        raise GuestLifecycleError(f"data inválida {value!r}: use AAAA-MM-DD") from e


def _player(conn: sqlite3.Connection, ref: int | str) -> sqlite3.Row:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        row = conn.execute("SELECT * FROM player WHERE id = ?", (int(ref),)).fetchone()
        if row is None:
            raise GuestLifecycleError(f"player_id {int(ref)} não existe")
        return row
    text = str(ref).strip()
    if not text:
        raise GuestLifecycleError("jogador vazio")
    key = aliases.normalize(text)
    rows = [
        row
        for row in conn.execute("SELECT * FROM player ORDER BY id")
        if aliases.normalize(row["name"]) == key
    ]
    if not rows:
        row = conn.execute(
            "SELECT p.* FROM player_alias a JOIN player p ON p.id = a.player_id WHERE a.alias = ?",
            (key,),
        ).fetchone()
        if row is not None:
            return row
        raise GuestLifecycleError(f"jogador {text!r} não encontrado (use o id ou o nome exato)")
    if len(rows) > 1:
        options = ", ".join(f"{row['name']} [{row['id']}]" for row in rows)
        raise GuestLifecycleError(f"jogador {text!r} ambíguo: {options}")
    return rows[0]


def pending_guests(conn: sqlite3.Connection) -> list[GuestCandidate]:
    """Convidados com ao menos quatro presenças, na ordem em que atingiram o limite."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.padrinho, s.date
        FROM player p
        JOIN attendance a ON a.player_id = p.id AND a.status IN ('X', 'J')
        JOIN session s ON s.id = a.session_id
        WHERE p.classe = ? AND p.guest_status = ?
        ORDER BY p.id, s.date, s.id
        """,
        (db.CLASS_GUEST, db.GUEST_PENDING),
    ).fetchall()
    candidates: list[GuestCandidate] = []
    current_id = None
    player_rows: list[sqlite3.Row] = []
    for row in rows + [None]:
        if row is None or row["id"] != current_id:
            if len(player_rows) >= 4:
                p = player_rows[0]
                candidates.append(
                    GuestCandidate(
                        p["id"], p["name"], p["padrinho"], len(player_rows), player_rows[3]["date"]
                    )
                )
            player_rows = []
            current_id = row["id"] if row is not None else None
        if row is not None:
            player_rows.append(row)
    return sorted(candidates, key=lambda c: (c.reached_on, c.name, c.player_id))


def _eligible(conn: sqlite3.Connection, player: sqlite3.Row) -> GuestCandidate:
    for candidate in pending_guests(conn):
        if candidate.player_id == player["id"]:
            return candidate
    raise GuestLifecycleError(f"{player['name']!r} não é convidado pendente com 4 presenças")


def promote(
    conn: sqlite3.Connection, player_ref: int | str, classe: str, decision_date: str
) -> GuestDecision:
    """Promove um convidado elegível a frequente (F) ou mensalista (M)."""
    if classe not in (db.CLASS_FREQUENT, db.CLASS_MONTHLY):
        raise GuestLifecycleError(f"classe {classe!r} não é F ou M")
    decision_date = _date(decision_date)
    player = _player(conn, player_ref)
    _eligible(conn, player)
    with conn:
        conn.execute(
            "UPDATE player SET classe = ?, guest_status = ?, guest_decision_date = ? WHERE id = ?",
            (classe, db.GUEST_PROMOTED, decision_date, player["id"]),
        )
    return GuestDecision(player["id"], player["name"], classe, db.GUEST_PROMOTED, decision_date)


def decline(
    conn: sqlite3.Connection, player_ref: int | str, keeps_guest: bool, decision_date: str
) -> GuestDecision:
    """Registra recusa, mantendo o convidado ou marcando sua saída sem apagar histórico."""
    decision_date = _date(decision_date)
    player = _player(conn, player_ref)
    _eligible(conn, player)
    status = db.GUEST_DECLINED_STAYS if keeps_guest else db.GUEST_DECLINED_LEAVES
    classe = db.CLASS_GUEST if keeps_guest else db.CLASS_INACTIVE
    with conn:
        conn.execute(
            "UPDATE player SET classe = ?, guest_status = ?, guest_decision_date = ? WHERE id = ?",
            (classe, status, decision_date, player["id"]),
        )
    return GuestDecision(player["id"], player["name"], classe, status, decision_date)


def render_pending(candidates: list[GuestCandidate]) -> str:
    if not candidates:
        return "nenhum convidado aguarda decisão"
    lines = ["Convidados que aguardam decisão:"]
    for candidate in candidates:
        padrinho = f"; padrinho: {candidate.padrinho}" if candidate.padrinho else ""
        lines.append(
            f"  {candidate.name} [{candidate.player_id}]: {candidate.presences} presenças "
            f"(4ª em {candidate.reached_on}){padrinho}"
        )
    return "\n".join(lines)
