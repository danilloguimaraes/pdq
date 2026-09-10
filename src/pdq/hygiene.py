"""Higiene conservadora de vínculos e mesclas de jogadores."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pdq import aliases

_PAREN_SUFFIX = re.compile(r"\s*\((?:(?:amigo|convidado)\s+)?([^()]+?)\)\s*$", re.IGNORECASE)
_WORD_SUFFIX = re.compile(r"\s+(?:amigo|convidado)\s+([^()]+?)\s*$", re.IGNORECASE)
DEFAULT_SIMILARITY_CUTOFF = aliases.DEFAULT_CUTOFF


class HygieneError(ValueError):
    """Decisão de higiene inválida; nenhuma alteração foi gravada."""


@dataclass(frozen=True)
class PadrinhoCandidate:
    player_id: int
    name: str
    score: float


@dataclass(frozen=True)
class PadrinhoReview:
    player_id: int
    player_name: str
    padrinho: str
    candidates: tuple[PadrinhoCandidate, ...]


@dataclass(frozen=True)
class PadrinhoDecision:
    player_id: int
    padrinho_id: int


@dataclass(frozen=True)
class MergeEvidence:
    source_id: int
    source_name: str
    canonical_id: int
    canonical_name: str
    attendance_count: int
    redirected_aliases: tuple[str, ...]
    learned_aliases: tuple[str, ...]


def extract_padrinho(name: str) -> str | None:
    """Extrai a referência no sufixo legado, sem alterar o nome literal."""
    match = _PAREN_SUFFIX.search(name) or _WORD_SUFFIX.search(name)
    return match.group(1).strip() if match and match.group(1).strip() else None


@dataclass(frozen=True)
class PadrinhoLink:
    player_id: int
    player_name: str
    padrinho: str
    padrinho_id: int


@dataclass(frozen=True)
class PadrinhoPending:
    player_id: int
    player_name: str
    padrinho: str
    reason: str


@dataclass
class HygieneReport:
    linked: list[PadrinhoLink] = field(default_factory=list)
    pending: list[PadrinhoPending] = field(default_factory=list)


def padrinho_report(
    conn: sqlite3.Connection, *, cutoff: float = DEFAULT_SIMILARITY_CUTOFF
) -> list[PadrinhoReview]:
    """Lista referências pendentes e candidatos sugestivos, sem gravar nada."""
    if not 0 <= cutoff <= 1:
        raise HygieneError("limiar precisa estar entre 0 e 1")
    players = conn.execute("SELECT id, name FROM player ORDER BY pos, id").fetchall()
    reviews: list[PadrinhoReview] = []
    for row in players:
        if conn.execute("SELECT padrinho_id FROM player WHERE id = ?", (row["id"],)).fetchone()[0]:
            continue
        padrinho = extract_padrinho(row["name"])
        if padrinho is None:
            continue
        needle = aliases.normalize(padrinho)
        candidates = [
            PadrinhoCandidate(candidate["id"], candidate["name"], round(score, 3))
            for candidate in players
            if candidate["id"] != row["id"]
            and (
                score := SequenceMatcher(None, needle, aliases.normalize(candidate["name"])).ratio()
            )
            >= cutoff
        ]
        candidates.sort(
            key=lambda candidate: (-candidate.score, candidate.name, candidate.player_id)
        )
        reviews.append(PadrinhoReview(row["id"], row["name"], padrinho, tuple(candidates)))
    return reviews


def render_padrinho_report(reviews: list[PadrinhoReview]) -> str:
    """Renderiza a prévia determinística para revisão humana."""
    lines = ["Higiene de padrinhos (candidatos não são aplicados automaticamente):"]
    if not reviews:
        return "\n".join(lines + ["  nenhuma referência legada pendente"])
    for review in reviews:
        lines.append(f"  {review.player_id} {review.player_name} -> {review.padrinho}")
        lines.extend(
            f"    candidato: {candidate.player_id} {candidate.name} ({candidate.score:.3f})"
            for candidate in review.candidates
        )
        if not review.candidates:
            lines.append("    candidato: nenhum")
    return "\n".join(lines)


def decisions_from_json(text: str) -> list[PadrinhoDecision]:
    """Lê o arquivo estrito de decisões explícitas de vínculo de padrinho."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HygieneError("JSON inválido no arquivo de decisões") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"decisions"}
        or not isinstance(data["decisions"], list)
    ):
        raise HygieneError("decisões devem ser um objeto com a lista 'decisions'")
    decisions = []
    for index, value in enumerate(data["decisions"], 1):
        if not isinstance(value, dict) or set(value) != {"player_id", "padrinho_id"}:
            raise HygieneError(f"decisão {index} deve ter player_id e padrinho_id")
        player_id, padrinho_id = value["player_id"], value["padrinho_id"]
        if type(player_id) is not int or type(padrinho_id) is not int:
            raise HygieneError(f"decisão {index} deve usar player_id e padrinho_id inteiros")
        decisions.append(PadrinhoDecision(player_id, padrinho_id))
    return decisions


def validate_padrinho_decisions(
    conn: sqlite3.Connection, decisions: list[PadrinhoDecision]
) -> list[PadrinhoLink]:
    """Valida o conjunto inteiro antes de qualquer escrita."""
    links: list[PadrinhoLink] = []
    seen: set[int] = set()
    for decision in decisions:
        if decision.player_id in seen:
            raise HygieneError(
                f"referência ambígua: player_id {decision.player_id} tem mais de uma decisão"
            )
        seen.add(decision.player_id)
        player = conn.execute(
            "SELECT id, name, padrinho_id FROM player WHERE id = ?", (decision.player_id,)
        ).fetchone()
        padrinho = conn.execute(
            "SELECT id FROM player WHERE id = ?", (decision.padrinho_id,)
        ).fetchone()
        if player is None:
            raise HygieneError(f"referência inválida: player_id {decision.player_id} não existe")
        if padrinho is None:
            raise HygieneError(
                f"referência inválida: padrinho_id {decision.padrinho_id} não existe"
            )
        if decision.player_id == decision.padrinho_id:
            raise HygieneError("referência inválida: jogador não pode ser seu próprio padrinho")
        if player["padrinho_id"] is not None:
            raise HygieneError(f"player_id {decision.player_id} já possui padrinho vinculado")
        annotation = extract_padrinho(player["name"])
        if annotation is None:
            raise HygieneError(
                f"player_id {decision.player_id} não possui referência legada de padrinho"
            )
        links.append(PadrinhoLink(player["id"], player["name"], annotation, padrinho["id"]))
    return links


def apply_padrinho_decisions(
    conn: sqlite3.Connection, decisions: list[PadrinhoDecision]
) -> list[PadrinhoLink]:
    """Persiste decisões revisadas em uma única transação."""
    links = validate_padrinho_decisions(conn, decisions)
    with conn:
        conn.executemany(
            "UPDATE player SET padrinho_id = ? WHERE id = ?",
            [(link.padrinho_id, link.player_id) for link in links],
        )
    return links


def merge(conn: sqlite3.Connection, source_id: int, canonical_id: int) -> MergeEvidence:
    """Mescla a origem no canônico sem reescrever suas presenças legadas."""
    if isinstance(source_id, bool) or isinstance(canonical_id, bool):
        raise HygieneError("origem e canônico devem ser player_id inteiros")
    if not isinstance(source_id, int) or not isinstance(canonical_id, int):
        raise HygieneError("origem e canônico devem ser player_id inteiros")
    if source_id == canonical_id:
        raise HygieneError("origem e canônico precisam ser jogadores diferentes")

    with conn:
        source = conn.execute(
            "SELECT id, name, canonical_player_id FROM player WHERE id = ?", (source_id,)
        ).fetchone()
        canonical = conn.execute(
            "SELECT id, name, canonical_player_id FROM player WHERE id = ?", (canonical_id,)
        ).fetchone()
        if source is None:
            raise HygieneError(f"origem player_id {source_id} não existe")
        if canonical is None:
            raise HygieneError(f"canônico player_id {canonical_id} não existe")
        if source["canonical_player_id"] is not None:
            raise HygieneError(f"origem player_id {source_id} já foi mesclada")
        if canonical["canonical_player_id"] is not None:
            raise HygieneError(f"canônico player_id {canonical_id} não é canônico")
        redirected = tuple(
            row["alias"]
            for row in conn.execute(
                "SELECT alias FROM player_alias WHERE player_id = ? ORDER BY alias", (source_id,)
            )
        )
        attendance_count = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE player_id = ?", (source_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE player SET canonical_player_id = ? WHERE id = ?", (canonical_id, source_id)
        )
        conn.execute(
            "UPDATE player_alias SET player_id = ? WHERE player_id = ?", (canonical_id, source_id)
        )
        conn.execute(
            "UPDATE payment SET player_id = ? WHERE player_id = ?", (canonical_id, source_id)
        )
        learned = []
        for name in (source["name"], canonical["name"]):
            alias = aliases.normalize(name)
            exists = conn.execute("SELECT 1 FROM player_alias WHERE alias = ?", (alias,)).fetchone()
            if alias and exists is None:
                conn.execute(
                    "INSERT INTO player_alias (alias, player_id) VALUES (?, ?)",
                    (alias, canonical_id),
                )
                learned.append(alias)
    return MergeEvidence(
        source_id,
        source["name"],
        canonical_id,
        canonical["name"],
        attendance_count,
        redirected,
        tuple(learned),
    )


def link_legacy_padrinhos(conn: sqlite3.Connection) -> HygieneReport:
    """Persiste somente referências extraídas que identificam um canônico único."""
    resolver = aliases.Resolver.from_db(conn)
    report = HygieneReport()
    rows = conn.execute(
        "SELECT id, name FROM player WHERE padrinho_id IS NULL ORDER BY pos"
    ).fetchall()
    with conn:
        for row in rows:
            padrinho = extract_padrinho(row["name"])
            if padrinho is None:
                continue
            padrinho_id = resolver.canonical(padrinho)
            if padrinho_id is None:
                ids = resolver.canonical_candidates(padrinho)
                reason = "ambíguo" if len(ids) > 1 else "ausente"
                report.pending.append(PadrinhoPending(row["id"], row["name"], padrinho, reason))
                continue
            if padrinho_id == row["id"]:
                report.pending.append(
                    PadrinhoPending(row["id"], row["name"], padrinho, "próprio jogador")
                )
                continue
            conn.execute("UPDATE player SET padrinho_id = ? WHERE id = ?", (padrinho_id, row["id"]))
            report.linked.append(PadrinhoLink(row["id"], row["name"], padrinho, padrinho_id))
    return report
