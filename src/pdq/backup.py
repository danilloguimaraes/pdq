"""Backup datado e restauração do diretório data/ (banco + fotos).

Cada backup é um diretório `backups/<YYYYMMDD-HHMMSS>/` contendo uma cópia
fiel de `data/` em `data/` e um `manifest.json` com o SHA-256 de cada arquivo.
O banco SQLite é copiado via API de backup para garantir consistência.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_BACKUPS_DIR = Path("backups")
MANIFEST_NAME = "manifest.json"
PAYLOAD_DIR = "data"
MANIFEST_VERSION = 1


@dataclass
class Manifest:
    created_at: str
    source: str
    files: dict[str, str]  # caminho relativo -> sha256

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": MANIFEST_VERSION,
                "created_at": self.created_at,
                "source": self.source,
                "files": dict(sorted(self.files.items())),
            },
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(created_at=raw["created_at"], source=raw["source"], files=raw["files"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _is_sqlite(src):
        source = sqlite3.connect(src)
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    else:
        shutil.copy2(src, dst)


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.endswith(("-journal", "-wal", "-shm")):
            yield p


def create_backup(
    data_dir: str | Path = "data",
    backups_dir: str | Path = DEFAULT_BACKUPS_DIR,
    *,
    now: datetime | None = None,
) -> Path:
    """Copia data/ para backups/<timestamp>/ e escreve o manifest. Retorna o diretório."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"diretório de dados não encontrado: {data_dir}")
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    target = Path(backups_dir) / stamp
    suffix = 1
    while target.exists():
        target = Path(backups_dir) / f"{stamp}-{suffix}"
        suffix += 1
    payload = target / PAYLOAD_DIR
    payload.mkdir(parents=True)

    files: dict[str, str] = {}
    for src in _iter_files(data_dir):
        rel = src.relative_to(data_dir)
        dst = payload / rel
        _copy_file(src, dst)
        files[rel.as_posix()] = sha256_file(dst)

    manifest = Manifest(created_at=now.isoformat(), source=str(data_dir), files=files)
    (target / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
    return target


def verify_backup(backup_dir: str | Path) -> list[str]:
    """Retorna a lista de arquivos cujo hash difere do manifest (vazia se íntegro)."""
    backup_dir = Path(backup_dir)
    manifest = Manifest.load(backup_dir / MANIFEST_NAME)
    payload = backup_dir / PAYLOAD_DIR
    bad = []
    for rel, digest in manifest.files.items():
        p = payload / rel
        if not p.is_file() or sha256_file(p) != digest:
            bad.append(rel)
    return bad


def latest_backup(backups_dir: str | Path = DEFAULT_BACKUPS_DIR) -> Path | None:
    backups_dir = Path(backups_dir)
    if not backups_dir.is_dir():
        return None
    candidates = sorted(p for p in backups_dir.iterdir() if (p / MANIFEST_NAME).is_file())
    return candidates[-1] if candidates else None


def restore_backup(backup_dir: str | Path, data_dir: str | Path = "data") -> list[str]:
    """Recria data/ a partir do backup, verificando hashes. Retorna os arquivos restaurados."""
    backup_dir = Path(backup_dir)
    data_dir = Path(data_dir)
    corrupted = verify_backup(backup_dir)
    if corrupted:
        raise ValueError(f"backup corrompido, hashes divergentes: {corrupted}")
    manifest = Manifest.load(backup_dir / MANIFEST_NAME)
    payload = backup_dir / PAYLOAD_DIR

    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    restored = []
    for rel in manifest.files:
        dst = data_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload / rel, dst)
        if sha256_file(dst) != manifest.files[rel]:
            raise ValueError(f"falha ao restaurar {rel}: hash divergente após cópia")
        restored.append(rel)
    return restored
