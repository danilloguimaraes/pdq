"""Higiene conservadora dos vínculos de padrinho nos nomes legados."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from pdq import aliases

_PAREN_SUFFIX = re.compile(r"\s*\((?:(?:amigo|convidado)\s+)?([^()]+?)\)\s*$", re.IGNORECASE)
_WORD_SUFFIX = re.compile(r"\s+(?:amigo|convidado)\s+([^()]+?)\s*$", re.IGNORECASE)


class HygieneError(ValueError):
    """Decisão de higiene inválida; a conexão permanece inalterada."""


@dataclass(frozen=True)
class ReclassifyDecision:
    """Altera a classe atual de um jogador identificado pela referência declarada."""

    player: int | str
    classe: str


@dataclass(frozen=True)
class SetPadrinhoDecision:
    """Define o padrinho estruturado de um jogador por referências declaradas."""

    player: int | str
    padrinho: int | str


@dataclass(frozen=True)
class MergeDecision:
    """Mescla a origem na identidade canônica de destino."""

    source: int | str
    target: int | str


@dataclass(frozen=True)
class Reclassification:
    player_id: int
    classe: str


@dataclass(frozen=True)
class Merge:
    source_id: int
    target_id: int


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
class HygienePreview:
    """Decisões totalmente resolvidas, pronta para uma futura fase de escrita."""

    reclassifications: tuple[Reclassification, ...] = ()
    padrinhos: tuple[PadrinhoLink, ...] = ()
    merges: tuple[Merge, ...] = ()


def resolve_player_reference(
    conn: sqlite3.Connection, reference: int | str, *, label: str = "jogador"
) -> int:
    """Resolve id inteiro/textual, nome ou alias para exatamente um jogador.

    Esta função é deliberadamente estrita: aliases não têm precedência sobre
    nomes. Uma grafia que represente mais de um jogador é uma ambiguidade.
    """
    if isinstance(reference, bool) or not isinstance(reference, (int, str)):
        raise HygieneError(f"referência de {label} inválida")
    if isinstance(reference, int):
        row = conn.execute("SELECT id FROM player WHERE id = ?", (reference,)).fetchone()
        if row is None:
            raise HygieneError(f"referência de {label} inexistente: {reference}")
        return row["id"]

    value = reference.strip()
    if not value:
        raise HygieneError(f"referência de {label} vazia")
    if value.isdecimal():
        return resolve_player_reference(conn, int(value), label=label)

    candidates = aliases.Resolver.from_db(conn).reference_candidates(value)
    if not candidates:
        raise HygieneError(f"referência de {label} inexistente: {reference!r}")
    if len(candidates) > 1:
        raise HygieneError(f"referência de {label} ambígua: {reference!r}")
    return next(iter(candidates))


def preview(
    conn: sqlite3.Connection,
    decisions: list[ReclassifyDecision | SetPadrinhoDecision | MergeDecision],
) -> HygienePreview:
    """Resolve e valida todas as decisões sem abrir transação nem modificar tabelas."""
    reclassifications: list[Reclassification] = []
    padrinhos: list[PadrinhoLink] = []
    merges: list[Merge] = []
    reclassified: set[int] = set()
    linked: set[int] = set()
    merge_sources: set[int] = set()
    merge_targets: set[int] = set()

    for index, decision in enumerate(decisions, 1):
        if isinstance(decision, ReclassifyDecision):
            player_id = resolve_player_reference(conn, decision.player, label="jogador")
            if decision.classe not in ("", "-", "C", "F", "M"):
                raise HygieneError(f"decisão {index}: classe inválida {decision.classe!r}")
            if player_id in reclassified:
                raise HygieneError(f"decisão {index}: jogador repetido em reclassificação")
            reclassified.add(player_id)
            reclassifications.append(Reclassification(player_id, decision.classe))
        elif isinstance(decision, SetPadrinhoDecision):
            player_id = resolve_player_reference(conn, decision.player, label="jogador")
            padrinho_id = resolve_player_reference(conn, decision.padrinho, label="padrinho")
            if player_id == padrinho_id:
                raise HygieneError(f"decisão {index}: jogador não pode ser seu próprio padrinho")
            if player_id in linked:
                raise HygieneError(f"decisão {index}: jogador repetido em padrinho")
            row = conn.execute("SELECT name FROM player WHERE id = ?", (player_id,)).fetchone()
            linked.add(player_id)
            padrinhos.append(PadrinhoLink(player_id, row["name"], "", padrinho_id))
        elif isinstance(decision, MergeDecision):
            source_id = resolve_player_reference(conn, decision.source, label="origem")
            target_id = resolve_player_reference(conn, decision.target, label="destino")
            if source_id == target_id:
                raise HygieneError(
                    f"decisão {index}: origem e destino não podem ser o mesmo jogador"
                )
            if source_id in merge_sources:
                raise HygieneError(f"decisão {index}: origem repetida em mesclagem")
            if target_id in merge_targets:
                raise HygieneError(f"decisão {index}: destino repetido em mesclagem")
            if source_id in merge_targets or target_id in merge_sources:
                raise HygieneError(f"decisão {index}: mesclagens não podem formar cadeia")
            merge_sources.add(source_id)
            merge_targets.add(target_id)
            merges.append(Merge(source_id, target_id))
        else:
            raise HygieneError(f"decisão {index}: tipo de decisão inválido")

    # Só a origem deixa de existir na aplicação. Reclassificar ou vincular o
    # padrinho do destino continua válido, mas nenhuma decisão pode depender
    # de uma identidade que será absorvida.
    if reclassified & merge_sources:
        raise HygieneError("origem de mesclagem não pode ser reclassificada")
    if linked & merge_sources:
        raise HygieneError("origem de mesclagem não pode receber padrinho")
    if {link.padrinho_id for link in padrinhos} & merge_sources:
        raise HygieneError("padrinho não pode ser origem de mesclagem")
    return HygienePreview(tuple(reclassifications), tuple(padrinhos), tuple(merges))


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
