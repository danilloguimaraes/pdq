from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_CSV = REPO_ROOT / "legacy" / "Pdq - Frequencia - Historico.csv"


@pytest.fixture(scope="session")
def legacy_csv() -> Path:
    assert LEGACY_CSV.is_file(), f"planilha legada ausente: {LEGACY_CSV}"
    return LEGACY_CSV


@pytest.fixture
def imported_db(tmp_path, legacy_csv):
    from pdq import db, importer

    path = tmp_path / "pdq.db"
    conn = db.connect(path)
    importer.import_legacy_csv(conn, legacy_csv)
    yield conn, path
    conn.close()
