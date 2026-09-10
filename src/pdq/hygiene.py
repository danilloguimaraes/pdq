"""Higiene conservadora dos vínculos de padrinho nos nomes legados."""

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


def padrinho_report(
    conn: sqlite3.Connection, *, cutoff: float = DEFAULT_SIMILARITY_CUTOFF
) -> list[PadrinhoReview]:
    """Lista referências legadas sem vínculo e candidatos apenas sugestivos.

    O limiar é a similaridade mínima entre a anotação e o nome canônico. Nem
    mesmo um candidato com pontuação 1.0 é aplicado por esta consulta.
    """
    if not 0 <= cutoff <= 1:
        raise HygieneError("limiar precisa estar entre 0 e 1")
    players = conn.execute("SELECT id, name FROM player ORDER BY pos, id").fetchall()
    reviews: list[PadrinhoReview] = []
    for row in players:
        if conn.execute(
            "SELECT padrinho_id FROM player WHERE id = ?", (row["id"],)
        ).fetchone()[0]:
            continue
        padrinho = extract_padrinho(row["name"])
        if padrinho is None:
            continue
        needle = aliases.normalize(padrinho)
        candidates = []
        for candidate in players:
            if candidate["id"] == row["id"]:
                continue
            score = SequenceMatcher(
                None, needle, aliases.normalize(candidate["name"])
            ).ratio()
            if score >= cutoff:
                candidates.append(
                    PadrinhoCandidate(candidate["id"], candidate["name"], round(score, 3))
                )
        candidates.sort(
            key=lambda candidate: (
                -candidate.score,
                aliases.normalize(candidate.name),
                candidate.player_id,
            )
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
        if review.candidates:
            for candidate in review.candidates:
                lines.append(
                    f"    candidato: {candidate.player_id} {candidate.name} ({candidate.score:.3f})"
                )
        else:
            lines.append("    candidato: nenhum")
    return "\n".join(lines)


def decisions_from_json(text: str) -> list[PadrinhoDecision]:
    """Lê o arquivo de decisões explícitas de vínculo de padrinho."""
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
    decisions: list[PadrinhoDecision] = []
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
    """Valida todas as decisões antes de iniciar a transação de escrita."""
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
        if player is None:
            raise HygieneError(f"referência inválida: player_id {decision.player_id} não existe")
        padrinho = conn.execute(
            "SELECT id, name FROM player WHERE id = ?", (decision.padrinho_id,)
        ).fetchone()
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
    """Persiste as decisões já revisadas em uma única transação."""
    links = validate_padrinho_decisions(conn, decisions)
    with conn:
        conn.executemany(
            "UPDATE player SET padrinho_id = ? WHERE id = ?",
            [(link.padrinho_id, link.player_id) for link in links],
        )
    return links


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
