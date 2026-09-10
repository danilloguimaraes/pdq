"""Higiene conservadora de vínculos e mesclas de jogadores."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from pdq import aliases

_PAREN_SUFFIX = re.compile(r"\s*\((?:(?:amigo|convidado)\s+)?([^()]+?)\)\s*$", re.IGNORECASE)
_WORD_SUFFIX = re.compile(r"\s+(?:amigo|convidado)\s+([^()]+?)\s*$", re.IGNORECASE)


class HygieneError(ValueError):
    """Mescla inválida; a transação não gravou nenhuma alteração."""


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
        conn.execute("UPDATE player_alias SET player_id = ? WHERE player_id = ?", (canonical_id, source_id))

        learned = []
        for name in (source["name"], canonical["name"]):
            alias = aliases.normalize(name)
            if alias and conn.execute("SELECT 1 FROM player_alias WHERE alias = ?", (alias,)).fetchone() is None:
                conn.execute("INSERT INTO player_alias (alias, player_id) VALUES (?, ?)", (alias, canonical_id))
                learned.append(alias)

    return MergeEvidence(source_id, source["name"], canonical_id, canonical["name"], attendance_count, redirected, tuple(learned))


def link_legacy_padrinhos(conn: sqlite3.Connection) -> HygieneReport:
    """Persiste somente referências extraídas que identificam um canônico único."""
    resolver = aliases.Resolver.from_db(conn)
    report = HygieneReport()
    rows = conn.execute("SELECT id, name FROM player WHERE padrinho_id IS NULL ORDER BY pos").fetchall()
    with conn:
        for row in rows:
            padrinho = extract_padrinho(row["name"])
            if padrinho is None:
                continue
            padrinho_id = resolver.canonical(padrinho)
            if padrinho_id is None:
                ids = resolver.canonical_candidates(padrinho)
                report.pending.append(PadrinhoPending(row["id"], row["name"], padrinho, "ambíguo" if len(ids) > 1 else "ausente"))
                continue
            if padrinho_id == row["id"]:
                report.pending.append(PadrinhoPending(row["id"], row["name"], padrinho, "próprio jogador"))
                continue
            conn.execute("UPDATE player SET padrinho_id = ? WHERE id = ?", (padrinho_id, row["id"]))
            report.linked.append(PadrinhoLink(row["id"], row["name"], padrinho, padrinho_id))
    return report
