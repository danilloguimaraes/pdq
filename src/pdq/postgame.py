"""Registro pós-jogo: da lista do WhatsApp à partida gravada.

Fluxo em dois passos, com um JSON editável no meio:

1. `build_proposal` interpreta a lista, vincula nomes a jogadores e propõe um
   status por linha: X (presença), F (furo) ou J (reserva que jogou).
   Nomes não reconhecidos viram `action: "review"` (há sugestões) ou
   `action: "create"` (jogador novo, nome editável e padrinho).
2. `confirm` recusa propostas com pendências e, numa única transação, grava
   sessão, meta da partida, presenças, jogadores novos e aprende aliases.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date

from pdq import aliases, db, whatsapp

PROPOSAL_SCHEMA = "pdq.proposal/1"

ACTION_LINK = "link"  # usa player_id
ACTION_CREATE = "create"  # cria new_player
ACTION_REVIEW = "review"  # pendente: escolher link/create/skip
ACTION_SKIP = "skip"  # ignora a linha
ACTIONS = (ACTION_LINK, ACTION_CREATE, ACTION_REVIEW, ACTION_SKIP)

PROPOSAL_STATUSES = (db.STATUS_PRESENT, db.STATUS_ABSENT, db.STATUS_PLAYED)


class ProposalError(ValueError):
    """Proposta inválida ou com pendências; nada foi gravado."""


@dataclass
class NewPlayer:
    name: str
    posicao: str = "L"  # G para goleiros
    padrinho: str = ""


@dataclass
class ProposalEntry:
    raw_name: str
    section: str
    status: str
    action: str
    player_id: int | None = None
    player_name: str = ""
    suggestions: list[dict] = field(default_factory=list)
    new_player: NewPlayer | None = None
    padrinho: str = ""
    observacao: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> ProposalEntry:
        d = dict(d)
        if d.get("new_player") is not None:
            d["new_player"] = NewPlayer(**d["new_player"])
        return cls(**d)


@dataclass
class Proposal:
    date: str  # ISO 8601
    venue: str = ""
    vagas_vazias: int = 0
    observacao: str = ""
    raw_list: str = ""
    entries: list[ProposalEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: str = PROPOSAL_SCHEMA

    # --- serialização -------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Proposal:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ProposalError(f"JSON inválido: {e}") from e
        if not isinstance(data, dict) or data.get("schema") != PROPOSAL_SCHEMA:
            raise ProposalError(f"proposta não reconhecida (schema esperado {PROPOSAL_SCHEMA})")
        try:
            entries = [ProposalEntry.from_dict(e) for e in data.pop("entries", [])]
            return cls(entries=entries, **data)
        except TypeError as e:
            raise ProposalError(f"campo inesperado na proposta: {e}") from e

    # --- consultas -----------------------------------------------------------

    def pending(self) -> list[ProposalEntry]:
        return [e for e in self.entries if e.action == ACTION_REVIEW]

    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in PROPOSAL_STATUSES}
        for e in self.entries:
            if e.action != ACTION_SKIP:
                c[e.status] = c.get(e.status, 0) + 1
        return c


# --- construção ---------------------------------------------------------------


def _resolve_date(parsed: whatsapp.ParsedList, explicit: str | None, today: date) -> str:
    if explicit:
        try:
            return date.fromisoformat(explicit).isoformat()
        except ValueError as e:
            raise ProposalError(f"data inválida {explicit!r}: use AAAA-MM-DD") from e
    if parsed.day is None or parsed.month is None:
        raise ProposalError("a lista não traz data; informe --date AAAA-MM-DD")
    year = parsed.year or today.year
    try:
        d = date(year, parsed.month, parsed.day)
    except ValueError as e:
        raise ProposalError(f"data inválida na lista: {parsed.day}/{parsed.month}/{year}") from e
    if parsed.year is None and d > today:
        d = d.replace(year=year - 1)  # lista de dezembro registrada em janeiro
    return d.isoformat()


def _resolve_venue(conn: sqlite3.Connection, parsed: whatsapp.ParsedList, explicit: str | None):
    if explicit is not None:
        return explicit
    known = [
        r["venue"]
        for r in conn.execute(
            "SELECT venue, COUNT(*) n FROM session WHERE venue <> '' GROUP BY venue ORDER BY n DESC"
        )
    ]
    title = aliases.normalize(parsed.title)
    for venue in known:
        if aliases.normalize(venue) and aliases.normalize(venue) in title:
            return venue
    row = conn.execute("SELECT venue FROM session ORDER BY date DESC LIMIT 1").fetchone()
    return row["venue"] if row else ""


def _default_status(entry: whatsapp.Entry) -> str:
    if entry.furo:
        return db.STATUS_ABSENT
    if entry.section == whatsapp.SECTION_RESERVES:
        return db.STATUS_PLAYED
    return db.STATUS_PRESENT


def build_proposal(
    conn: sqlite3.Connection,
    text: str,
    *,
    date_iso: str | None = None,
    venue: str | None = None,
    today: date | None = None,
) -> Proposal:
    today = today or date.today()
    parsed = whatsapp.parse(text)
    resolver = aliases.Resolver.from_db(conn)

    proposal = Proposal(
        date=_resolve_date(parsed, date_iso, today),
        venue=_resolve_venue(conn, parsed, venue),
        vagas_vazias=parsed.vagas_vazias,
        raw_list=text,
    )
    if not parsed.entries:
        proposal.warnings.append("nenhuma linha numerada com nome foi encontrada")
    for line in parsed.unparsed:
        proposal.warnings.append(f"linha ignorada: {line!r}")

    seen: dict[int, str] = {}
    for e in parsed.entries:
        match = resolver.resolve(e.name)
        pe = ProposalEntry(
            raw_name=e.name,
            section=e.section,
            status=_default_status(e),
            action=ACTION_LINK if match.exact else ACTION_REVIEW,
            player_id=match.player_id,
            player_name=resolver.players.get(match.player_id, "") if match.resolved else "",
            suggestions=[asdict(s) for s in match.suggestions],
            padrinho=e.padrinho,
            observacao=e.observacao,
        )
        if not match.exact:
            pe.new_player = NewPlayer(
                name=e.name.upper(),
                posicao="G" if e.section == whatsapp.SECTION_GOALKEEPERS else "L",
                padrinho=e.padrinho,
            )
            if not match.suggestions:
                pe.action = ACTION_CREATE
        if pe.player_id is not None:
            if pe.player_id in seen:
                proposal.warnings.append(
                    f"{e.name!r} e {seen[pe.player_id]!r} apontam para o mesmo jogador "
                    f"{pe.player_name!r}"
                )
            seen[pe.player_id] = e.name
        proposal.entries.append(pe)

    if conn.execute("SELECT 1 FROM session WHERE date = ?", (proposal.date,)).fetchone():
        proposal.warnings.append(f"já existe sessão em {proposal.date}; confirmar vai falhar")
    return proposal


# --- confirmação ----------------------------------------------------------------


@dataclass
class ConfirmResult:
    session_id: int
    date: str
    attendance: int
    created_players: list[str]
    learned_aliases: list[str]


def validate(conn: sqlite3.Connection, proposal: Proposal) -> None:
    """Levanta ProposalError se a proposta não puder ser gravada."""
    problems: list[str] = []
    try:
        date.fromisoformat(proposal.date)
    except (TypeError, ValueError):
        problems.append(f"data inválida: {proposal.date!r}")
    if conn.execute("SELECT 1 FROM session WHERE date = ?", (proposal.date,)).fetchone():
        problems.append(f"já existe sessão em {proposal.date}")

    player_ids = {r["id"] for r in conn.execute("SELECT id FROM player")}
    used: dict[int, str] = {}
    for i, e in enumerate(proposal.entries, 1):
        where = f"linha {i} ({e.raw_name!r})"
        if e.action not in ACTIONS:
            problems.append(f"{where}: action desconhecida {e.action!r}")
            continue
        if e.action == ACTION_SKIP:
            continue
        if e.status not in PROPOSAL_STATUSES:
            problems.append(f"{where}: status {e.status!r} não é X, F ou J")
        if e.action == ACTION_REVIEW:
            problems.append(f"{where}: pendente de revisão (defina action link/create/skip)")
        elif e.action == ACTION_LINK:
            if e.player_id not in player_ids:
                problems.append(f"{where}: player_id {e.player_id!r} não existe")
            elif e.player_id in used:
                problems.append(f"{where}: jogador repetido ({used[e.player_id]!r})")
            else:
                used[e.player_id] = e.raw_name
        elif e.action == ACTION_CREATE:
            if e.new_player is None or not e.new_player.name.strip():
                problems.append(f"{where}: new_player.name vazio")
            elif e.new_player.posicao not in ("L", "G", ""):
                problems.append(f"{where}: posicao {e.new_player.posicao!r} não é L ou G")
    if problems:
        raise ProposalError("proposta com pendências:\n  - " + "\n  - ".join(problems))


def confirm(conn: sqlite3.Connection, proposal: Proposal) -> ConfirmResult:
    """Grava a partida. Tudo ou nada."""
    validate(conn, proposal)
    d = date.fromisoformat(proposal.date)
    created: list[str] = []
    learned: list[str] = []
    rows = 0
    with conn:
        cur = conn.execute(
            "INSERT INTO session (ordem, date, venue) VALUES "
            "((SELECT COALESCE(MAX(ordem), 0) + 1 FROM session), ?, ?)",
            (proposal.date, proposal.venue),
        )
        session_id = cur.lastrowid
        db.renumber_sessions(conn)
        conn.execute(
            "INSERT INTO match_meta (session_id, vagas_vazias, observacao, raw_list) "
            "VALUES (?, ?, ?, ?)",
            (session_id, proposal.vagas_vazias, proposal.observacao, proposal.raw_list),
        )
        for e in proposal.entries:
            if e.action == ACTION_SKIP:
                continue
            if e.action == ACTION_CREATE:
                np = e.new_player
                cur = conn.execute(
                    "INSERT INTO player "
                    "(pos, classe, posicao, legacy_id, name, padrinho, guest_status) VALUES "
                    "((SELECT COALESCE(MAX(pos), 0) + 1 FROM player), ?, ?, ?, ?, ?, ?)",
                    (
                        db.CLASS_GUEST,
                        np.posicao,
                        d.strftime("%y%m"),
                        np.name.strip(),
                        np.padrinho.strip(),
                        db.GUEST_PENDING,
                    ),
                )
                pid = cur.lastrowid
                created.append(np.name.strip())
            else:
                pid = e.player_id
                if e.padrinho:
                    conn.execute(
                        "UPDATE player SET padrinho = ? WHERE id = ? AND padrinho = ''",
                        (e.padrinho, pid),
                    )
            conn.execute(
                "INSERT INTO attendance (player_id, session_id, status, note, section) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, session_id, e.status, e.observacao, e.section),
            )
            rows += 1
            if _worth_learning(conn, e.raw_name, pid) and aliases.learn(conn, e.raw_name, pid):
                learned.append(aliases.normalize(e.raw_name))
    return ConfirmResult(session_id, proposal.date, rows, created, learned)


def _worth_learning(conn: sqlite3.Connection, raw_name: str, player_id: int) -> bool:
    """Só aprende quando o nome escrito difere do cadastrado (evita aliases redundantes)."""
    name = conn.execute("SELECT name FROM player WHERE id = ?", (player_id,)).fetchone()["name"]
    return aliases.normalize(raw_name) != aliases.normalize(name)


# --- apresentação -----------------------------------------------------------------

_STATUS_LABEL = {db.STATUS_PRESENT: "presença", db.STATUS_ABSENT: "furo", db.STATUS_PLAYED: "jogou"}


def render_summary(proposal: Proposal) -> str:
    """Resumo legível para o terminal."""
    lines = [f"Partida {proposal.date} @ {proposal.venue or '?'}"]
    counts = proposal.counts()
    lines.append(
        "  "
        + ", ".join(f"{counts[s]} {_STATUS_LABEL[s]}" for s in PROPOSAL_STATUSES)
        + f", {proposal.vagas_vazias} vagas vazias"
    )
    for e in proposal.entries:
        if e.action == ACTION_LINK:
            target = f"-> {e.player_name}"
        elif e.action == ACTION_CREATE:
            target = f"-> NOVO {e.new_player.name}" if e.new_player else "-> NOVO"
            if e.new_player and e.new_player.padrinho:
                target += f" (padrinho: {e.new_player.padrinho})"
        elif e.action == ACTION_SKIP:
            target = "-> (ignorado)"
        else:
            opts = ", ".join(f"{s['name']} [{s['player_id']}]" for s in e.suggestions)
            target = f"?? REVISAR: {opts}"
        note = f"  # {e.observacao}" if e.observacao else ""
        lines.append(f"  {e.status} {e.section[:3]:>3} {e.raw_name:<28} {target}{note}")
    for w in proposal.warnings:
        lines.append(f"  ! {w}")
    if pending := proposal.pending():
        lines.append(f"  {len(pending)} linha(s) pendente(s): edite o JSON antes de confirmar")
    return "\n".join(lines)
