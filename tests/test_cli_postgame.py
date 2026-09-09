import json
import subprocess
import sys
from pathlib import Path

import pytest

from pdq import db

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"


def run(*args, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pdq", *args], capture_output=True, text=True, input=stdin
    )


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "pdq.db"
    conn = db.connect(path)
    conn.executemany(
        "INSERT INTO player (pos, name, posicao) VALUES (?, ?, ?)",
        [
            (1, "RODRIGO", "L"),
            (2, "DANILLO", "L"),
            (3, "MIGUEL", "L"),
            (4, "GUSTAVO BASTOS", "L"),
            (5, "GUILHERME GK", "G"),
            (6, "SAULO", "L"),
            (7, "GUSTAVO OLIVEIRA", "L"),
        ],
    )
    conn.execute("INSERT INTO session (ordem, date, venue) VALUES (1, '2025-08-28', 'Fair Play')")
    conn.commit()
    conn.close()
    return path


def test_help_lists_new_commands():
    for cmd in ("propose", "confirm"):
        proc = run(cmd, "--help")
        assert proc.returncode == 0 and proc.stdout.startswith(f"usage: pdq {cmd}")


def test_propose_to_stdout_with_summary_on_stderr(db_path):
    proc = run("propose", "--db", str(db_path), str(FIXTURES / "lista_com_vagas_e_padrinho.txt"))
    assert proc.returncode == 0, proc.stderr  # nenhuma linha pendente neste banco
    data = json.loads(proc.stdout)
    assert data["schema"] == "pdq.proposal/1"
    assert data["date"].endswith("-09-04") and data["venue"] == "Fair Play"
    assert data["vagas_vazias"] == 3
    novo = next(e for e in data["entries"] if e["raw_name"] == "Joãozinho")
    assert novo["action"] == "create" and novo["new_player"]["padrinho"] == "Danillo"
    assert "Partida" in proc.stderr and "NOVO JOÃOZINHO" in proc.stderr


def test_propose_from_stdin_to_file_and_pending_exit_code(db_path, tmp_path):
    out = tmp_path / "p.json"
    proc = run(
        "propose", "--db", str(db_path), "-", "-o", str(out), "--date", "2025-09-05",
        stdin="1. Gustavo\n2. Rodrigo\n",
    )  # fmt: skip
    assert proc.returncode == 1  # há pendência de revisão
    assert "REVISAR" in proc.stdout and "1 linha(s) pendente(s)" in proc.stdout
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["date"] == "2025-09-05"
    assert [e["action"] for e in data["entries"]] == ["review", "link"]
    assert [s["name"] for s in data["entries"][0]["suggestions"]] == [
        "GUSTAVO BASTOS",
        "GUSTAVO OLIVEIRA",
    ]


def test_propose_quiet_and_errors(db_path):
    proc = run("propose", "--db", str(db_path), "-", "-q", "--date", "2025-09-05", stdin="1. A\n")
    assert proc.returncode == 0 and proc.stderr == ""
    proc = run("propose", "--db", str(db_path), "-", stdin="1. Rodrigo\n")
    assert proc.returncode == 2 and "não traz data" in proc.stderr
    proc = run("propose", "--db", str(db_path), "-", "--date", "05/09/2025", stdin="1. Rodrigo\n")
    assert proc.returncode == 2 and "AAAA-MM-DD" in proc.stderr


def test_confirm_refuses_pending_then_accepts_after_edit(db_path, tmp_path):
    out = tmp_path / "p.json"
    run("propose", "--db", str(db_path), "-", "-o", str(out), "--date", "2025-09-05",
        stdin="Goleiros\n1. Guilherme GK\nLinha\n1. Gustavo\n2. Rodrigo\n3. Zico (conv. Rodrigo)\n"
        "Reservas\n1. Saulo\n")  # fmt: skip

    proc = run("confirm", "--db", str(db_path), str(out))
    assert proc.returncode == 2 and "pendente de revisão" in proc.stderr
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    conn.close()

    data = json.loads(out.read_text(encoding="utf-8"))
    gustavo = next(e for e in data["entries"] if e["raw_name"] == "Gustavo")
    gustavo["action"], gustavo["player_id"] = "link", 4
    zico = next(e for e in data["entries"] if e["raw_name"] == "Zico")
    assert zico["action"] == "create"
    zico["new_player"]["name"] = "ZICO (AMIGO RODRIGO)"
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    proc = run("confirm", "--db", str(db_path), str(out), "--dry-run")
    assert proc.returncode == 0 and "dry-run" in proc.stdout
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    conn.close()

    proc = run("confirm", "--db", str(db_path), str(out))
    assert proc.returncode == 0, proc.stderr
    assert "partida 2025-09-05 gravada" in proc.stdout
    assert "novo jogador: ZICO (AMIGO RODRIGO)" in proc.stdout
    assert "alias aprendido: gustavo" in proc.stdout
    assert "alias aprendido: zico" in proc.stdout

    conn = db.connect(db_path)
    sess = conn.execute("SELECT ordem, date FROM session ORDER BY ordem").fetchall()
    assert [tuple(r) for r in sess] == [(1, "2025-09-05"), (2, "2025-08-28")]
    att = {
        r["name"]: r["status"]
        for r in conn.execute(
            "SELECT p.name, a.status FROM attendance a JOIN player p ON p.id = a.player_id "
            "JOIN session s ON s.id = a.session_id WHERE s.date = '2025-09-05'"
        )
    }
    assert att == {
        "GUILHERME GK": "X",
        "GUSTAVO BASTOS": "X",
        "RODRIGO": "X",
        "ZICO (AMIGO RODRIGO)": "X",
        "SAULO": "J",
    }
    zico_row = conn.execute("SELECT padrinho, posicao FROM player WHERE name LIKE 'ZICO%'")
    assert tuple(zico_row.fetchone()) == ("Rodrigo", "L")
    conn.close()

    # confirmar de novo a mesma proposta: sessão duplicada
    proc = run("confirm", "--db", str(db_path), str(out))
    assert proc.returncode == 2 and "já existe sessão" in proc.stderr


def test_confirm_rejects_invalid_json(db_path, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = run("confirm", "--db", str(db_path), str(bad))
    assert proc.returncode == 2 and "JSON inválido" in proc.stderr
    proc = run("confirm", "--db", str(db_path), "-", stdin='{"date": "2025-01-01"}')
    assert proc.returncode == 2 and "schema" in proc.stderr
