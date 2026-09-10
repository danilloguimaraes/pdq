import json
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
    conn.executemany(
        "INSERT INTO player (pos, name) VALUES (?, ?)",
        [(1, "Bruno"), (2, "Bruno Silva"), (3, "Zico (AMIGO Bruno)")],
    )
    conn.commit()
    conn.close()
    return path


def linked_padrinho(db_path):
    conn = db.connect(db_path)
    try:
        return conn.execute("SELECT padrinho_id FROM player WHERE id = 3").fetchone()[0]
    finally:
        conn.close()


def test_report_is_available_stable_and_never_writes(db_path):
    assert "hygiene-report" in run("--help").stdout
    first = run("hygiene-report", "--db", str(db_path), "--threshold", "0.9")
    second = run("hygiene-report", "--db", str(db_path), "--threshold", "0.9")
    assert first.returncode == 0
    assert first.stdout == second.stdout
    assert "candidatos não são aplicados automaticamente" in first.stdout
    assert "1 Bruno (1.000)" in first.stdout
    assert linked_padrinho(db_path) is None


def test_apply_previews_then_persists_only_with_yes(db_path, tmp_path):
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"decisions": [{"player_id": 3, "padrinho_id": 1}]}))
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions))
    assert proc.returncode == 1
    assert "nada aplicado" in proc.stderr
    assert linked_padrinho(db_path) is None
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions), "--yes")
    assert proc.returncode == 0
    assert linked_padrinho(db_path) == 1


def test_apply_rejects_invalid_or_ambiguous_decisions_without_writing(db_path, tmp_path):
    decisions = tmp_path / "bad.json"
    decisions.write_text(json.dumps({"decisions": [{"player_id": 3, "padrinho_id": 99}]}))
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions), "--yes")
    assert proc.returncode == 2 and "referência inválida" in proc.stderr
    decisions.write_text(
        json.dumps(
            {
                "decisions": [
                    {"player_id": 3, "padrinho_id": 1},
                    {"player_id": 3, "padrinho_id": 2},
                ]
            }
        )
    )
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions), "--yes")
    assert proc.returncode == 2 and "referência ambígua" in proc.stderr
    assert linked_padrinho(db_path) is None


def test_merge_command_is_available_and_requires_valid_ids(db_path):
    proc = run("merge", "--db", str(db_path), "3", "1")
    assert proc.returncode == 0
    assert "Zico (AMIGO Bruno) [3] mesclado em Bruno [1]" in proc.stdout
    proc = run("merge", "--db", str(db_path), "3", "1")
    assert proc.returncode == 2 and "já foi mesclada" in proc.stderr
