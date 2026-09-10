import subprocess
import sys

import pytest

from pdq import db


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pdq", *args], capture_output=True, text=True)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "pdq.db"
    conn = db.connect(path)
    conn.executemany(
        "INSERT INTO player (id, pos, classe, name, padrinho) VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, "M", "RODRIGO", ""),
            (2, 2, "F", "SAULO", ""),
            (3, 3, "-", "ZICO (AMIGO RODRIGO)", "Rodrigo"),
            (4, 4, "M", "DANILLO", ""),
        ],
    )
    conn.executemany(
        "INSERT INTO session (id, ordem, date) VALUES (?, ?, ?)",
        [(10, 2, "2025-08-28"), (11, 1, "2025-09-04")],
    )
    conn.executemany(
        "INSERT INTO attendance (player_id, session_id, status) VALUES (?, ?, ?)",
        [
            (1, 10, "X"), (2, 10, "F"), (3, 10, "-"), (4, 10, "X"),
            (1, 11, "X"), (2, 11, "J"), (3, 11, "X"), (4, 11, "F"),
        ],
    )  # fmt: skip
    conn.execute("INSERT INTO player_alias VALUES ('zico', 3)")
    conn.commit()
    conn.close()
    return path


def test_help_lists_finance_commands():
    proc = run("--help")
    assert proc.returncode == 0
    for cmd in ("charges", "pay", "balance"):
        assert cmd in proc.stdout
        sub = run(cmd, "--help")
        assert sub.returncode == 0 and sub.stdout.startswith(f"usage: pdq {cmd}")


def test_charges_defaults_to_latest_session_and_its_month(db_path):
    proc = run("charges", "--db", str(db_path))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Diárias da partida 2025-09-04: 2" in out
    assert "SAULO" in out and "frequente" in out and "R$ 15,00" in out
    assert "ZICO (AMIGO RODRIGO)" in out and "(ref. padrinho: Rodrigo)" in out
    assert "Mensalidades de 2025-09: 2" in out
    assert "RODRIGO" in out and "DANILLO" in out and "R$ 60,00" in out
    assert "total: R$ 150,00" in out


def test_charges_with_explicit_date_month_and_mensalidade(db_path):
    proc = run(
        "charges", "--db", str(db_path), "--date", "2025-08-28", "--mensalidade", "50", "--month",
        "2025-08",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "Diárias da partida 2025-08-28: 0" in proc.stdout  # frequente furou
    assert "Mensalidades de 2025-08: 2" in proc.stdout and "R$ 50,00" in proc.stdout

    proc = run("charges", "--db", str(db_path), "--date", "2025-01-01")
    assert proc.returncode == 2 and "não há partida" in proc.stderr
    proc = run("charges", "--db", str(db_path), "--mensalidade", "abc")
    assert proc.returncode == 2 and "valor inválido" in proc.stderr


def test_charges_all_and_since(db_path):
    proc = run("charges", "--db", str(db_path), "--all")
    assert proc.returncode == 0 and "Cobranças do histórico: 6" in proc.stdout
    assert "total: R$ 270,00" in proc.stdout
    proc = run("charges", "--db", str(db_path), "--all", "--since", "2025-09")
    assert proc.returncode == 0 and "Cobranças do histórico: 4" in proc.stdout


def test_pay_then_balance(db_path):
    proc = run("pay", "--db", str(db_path), "zico", "15", "--on", "2025-09-05", "--ref",
               "2025-09-04", "--note", "pix")  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert (
        "pagamento #1: ZICO (AMIGO RODRIGO) R$ 15,00 em 2025-09-05 ref. 2025-09-04" in proc.stdout
    )
    assert "saldo de ZICO (AMIGO RODRIGO): quitado" in proc.stdout

    proc = run("pay", "--db", str(db_path), "Saulo", "20,00", "--on", "2025-09-05")
    assert proc.returncode == 0 and "crédito de R$ 5,00" in proc.stdout

    proc = run("pay", "--db", str(db_path), "1", "60", "--on", "2025-09-05", "--ref", "2025-08")
    assert proc.returncode == 0 and "pendência de R$ 60,00" in proc.stdout

    proc = run("balance", "--db", str(db_path))
    assert proc.returncode == 0, proc.stderr
    assert "Pendências: 2" in proc.stdout
    assert "RODRIGO" in proc.stdout and "DANILLO" in proc.stdout
    assert "SAULO" not in proc.stdout and "ZICO" not in proc.stdout
    assert "total pendente: R$ 180,00" in proc.stdout

    proc = run("balance", "--db", str(db_path), "--all")
    assert "Saldo por jogador: 4" in proc.stdout
    assert "SAULO" in proc.stdout and "crédito de R$ 5,00" in proc.stdout
    assert "quitado" in proc.stdout

    proc = run("balance", "--db", str(db_path), "Rodrigo")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("RODRIGO (mensalista)")
    assert "Cobranças: 2" in proc.stdout and "2025-08" in proc.stdout and "2025-09" in proc.stdout
    assert "Pagamentos: 1" in proc.stdout and "ref. 2025-08" in proc.stdout
    assert "cobrado R$ 120,00 · pago R$ 60,00 · pendência de R$ 60,00" in proc.stdout

    proc = run("balance", "--db", str(db_path), "Rodrigo", "--since", "2025-09")
    assert "Cobranças: 1" in proc.stdout and "crédito de R$ 0,00" not in proc.stdout
    assert "quitado" in proc.stdout


def test_pay_errors_do_not_write(db_path):
    for args, msg in [
        (("ninguem", "15"), "não encontrado"),
        (("zico", "0"), "positivo"),
        (("zico", "15", "--on", "05/09/2025"), "AAAA-MM-DD"),
        (("zico", "15", "--ref", "setembro"), "inválid"),
        (("99", "15"), "não existe"),
    ]:
        proc = run("pay", "--db", str(db_path), *args)
        assert proc.returncode == 2 and msg in proc.stderr, (args, proc.stderr)
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 0
    conn.close()


def test_charges_without_sessions(tmp_path):
    path = tmp_path / "empty.db"
    db.connect(path).close()
    proc = run("charges", "--db", str(path))
    assert proc.returncode == 2 and "nenhuma partida" in proc.stderr
    proc = run("balance", "--db", str(path))
    assert proc.returncode == 0 and "Pendências: 0" in proc.stdout
