import pytest

from pdq import aliases, db
from pdq.aliases import Resolver, core, normalize


@pytest.mark.parametrize(
    "text, expected",
    [
        ("NAALDI - GK", "naaldi gk"),
        ("Naaldi GK", "naaldi gk"),
        ("André Tomé", "andre tome"),
        ("  Gustavo   Bastos ", "gustavo bastos"),
        ("JADER (BRUNO)", "jader bruno"),
        ("Zé.Vitor!", "ze vitor"),
        ("", ""),
        ("---", ""),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected


def test_core_drops_noise_tokens():
    assert core("NAALDI - GK") == "naaldi"
    assert core("Guilherme GK") == "guilherme"
    assert core("Goleiro Zé") == "ze"


@pytest.fixture
def resolver():
    players = {
        1: "RODRIGO",
        2: "GUSTAVO BASTOS",
        3: "GUILHERME GK",
        4: "NAALDI - GK",
        5: "GUSTAVO OLIVEIRA",
        6: "JOSE VITOR",
        7: "ANDRE TOME",
    }
    return Resolver(players, {"ze vitor": 6})


def test_exact_by_normalized_name(resolver):
    assert resolver.exact("rodrigo") == 1
    assert resolver.exact("André Tomé") == 7
    assert resolver.exact("Guilherme GK") == 3


def test_exact_by_core_when_unique(resolver):
    assert resolver.exact("Naaldi") == 4  # "NAALDI - GK" sem o token GK
    assert resolver.exact("Guilherme") == 3


def test_exact_by_alias(resolver):
    assert resolver.exact("Zé Vitor") == 6
    assert resolver.exact("ZE VITOR") == 6


def test_exact_refuses_ambiguity(resolver):
    assert resolver.exact("Gustavo") is None
    assert resolver.exact("") is None
    assert resolver.exact("Ninguém") is None


def test_canonical_requires_a_unique_normalized_player_name(resolver):
    assert resolver.canonical("André Tomé") == 7
    assert resolver.canonical("Zé Vitor") is None  # só existe como alias
    assert Resolver({1: "GUSTAVO", 2: "Gustavo"}).canonical("Gustavo") is None


def test_suggest_ranks_by_similarity(resolver):
    s = resolver.suggest("Gustavo")
    assert [x.player_id for x in s] == [2, 5]  # empate: ordem de cadastro
    assert all(x.score >= aliases.DEFAULT_CUTOFF for x in s)
    s = resolver.suggest("Gustavo Bastoss")
    assert s[0].player_id == 2 and s[0].score > 0.9


def test_suggest_uses_aliases_and_cutoff(resolver):
    assert [x.player_id for x in resolver.suggest("Ze Vitor")] == [6]
    assert resolver.suggest("xyzxyz") == []
    assert resolver.suggest("") == []
    strict = Resolver(resolver.players, cutoff=0.99)
    assert strict.suggest("Rodrigu") == []


def test_suggest_limits_results():
    players = {i: f"BRUNO {i}" for i in range(1, 10)}
    assert len(Resolver(players).suggest("Bruno")) == aliases.DEFAULT_MAX_SUGGESTIONS
    assert len(Resolver(players, max_suggestions=5).suggest("Bruno")) == 5


def test_resolve_returns_match(resolver):
    m = resolver.resolve("Rodrigo")
    assert m.resolved and m.exact and m.player_id == 1 and m.suggestions == []
    m = resolver.resolve("Gustavo")
    assert not m.resolved and not m.exact and [s.player_id for s in m.suggestions] == [2, 5]
    assert m.query == "Gustavo"


def test_from_db_and_learn(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'JOSE VITOR'), (2, 'RODRIGO')")
    conn.commit()
    assert Resolver.from_db(conn).exact("Zé Vitor") is None

    assert aliases.learn(conn, "Zé Vitor", 1) is True
    assert aliases.learn(conn, "Zé Vitor", 1) is False  # já sabia
    assert aliases.learn(conn, "  ", 1) is False
    conn.commit()
    assert Resolver.from_db(conn).exact("ze vitor") == 1
    assert aliases.aliases_of(conn, 1) == ["ze vitor"]

    # correção: o mesmo apelido passa a apontar para outro jogador
    assert aliases.learn(conn, "Zé Vitor", 2) is True
    assert Resolver.from_db(conn).exact("Zé Vitor") == 2
    assert aliases.aliases_of(conn, 1) == []
    conn.close()


def test_from_db_ignores_merged_players(tmp_path):
    conn = db.connect(tmp_path / "pdq.db")
    conn.execute("INSERT INTO player (pos, name) VALUES (1, 'Carlos Silva'), (2, 'C. Silva')")
    conn.execute("UPDATE player SET canonical_id = 1 WHERE id = 2")
    conn.commit()
    resolver = Resolver.from_db(conn)
    assert resolver.exact("C. Silva") is None
    assert [suggestion.player_id for suggestion in resolver.suggest("C. Silva")] == [1]
    conn.close()
