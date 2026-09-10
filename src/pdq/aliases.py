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

from pdq import db

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
        canonical_player_ids: dict[int, int | None] | None = None,
        *,
        cutoff: float = DEFAULT_CUTOFF,
        max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
    ) -> None:
        all_players = dict(players)
        canonical_player_ids = dict(canonical_player_ids or {})
        canonical = {
            pid: _canonical_id(pid, all_players, canonical_player_ids) for pid in all_players
        }
        self.players = {pid: name for pid, name in all_players.items() if canonical[pid] == pid}
        self.aliases: dict[str, int] = {}
        self.cutoff = cutoff
        self.max_suggestions = max_suggestions
        self._exact: dict[str, set[int]] = {}
        self._canonical_exact: dict[str, set[int]] = {}
        self._core: dict[str, set[int]] = {}
        self._candidates: dict[int, set[str]] = {pid: set() for pid in self.players}
        for pid, name in self.players.items():
            self._canonical_exact.setdefault(normalize(name), set()).add(pid)
        for pid, name in all_players.items():
            if (target := canonical[pid]) is not None:
                self._add_evidence(name, target)
        for alias, pid in (aliases or {}).items():
            if (target := canonical.get(pid)) is not None:
                key = normalize(alias)
                if key:
                    self.aliases[key] = target
                self._add_evidence(alias, target)

    @classmethod
    def from_db(cls, conn: sqlite3.Connection, **kw) -> Resolver:
        players = {
            r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM player ORDER BY pos")
        }
        canonical_player_ids = {
            r["id"]: r["canonical_player_id"]
            for r in conn.execute("SELECT id, canonical_player_id FROM player ORDER BY pos")
        }
        aliases = {
            r["alias"]: r["player_id"]
            for r in conn.execute("SELECT alias, player_id FROM player_alias")
        }
        return cls(players, aliases, canonical_player_ids, **kw)

    def _add_evidence(self, value: str, player_id: int) -> None:
        exact = normalize(value)
        if exact:
            self._exact.setdefault(exact, set()).add(player_id)
            self._candidates[player_id].add(exact)
        key = core(value)
        if key:
            self._core.setdefault(key, set()).add(player_id)
            self._candidates[player_id].add(key)

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
        ids = self._canonical_exact.get(key, ())
        return set(ids) if key else set()

    def suggest(self, name: str) -> list[Suggestion]:
        query = core(name) or normalize(name)
        if not query:
            return []
        best: dict[int, float] = {}
        for pid, candidates in self._candidates.items():
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


def _canonical_id(
    player_id: int, players: dict[int, str], canonical_player_ids: dict[int, int | None]
) -> int | None:
    seen: set[int] = set()
    current = player_id
    while current not in seen:
        seen.add(current)
        if current not in players:
            return None
        target = canonical_player_ids.get(current)
        if target is None:
            return current
        current = target
    return None


def learn(conn: sqlite3.Connection, alias: str, player_id: int) -> bool:
    """Grava o alias normalizado -> jogador. Devolve False se não havia nada novo a aprender."""
    key = normalize(alias)
    canonical_id = db.canonical_player_id(conn, player_id)
    if not key or canonical_id is None:
        return False
    row = conn.execute("SELECT player_id FROM player_alias WHERE alias = ?", (key,)).fetchone()
    if row is not None and db.canonical_player_id(conn, row["player_id"]) == canonical_id:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO player_alias (alias, player_id) VALUES (?, ?)", (key, canonical_id)
    )
    return True


def aliases_of(conn: sqlite3.Connection, player_id: int) -> list[str]:
    canonical_id = db.canonical_player_id(conn, player_id)
    if canonical_id is None:
        return []
    return [
        r["alias"]
        for r in conn.execute("SELECT alias, player_id FROM player_alias ORDER BY alias")
        if db.canonical_player_id(conn, r["player_id"]) == canonical_id
    ]
