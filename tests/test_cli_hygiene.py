import json
import subprocess
import sys

import pytest

from pdq import db


def run(*args, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "pdq", *args], capture_output=True, text=True, input=stdin
    )


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


def test_help_and_report_threshold_are_available(db_path):
    assert "hygiene-report" in run("--help").stdout
    for command in ("hygiene-report", "hygiene-apply"):
        assert run(command, "--help").returncode == 0
    proc = run("hygiene-report", "--db", str(db_path), "--threshold", "0.9")
    assert proc.returncode == 0
    assert "candidatos não são aplicados automaticamente" in proc.stdout
    assert "1 Bruno (1.000)" in proc.stdout
    assert "2 Bruno Silva" not in proc.stdout


def test_report_order_is_stable_and_never_writes(db_path):
    first = run("hygiene-report", "--db", str(db_path)).stdout
    second = run("hygiene-report", "--db", str(db_path)).stdout
    assert first == second
    assert first.index("1 Bruno") < first.index("2 Bruno Silva")
    assert linked_padrinho(db_path) is None


def test_apply_previews_then_persists_only_with_yes(db_path, tmp_path):
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"decisions": [{"player_id": 3, "padrinho_id": 1}]}))
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions))
    assert proc.returncode == 1 and "nada aplicado" in proc.stderr
    assert "Zico (AMIGO Bruno) [3] -> padrinho [1]" in proc.stdout
    assert linked_padrinho(db_path) is None
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions), "--yes")
    assert proc.returncode == 0 and "1 vínculo(s)" in proc.stdout
    assert linked_padrinho(db_path) == 1


def test_apply_rejects_invalid_or_ambiguous_decisions_without_writing(db_path, tmp_path):
    decisions = tmp_path / "bad.json"
    decisions.write_text(json.dumps({"decisions": [{"player_id": 3, "padrinho_id": 99}]}))
    proc = run("hygiene-apply", "--db", str(db_path), str(decisions), "--yes")
    assert proc.returncode == 2 and "erro: referência inválida" in proc.stderr
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
    assert proc.returncode == 2 and "erro: referência ambígua" in proc.stderr
    assert linked_padrinho(db_path) is None
