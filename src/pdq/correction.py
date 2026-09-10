"""Correção de partida: ajustes numa sessão já confirmada, sem SQL manual.

Toda operação localiza a sessão pela data (ISO 8601), valida os argumentos e
grava numa única transação. Erros levantam `CorrectionError` e nada é alterado.

Operações:

- `relink`: troca o jogador vinculado a uma presença (linha da lista apontava
  para a pessoa errada), opcionalmente aprendendo o alias correto.
- `set_status`: alterna X (presença), F (furo), J (jogou) e `-` (estava na
  lista e não jogou; a linha permanece, com sua seção e observação).
- `set_section`: corrige a seção da lista (goleiros / linha / reservas).
- `set_date` / `set_venue`: corrigem data (renumerando `ordem`) e local.
- `delete_session`: exclui a sessão e tudo que depende dela; exige
  `confirm=True` explícito.

O export legado não muda de layout: `section` não é exportada e `-` já era
"sem registro" na planilha.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from pdq import aliases, db


class CorrectionError(ValueError):
    """Correção inválida; nada foi gravado."""


# --- localização ----------------------------------------------------------------


def _iso(value: str, label: str = "data") -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as e:
        raise CorrectionError(f"{label} inválida {value!r}: use AAAA-MM-DD") from e


def find_session(conn: sqlite3.Connection, date_iso: str) -> sqlite3.Row:
    """Sessão pela data. Levanta CorrectionError se não existir."""
    d = _iso(date_iso)
    row = conn.execute("SELECT id, ordem, date, venue FROM session WHERE date = ?", (d,)).fetchone()
    if row is None:
        raise CorrectionError(f"não existe sessão em {d}")
    return row


def find_player(conn: sqlite3.Connection, ref: int | str) -> sqlite3.Row:
    """Jogador por id, nome exato ou alias aprendido. Ambiguidade é erro."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        row = conn.execute("SELECT id, name FROM player WHERE id = ?", (int(ref),)).fetchone()
        if row is None:
            raise CorrectionError(f"player_id {int(ref)} não existe")
        return row
    text = str(ref).strip()
    if not text:
        raise CorrectionError("jogador vazio")
    key = aliases.normalize(text)
    rows = [
        r
        for r in conn.execute("SELECT id, name FROM player ORDER BY id")
        if aliases.normalize(r["name"]) == key
    ]
    if not rows:
        alias = conn.execute(
            "SELECT player_id FROM player_alias WHERE alias = ?", (key,)
        ).fetchone()
        if alias is not None:
            return find_player(conn, alias["player_id"])
        raise CorrectionError(f"jogador {text!r} não encontrado (use o id ou o nome exato)")
    if len(rows) > 1:
        opts = ", ".join(f"{r['name']} [{r['id']}]" for r in rows)
        raise CorrectionError(f"jogador {text!r} ambíguo: {opts}")
    return rows[0]


def _attendance(conn: sqlite3.Connection, session_id: int, player_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT player_id, session_id, status, note, section FROM attendance "
        "WHERE session_id = ? AND player_id = ?",
        (session_id, player_id),
    ).fetchone()
    if row is None:
        name = conn.execute("SELECT name FROM player WHERE id = ?", (player_id,)).fetchone()["name"]
        raise CorrectionError(f"{name!r} não tem registro nessa sessão")
    return row


# --- consulta -------------------------------------------------------------------


@dataclass
class AttendanceView:
    player_id: int
    name: str
    status: str
    section: str
    note: str


@dataclass
class SessionView:
    session_id: int
    ordem: int
    date: str
    venue: str
    vagas_vazias: int = 0
    observacao: str = ""
    entries: list[AttendanceView] = field(default_factory=list)


_SECTION_ORDER = {s: i for i, s in enumerate(db.SECTIONS)}


def show_session(conn: sqlite3.Connection, date_iso: str) -> SessionView:
    """Sessão com suas presenças, ordenadas por seção e nome."""
    s = find_session(conn, date_iso)
    meta = conn.execute(
        "SELECT vagas_vazias, observacao FROM match_meta WHERE session_id = ?", (s["id"],)
    ).fetchone()
    view = SessionView(
        session_id=s["id"],
        ordem=s["ordem"],
        date=s["date"],
        venue=s["venue"],
        vagas_vazias=meta["vagas_vazias"] if meta else 0,
        observacao=meta["observacao"] if meta else "",
    )
    rows = conn.execute(
        "SELECT a.player_id, p.name, a.status, a.section, a.note FROM attendance a "
        "JOIN player p ON p.id = a.player_id WHERE a.session_id = ?",
        (s["id"],),
    ).fetchall()
    rows.sort(key=lambda r: (_SECTION_ORDER.get(r["section"], len(db.SECTIONS)), r["name"]))
    view.entries = [
        AttendanceView(r["player_id"], r["name"], r["status"], r["section"], r["note"])
        for r in rows
    ]
    return view


def render_session(view: SessionView) -> str:
    lines = [f"Partida {view.date} @ {view.venue or '?'} (ordem {view.ordem})"]
    counts = {s: 0 for s in db.STATUSES}
    for e in view.entries:
        counts[e.status] += 1
    lines.append(
        f"  {counts['X']} presença, {counts['F']} furo, {counts['J']} jogou, "
        f"{counts['-']} não jogou, {view.vagas_vazias} vagas vazias"
    )
    for e in view.entries:
        note = f"  # {e.note}" if e.note else ""
        lines.append(f"  {e.status} {e.section[:3]:>3} {e.name:<28} [{e.player_id}]{note}")
    return "\n".join(lines)


# --- operações --------------------------------------------------------------------


@dataclass
class RelinkResult:
    date: str
    old_player: str
    new_player: str
    learned_alias: str = ""


def relink(
    conn: sqlite3.Connection,
    date_iso: str,
    old: int | str,
    new: int | str,
    *,
    alias: str | None = None,
) -> RelinkResult:
    """Move a presença de `old` para `new` na sessão, preservando status, seção e observação.

    `alias` (a grafia usada na lista) passa a apontar para `new`, corrigindo o vínculo
    para as próximas listas.
    """
    s = find_session(conn, date_iso)
    old_p = find_player(conn, old)
    new_p = find_player(conn, new)
    if old_p["id"] == new_p["id"]:
        raise CorrectionError(f"{old_p['name']!r} já é o jogador vinculado")
    _attendance(conn, s["id"], old_p["id"])
    if conn.execute(
        "SELECT 1 FROM attendance WHERE session_id = ? AND player_id = ?", (s["id"], new_p["id"])
    ).fetchone():
        raise CorrectionError(f"{new_p['name']!r} já tem registro nessa sessão")
    learned = ""
    with conn:
        conn.execute(
            "UPDATE attendance SET player_id = ? WHERE session_id = ? AND player_id = ?",
            (new_p["id"], s["id"], old_p["id"]),
        )
        if alias and aliases.learn(conn, alias, new_p["id"]):
            learned = aliases.normalize(alias)
    return RelinkResult(s["date"], old_p["name"], new_p["name"], learned)


@dataclass
class StatusResult:
    date: str
    player: str
    old_status: str
    new_status: str


def set_status(
    conn: sqlite3.Connection, date_iso: str, player: int | str, status: str
) -> StatusResult:
    """Alterna o status de uma presença: X, F, J ou `-` (listado, não jogou)."""
    if status not in db.STATUSES:
        raise CorrectionError(f"status {status!r} não é X, F, J ou -")
    s = find_session(conn, date_iso)
    p = find_player(conn, player)
    row = _attendance(conn, s["id"], p["id"])
    with conn:
        conn.execute(
            "UPDATE attendance SET status = ? WHERE session_id = ? AND player_id = ?",
            (status, s["id"], p["id"]),
        )
    return StatusResult(s["date"], p["name"], row["status"], status)


@dataclass
class SectionResult:
    date: str
    player: str
    old_section: str
    new_section: str


def set_section(
    conn: sqlite3.Connection, date_iso: str, player: int | str, section: str
) -> SectionResult:
    """Corrige a seção da lista em que o jogador estava."""
    if section not in db.SECTIONS:
        raise CorrectionError(f"seção {section!r} não é {', '.join(db.SECTIONS)}")
    s = find_session(conn, date_iso)
    p = find_player(conn, player)
    row = _attendance(conn, s["id"], p["id"])
    with conn:
        conn.execute(
            "UPDATE attendance SET section = ? WHERE session_id = ? AND player_id = ?",
            (section, s["id"], p["id"]),
        )
    return SectionResult(s["date"], p["name"], row["section"], section)


@dataclass
class SessionResult:
    session_id: int
    old_date: str
    new_date: str
    old_venue: str
    new_venue: str


def set_date(conn: sqlite3.Connection, date_iso: str, new_date: str) -> SessionResult:
    """Move a sessão para outra data e renumera `ordem`."""
    s = find_session(conn, date_iso)
    d = _iso(new_date, "nova data")
    if d == s["date"]:
        raise CorrectionError(f"a sessão já está em {d}")
    if conn.execute("SELECT 1 FROM session WHERE date = ?", (d,)).fetchone():
        raise CorrectionError(f"já existe sessão em {d}")
    with conn:
        conn.execute("UPDATE session SET date = ? WHERE id = ?", (d, s["id"]))
        db.renumber_sessions(conn)
    return SessionResult(s["id"], s["date"], d, s["venue"], s["venue"])


def set_venue(conn: sqlite3.Connection, date_iso: str, venue: str) -> SessionResult:
    """Corrige o local da sessão."""
    s = find_session(conn, date_iso)
    venue = venue.strip()
    with conn:
        conn.execute("UPDATE session SET venue = ? WHERE id = ?", (venue, s["id"]))
    return SessionResult(s["id"], s["date"], s["date"], s["venue"], venue)


@dataclass
class DeleteResult:
    session_id: int
    date: str
    venue: str
    attendance: int


def delete_session(
    conn: sqlite3.Connection, date_iso: str, *, confirm: bool = False
) -> DeleteResult:
    """Exclui a sessão, suas presenças e `match_meta`. Exige `confirm=True`.

    Jogadores e aliases criados a partir dela são mantidos.
    """
    s = find_session(conn, date_iso)
    n = conn.execute("SELECT COUNT(*) FROM attendance WHERE session_id = ?", (s["id"],)).fetchone()
    result = DeleteResult(s["id"], s["date"], s["venue"], n[0])
    if not confirm:
        raise CorrectionError(
            f"excluir a sessão {s['date']} ({result.attendance} presenças) exige confirmação"
        )
    with conn:
        conn.execute("DELETE FROM session WHERE id = ?", (s["id"],))
        db.renumber_sessions(conn)
    return result
