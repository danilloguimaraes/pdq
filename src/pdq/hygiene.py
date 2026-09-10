"""Consulta somente de leitura para a higiene de jogadores legados.

Os resultados deste módulo são alertas para curadoria. Nenhuma consulta altera
``player``, ``player_alias`` ou ``attendance``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from pdq import aliases

DEFAULT_PRESENCE_THRESHOLD = 4
DEFAULT_SIMILARITY_CUTOFF = aliases.DEFAULT_CUTOFF

_PADRINHO_RE = re.compile(
    r"(?:\(\s*(?:padrinho|madrinha|conv(?:idado|ite)?\.?|amigo|convidado)"
    r"(?:\s+(?:do|da|de|por|pelo|pela))?\s*[:\-–—]?\s*([^()]+?)\s*\)"
    r"|\s+[-–—]\s*(?:conv(?:idado|ite)?\.?|padrinho|madrinha)"
    r"(?:\s+(?:do|da|de|por|pelo|pela))?\s*[:\-–—]?\s*(.+?)\s*$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DuplicateCandidate:
    """Outro jogador cujo nome é potencial duplicidade do item consultado."""

    player_id: int
    name: str
    score: float


@dataclass(frozen=True)
class HygieneItem:
    """Jogador legado elegível para revisão, acompanhado de alertas derivados."""

    player_id: int
    name: str
    presences: int
    padrinho_annotation: str | None = None
    duplicate_candidates: tuple[DuplicateCandidate, ...] = ()


@dataclass(frozen=True)
class CurationDecision:
    """Decisão declarada pela curadoria; aplicá-la é responsabilidade de outro fluxo."""

    player_id: int
    action: str
    note: str = ""


def extract_padrinho_annotation(name: str) -> str | None:
    """Retorna a referência de padrinho embutida no nome, sem modificar o nome."""
    match = _PADRINHO_RE.search(name)
    if match is None:
        return None
    annotation = next((group for group in match.groups() if group is not None), "").strip(" .:-")
    return annotation or None


def legacy_players(
    conn: sqlite3.Connection,
    *,
    threshold: int = DEFAULT_PRESENCE_THRESHOLD,
    similarity_cutoff: float = DEFAULT_SIMILARITY_CUTOFF,
) -> list[HygieneItem]:
    """Lista jogadores de classe legada vazia ou ``-`` que atingiram o limiar.

    Só ``X`` e ``J`` contam como presenças. Alertas de padrinho e duplicidade
    são calculados em memória e não representam uma decisão ou alteração.
    """
    if threshold < 0:
        raise ValueError("threshold precisa ser não negativo")
    if not 0 <= similarity_cutoff <= 1:
        raise ValueError("similarity_cutoff precisa estar entre 0 e 1")

    rows = conn.execute(
        """
        SELECT p.id, p.name,
               SUM(CASE WHEN a.status IN ('X', 'J') THEN 1 ELSE 0 END) AS presences
          FROM player p
          LEFT JOIN attendance a ON a.player_id = p.id
         WHERE p.classe IN ('', '-')
         GROUP BY p.id, p.name
        HAVING presences >= ?
         ORDER BY presences DESC, p.name, p.id
        """,
        (threshold,),
    ).fetchall()

    # SQLite's text collation is not the normalization used by the domain.
    rows = sorted(
        rows,
        key=lambda row: (-row["presences"], aliases.normalize(row["name"]), row["id"]),
    )
    names = [(row["id"], row["name"]) for row in conn.execute("SELECT id, name FROM player")]

    return [
        HygieneItem(
            player_id=row["id"],
            name=row["name"],
            presences=row["presences"],
            padrinho_annotation=extract_padrinho_annotation(row["name"]),
            duplicate_candidates=_duplicates(row["id"], row["name"], names, similarity_cutoff),
        )
        for row in rows
    ]


def _duplicates(
    player_id: int, name: str, names: list[tuple[int, str]], cutoff: float
) -> tuple[DuplicateCandidate, ...]:
    normalized = aliases.normalize(name)
    candidates = []
    for candidate_id, candidate_name in names:
        if candidate_id == player_id:
            continue
        score = SequenceMatcher(None, normalized, aliases.normalize(candidate_name)).ratio()
        if score >= cutoff:
            candidates.append(DuplicateCandidate(candidate_id, candidate_name, round(score, 3)))
    return tuple(
        sorted(candidates, key=lambda c: (-c.score, aliases.normalize(c.name), c.player_id))
    )
