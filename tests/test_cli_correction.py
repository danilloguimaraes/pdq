import subprocess
import sys

import pytest

from pdq import db

DATE = "2025-09-04"


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pdq", *args], capture_output=True, text=True)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "pdq.db"
    conn = db.connect(path)
    conn.executemany(
        "INSERT INTO player (pos, name, posicao) VALUES (?, ?, ?)",
        [
            (1, "RODRIGO", "L"),
            (2, "DANILLO", "L"),
            (3, "GUSTAVO BASTOS", "L"),
            (4, "GUSTAVO OLIVEIRA", "L"),
            (5, "SAULO", "L"),
        ],
    )
    conn.executemany(
        "INSERT INTO session (ordem, date, venue) VALUES (?, ?, ?)",
        [(1, DATE, "Fair Play"), (2, "2025-08-28", "Fair Play")],
    )
    conn.executemany(
        "INSERT INTO attendance (player_id, session_id, status, section) VALUES (?, ?, ?, ?)",
        [(1, 1, "X", "linha"), (2, 1, "F", "linha"), (3, 1, "X", "linha"), (5, 1, "J", "reservas")],
    )
    conn.commit()
    conn.close()
    return path


def query(db_path, sql):
    conn = db.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_help_lists_correction_commands():
    for cmd in (
        "show-session",
        "relink",
        "set-status",
        "set-section",
        "set-date",
        "set-venue",
        "delete-session",
    ):
        proc = run(cmd, "--help")
        assert proc.returncode == 0 and proc.stdout.startswith(f"usage: pdq {cmd}")


def test_show_session(db_path):
    proc = run("show-session", "--db", str(db_path), DATE)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("Partida 2025-09-04 @ Fair Play (ordem 1)")
    assert "F lin DANILLO" in proc.stdout and "J res SAULO" in proc.stdout
    proc = run("show-session", "--db", str(db_path), "2025-01-01")
    assert proc.returncode == 2 and "não existe sessão" in proc.stderr


def test_relink_with_alias(db_path):
    proc = run(
        "relink", "--db", str(db_path), DATE, "Gustavo Bastos", "4", "--alias", "Gustavo"
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "GUSTAVO BASTOS -> GUSTAVO OLIVEIRA" in proc.stdout
    assert "alias aprendido: gustavo" in proc.stdout
    assert query(db_path, "SELECT player_id FROM attendance WHERE session_id=1 ORDER BY 1") == [
        (1,),
        (2,),
        (4,),
        (5,),
    ]
    assert query(db_path, "SELECT player_id FROM player_alias WHERE alias='gustavo'") == [(4,)]
    proc = run("relink", "--db", str(db_path), DATE, "Rodrigo", "Danillo")
    assert proc.returncode == 2 and "já tem registro" in proc.stderr


def test_set_status_and_section(db_path):
    proc = run("set-status", "--db", str(db_path), DATE, "Danillo", "X")
    assert proc.returncode == 0 and "DANILLO F -> X" in proc.stdout
    proc = run("set-status", "--db", str(db_path), DATE, "Saulo", "-")
    assert proc.returncode == 0 and "SAULO J -> -" in proc.stdout
    proc = run("set-status", "--db", str(db_path), DATE, "Saulo", "Z")
    assert proc.returncode == 2 and "invalid choice" in proc.stderr
    proc = run("set-section", "--db", str(db_path), DATE, "Saulo", "linha")
    assert proc.returncode == 0 and "SAULO reservas -> linha" in proc.stdout
    assert query(db_path, "SELECT status, section FROM attendance WHERE player_id=5") == [
        ("-", "linha")
    ]


def test_set_date_and_venue(db_path):
    proc = run("set-date", "--db", str(db_path), DATE, "2025-08-21")
    assert proc.returncode == 0 and "movida para 2025-08-21" in proc.stdout
    assert query(db_path, "SELECT ordem, date FROM session ORDER BY ordem") == [
        (1, "2025-08-28"),
        (2, "2025-08-21"),
    ]
    proc = run("set-date", "--db", str(db_path), "2025-08-21", "2025-08-28")
    assert proc.returncode == 2 and "já existe sessão" in proc.stderr
    proc = run("set-venue", "--db", str(db_path), "2025-08-21", "Bora Bola")
    assert proc.returncode == 0 and "'Fair Play' -> 'Bora Bola'" in proc.stdout


def test_delete_session_requires_yes(db_path):
    proc = run("delete-session", "--db", str(db_path), DATE)
    assert proc.returncode == 1
    assert "Partida 2025-09-04" in proc.stdout and "--yes" in proc.stderr
    assert query(db_path, "SELECT COUNT(*) FROM session") == [(2,)]

    proc = run("delete-session", "--db", str(db_path), DATE, "--yes")
    assert proc.returncode == 0 and "excluída (4 presenças removidas)" in proc.stdout
    assert query(db_path, "SELECT ordem, date FROM session") == [(1, "2025-08-28")]
    assert query(db_path, "SELECT COUNT(*) FROM attendance") == [(0,)]
    assert query(db_path, "SELECT COUNT(*) FROM player") == [(5,)]

    proc = run("delete-session", "--db", str(db_path), DATE, "--yes")
    assert proc.returncode == 2 and "não existe sessão" in proc.stderr
