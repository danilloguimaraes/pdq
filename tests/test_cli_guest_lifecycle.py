import subprocess
import sys

import pytest

from pdq import db


def run(*args):
    return subprocess.run([sys.executable, "-m", "pdq", *args], capture_output=True, text=True)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "pdq.db"
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO player (pos, classe, name, guest_status) VALUES (1, 'C', 'Beto', 'pending')"
    )
    for i in range(4):
        conn.execute(
            "INSERT INTO session (ordem, date) VALUES (?, ?)", (i + 1, f"2025-09-0{i + 1}")
        )
        conn.execute("INSERT INTO attendance VALUES (1, ?, 'X', '', '')", (i + 1,))
    conn.commit()
    conn.close()
    return path


def test_help_and_empty_queue(tmp_path):
    for command in ("guest-queue", "promote-guest", "decline-guest"):
        assert run(command, "--help").returncode == 0
    proc = run("guest-queue", "--db", str(tmp_path / "empty.db"))
    assert proc.returncode == 0 and "nenhum convidado" in proc.stdout


def test_queue_promote_and_decline(db_path):
    proc = run("guest-queue", "--db", str(db_path))
    assert proc.returncode == 0 and "Beto [1]" in proc.stdout
    proc = run("promote-guest", "--db", str(db_path), "1", "M", "2025-09-10")
    assert proc.returncode == 0 and "promovido a M" in proc.stdout
    proc = run("promote-guest", "--db", str(db_path), "Beto", "F", "2025-09-10")
    assert proc.returncode == 2 and "não é convidado pendente" in proc.stderr


def test_decline_by_alias(db_path):
    conn = db.connect(db_path)
    conn.execute("INSERT INTO player_alias VALUES ('betinho', 1)")
    conn.commit()
    conn.close()
    proc = run("decline-guest", "--db", str(db_path), "Betinho", "2025-09-10", "--leaves")
    assert proc.returncode == 0 and "saiu do grupo" in proc.stdout
