import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from pdq import backup, db, exporter, importer


@pytest.fixture
def seeded_data(tmp_path, legacy_csv):
    data_dir = tmp_path / "data"
    photos = data_dir / "photos" / "2025"
    photos.mkdir(parents=True)
    conn = db.connect(data_dir / "pdq.db")
    importer.import_legacy_csv(conn, legacy_csv)
    conn.close()
    (photos / "danillo.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    (data_dir / ".gitkeep").write_bytes(b"")
    return data_dir


def test_backup_creates_dated_dir_with_manifest(seeded_data, tmp_path):
    now = datetime(2026, 9, 9, 12, 30, 45, tzinfo=UTC)
    target = backup.create_backup(seeded_data, tmp_path / "backups", now=now)
    assert target == tmp_path / "backups" / "20260909-123045"
    manifest = json.loads((target / "manifest.json").read_text())
    assert set(manifest["files"]) == {".gitkeep", "pdq.db", "photos/2025/danillo.jpg"}
    for rel, digest in manifest["files"].items():
        assert backup.sha256_file(target / "data" / rel) == digest
    # colisão de timestamp gera sufixo em vez de sobrescrever
    second = backup.create_backup(seeded_data, tmp_path / "backups", now=now)
    assert second.name == "20260909-123045-1"
    assert backup.latest_backup(tmp_path / "backups") == second


def test_rm_rf_data_is_recoverable(seeded_data, tmp_path, legacy_csv):
    photo_hash = backup.sha256_file(seeded_data / "photos" / "2025" / "danillo.jpg")
    target = backup.create_backup(seeded_data, tmp_path / "backups")
    manifest = backup.Manifest.load(target / "manifest.json")

    shutil.rmtree(seeded_data)  # simula `rm -rf data/`
    assert not seeded_data.exists()

    restored = backup.restore_backup(target, seeded_data)
    assert sorted(restored) == sorted(manifest.files)
    for rel, digest in manifest.files.items():
        assert backup.sha256_file(seeded_data / rel) == digest
    assert backup.sha256_file(seeded_data / "photos" / "2025" / "danillo.jpg") == photo_hash

    conn = db.connect(seeded_data / "pdq.db")
    try:
        assert exporter.export_legacy_csv(conn, strict_legacy_quirk=True) == legacy_csv.read_bytes()
    finally:
        conn.close()


def test_restore_refuses_corrupted_backup(seeded_data, tmp_path):
    target = backup.create_backup(seeded_data, tmp_path / "backups")
    (target / "data" / "photos" / "2025" / "danillo.jpg").write_bytes(b"tampered")
    assert backup.verify_backup(target) == ["photos/2025/danillo.jpg"]
    with pytest.raises(ValueError, match="corrompido"):
        backup.restore_backup(target, seeded_data)


def test_backup_copies_sqlite_consistently_while_open(seeded_data, tmp_path):
    conn = sqlite3.connect(seeded_data / "pdq.db")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO player (pos, name) VALUES (999, 'NAO COMMITADO')")
    target = backup.create_backup(seeded_data, tmp_path / "backups")
    conn.rollback()
    conn.close()
    copy = sqlite3.connect(target / "data" / "pdq.db")
    assert copy.execute("SELECT COUNT(*) FROM player WHERE pos = 999").fetchone()[0] == 0
    assert copy.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 146
    copy.close()


def test_cli_backup_and_restore(seeded_data, tmp_path):
    backups_dir = tmp_path / "backups"
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "pdq", *a], capture_output=True, text=True
    )
    proc = run("backup", "--data-dir", str(seeded_data), "--backups-dir", str(backups_dir))
    assert proc.returncode == 0, proc.stderr
    shutil.rmtree(seeded_data)
    proc = run("restore", "--data-dir", str(seeded_data), "--backups-dir", str(backups_dir))
    assert proc.returncode == 0, proc.stderr
    assert (seeded_data / "pdq.db").is_file()
    proc = run("verify-backup", "--backups-dir", str(backups_dir))
    assert proc.returncode == 0, proc.stderr
