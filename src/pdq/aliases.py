"""Vínculo entre nomes escritos na lista do WhatsApp e jogadores do banco.

- `normalize` reduz um nome a uma chave comparável (sem acento, caixa ou
  pontuação): "NAALDI - GK" == "Naaldi GK" == "naaldi gk".
- `Resolver` casa por alias exato (tabela player_alias + nome cadastrado) e,
  falhando isso, sugere candidatos por similaridade (difflib).
- `learn` grava um alias confirmado para que a próxima lista case sozinha.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DEFAULT_CUTOFF = 0.6
DEFAULT_MAX_SUGGESTIONS = 3
# Tokens que não distinguem jogadores ("GK" indica goleiro, não identidade).
_NOISE_TOKENS = {"gk", "goleiro"}
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")


def normalize(text: str) -> str:
    """Chave de comparação: sem acentos, minúsculas, só letras/dígitos e espaços simples."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM_RE.sub(" ", ascii_only.casefold()).strip()


def core(text: str) -> str:
    """Normalização sem tokens de ruído (usada como segundo nível de casamento exato)."""
    tokens = [t for t in normalize(text).split() if t not in _NOISE_TOKENS]
    return " ".join(tokens)


@dataclass(frozen=True)
class Suggestion:
    player_id: int
    name: str
    score: float


@dataclass
class Match:
    query: str
    player_id: int | None = None
    exact: bool = False
    suggestions: list[Suggestion] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.player_id is not None


class Resolver:
    """Índice em memória dos jogadores e aliases de uma conexão."""

    def __init__(
        self,
        players: dict[int, str],
        aliases: dict[str, int] | None = None,
        *,
        cutoff: float = DEFAULT_CUTOFF,
        max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
    ) -> None:
        self.players = dict(players)
        self.aliases = dict(aliases or {})
        self.cutoff = cutoff
        self.max_suggestions = max_suggestions
        self._exact: dict[str, set[int]] = {}
        self._references: dict[str, set[int]] = {}
        self._core: dict[str, set[int]] = {}
        for pid, name in self.players.items():
            key = normalize(name)
            self._exact.setdefault(key, set()).add(pid)
            self._references.setdefault(key, set()).add(pid)
            self._core.setdefault(core(name), set()).add(pid)
        for alias, pid in self.aliases.items():
            self._references.setdefault(normalize(alias), set()).add(pid)
            self._core.setdefault(core(alias), set()).add(pid)

    @classmethod
    def from_db(cls, conn: sqlite3.Connection, **kw) -> Resolver:
        players = {
            r["id"]: r["name"]
            for r in conn.execute(
                "SELECT id, name FROM player WHERE canonical_player_id IS NULL ORDER BY pos"
            )
        }
        aliases = {
            r["alias"]: r["player_id"]
            for r in conn.execute("SELECT alias, player_id FROM player_alias")
        }
        return cls(players, aliases, **kw)

    def exact(self, name: str) -> int | None:
        """Alias gravado, nome idêntico normalizado ou núcleo idêntico e único."""
        key = normalize(name)
        if not key:
            return None
        if (pid := self.aliases.get(key)) is not None:
            return pid
        if len(ids := self._exact.get(key, ())) == 1:
            return next(iter(ids))
        if len(ids := self._core.get(core(name), ())) == 1:
            return next(iter(ids))
        return None

    def canonical(self, name: str) -> int | None:
        """Retorna um jogador apenas para seu nome canônico normalizado e único."""
        ids = self.canonical_candidates(name)
        return next(iter(ids)) if len(ids) == 1 else None

    def canonical_candidates(self, name: str) -> set[int]:
        """Jogadores cujo nome canônico tem a mesma normalização, sem aliases."""
        key = normalize(name)
        ids = self._exact.get(key, ())
        return set(ids) if key else set()

    def reference_candidates(self, reference: str) -> set[int]:
        """Retorna nomes e aliases que correspondem exatamente à referência normalizada.

        Diferente de :meth:`exact`, esta consulta não escolhe silenciosamente
        um alias quando ele colide com o nome de outro jogador. É apropriada
        para operações administrativas, que devem tornar essa ambiguidade
        explícita para a curadoria.
        """
        key = normalize(reference)
        return set(self._references.get(key, ())) if key else set()

    def suggest(self, name: str) -> list[Suggestion]:
        query = core(name) or normalize(name)
        if not query:
            return []
        best: dict[int, float] = {}
        for pid, pname in self.players.items():
            candidates = {normalize(pname), core(pname)}
            candidates |= {a for a, p in self.aliases.items() if p == pid}
            score = max((_similarity(query, c) for c in candidates if c), default=0.0)
            if score >= self.cutoff:
                best[pid] = max(score, best.get(pid, 0.0))
        # empate: mantém a ordem de cadastro (na planilha, os mais frequentes vêm primeiro)
        order = {pid: i for i, pid in enumerate(self.players)}
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], order[kv[0]]))
        return [
            Suggestion(pid, self.players[pid], round(score, 3))
            for pid, score in ranked[: self.max_suggestions]
        ]

    def resolve(self, name: str) -> Match:
        if (pid := self.exact(name)) is not None:
            return Match(query=name, player_id=pid, exact=True)
        return Match(query=name, suggestions=self.suggest(name))


def _similarity(a: str, b: str) -> float:
    ratio = SequenceMatcher(None, a, b).ratio()
    # "gustavo" dentro de "gustavo bastos": prefixo/token completo vale mais que o ratio cru
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if a_tokens and (a_tokens <= b_tokens or b_tokens <= a_tokens):
        ratio = max(ratio, 0.75 + 0.25 * ratio)
    return ratio


def learn(conn: sqlite3.Connection, alias: str, player_id: int) -> bool:
    """Grava o alias normalizado -> jogador. Devolve False se não havia nada novo a aprender."""
    key = normalize(alias)
    if not key:
        return False
    row = conn.execute("SELECT player_id FROM player_alias WHERE alias = ?", (key,)).fetchone()
    if row is not None and row["player_id"] == player_id:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO player_alias (alias, player_id) VALUES (?, ?)", (key, player_id)
    )
    return True


def aliases_of(conn: sqlite3.Connection, player_id: int) -> list[str]:
    return [
        r["alias"]
        for r in conn.execute(
            "SELECT alias FROM player_alias WHERE player_id = ? ORDER BY alias", (player_id,)
        )
    ]
