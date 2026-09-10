"""Higiene conservadora dos vínculos de padrinho nos nomes legados."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from pdq import aliases

_PAREN_SUFFIX = re.compile(r"\s*\((?:(?:amigo|convidado)\s+)?([^()]+?)\)\s*$", re.IGNORECASE)
_WORD_SUFFIX = re.compile(r"\s+(?:amigo|convidado)\s+([^()]+?)\s*$", re.IGNORECASE)


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
