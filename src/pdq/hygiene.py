"""Higiene de cadastros: mescla lógica e auditável de jogadores duplicados.

Uma mescla não move presenças: elas continuam associadas ao identificador que
as recebeu originalmente. O jogador de origem passa a apontar para o canônico,
e os próximos vínculos por alias usam o canônico.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pdq import aliases


class HygieneError(ValueError):
    """Mescla inválida; a transação não gravou nenhuma alteração."""


@dataclass(frozen=True)
class MergeEvidence:
    """Evidências persistidas ou observadas durante uma mescla."""

    source_id: int
    source_name: str
    canonical_id: int
    canonical_name: str
    attendance_count: int
    redirected_aliases: tuple[str, ...]
    learned_aliases: tuple[str, ...]


def _player(conn: sqlite3.Connection, player_id: int, label: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, canonical_id FROM player WHERE id = ?", (player_id,)
    ).fetchone()
    if row is None:
        raise HygieneError(f"{label} player_id {player_id} não existe")
    return row


def merge(conn: sqlite3.Connection, source_id: int, canonical_id: int) -> MergeEvidence:
    """Mescla `source_id` no `canonical_id` sem reescrever o histórico.

    Ambos os ids são intencionais para tornar a consolidação revisável. O
    canônico precisa ser um jogador raiz; assim a relação nunca cria cadeias.
    """
    if not isinstance(source_id, int) or not isinstance(canonical_id, int):
        raise HygieneError("origem e canônico devem ser player_id inteiros")
    if source_id == canonical_id:
        raise HygieneError("origem e canônico precisam ser jogadores diferentes")

    # Todas as leituras que definem a decisão são feitas antes das escritas; o
    # bloco transacional também garante rollback se um FK ou alias falhar.
    with conn:
        source = _player(conn, source_id, "origem")
        canonical = _player(conn, canonical_id, "canônico")
        if source["canonical_id"] is not None:
            raise HygieneError(f"origem player_id {source_id} já foi mesclada")
        if canonical["canonical_id"] is not None:
            raise HygieneError(f"canônico player_id {canonical_id} não é canônico")

        source_aliases = tuple(
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM player_alias WHERE player_id = ? ORDER BY alias", (source_id,)
            )
        )
        attendance_count = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE player_id = ?", (source_id,)
        ).fetchone()[0]
        conn.execute("UPDATE player SET canonical_id = ? WHERE id = ?", (canonical_id, source_id))
        conn.execute(
            "UPDATE player_alias SET player_id = ? WHERE player_id = ?", (canonical_id, source_id)
        )

        learned: list[str] = []
        # Names are useful aliases after the source is hidden from resolution.
        # Existing aliases win: a conflicting spelling must not be overwritten.
        for name in (source["name"], canonical["name"]):
            key = aliases.normalize(name)
            if not key:
                continue
            existing = conn.execute(
                "SELECT player_id FROM player_alias WHERE alias = ?", (key,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO player_alias (alias, player_id) VALUES (?, ?)",
                    (key, canonical_id),
                )
                learned.append(key)

    return MergeEvidence(
        source_id,
        source["name"],
        canonical_id,
        canonical["name"],
        attendance_count,
        source_aliases,
        tuple(learned),
    )
