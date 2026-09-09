"""Interface de linha de comando: python -m pdq <comando>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pdq import __version__, backup, db, exporter, importer, validate

LEGACY_CSV_DEFAULT = "legacy/Pdq - Frequencia - Historico.csv"


def _add_db_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="caminho do banco SQLite")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdq", description=__doc__)
    parser.add_argument("--version", action="version", version=f"pdq {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="cria o banco vazio com o schema")
    _add_db_arg(p)

    p = sub.add_parser("import-legacy", help="importa a planilha legada (substitui o conteúdo)")
    p.add_argument("csv", nargs="?", default=LEGACY_CSV_DEFAULT)
    _add_db_arg(p)

    p = sub.add_parser("export-legacy", help="exporta no formato da planilha legada")
    p.add_argument("-o", "--output", help="arquivo de saída (padrão: stdout)")
    p.add_argument(
        "--strict-legacy-quirk",
        action="store_true",
        help="reproduz a fórmula desatualizada de Presenças (bytes idênticos ao original)",
    )
    _add_db_arg(p)

    p = sub.add_parser("validate-legacy", help="importa, exporta e compara com o CSV original")
    p.add_argument("csv", nargs="?", default=LEGACY_CSV_DEFAULT)

    p = sub.add_parser("backup", help="copia data/ para backups/<data-hora>/ com manifest")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--backups-dir", default=str(backup.DEFAULT_BACKUPS_DIR))

    p = sub.add_parser("restore", help="recria data/ a partir de um backup")
    p.add_argument("backup_dir", nargs="?", help="diretório do backup (padrão: mais recente)")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--backups-dir", default=str(backup.DEFAULT_BACKUPS_DIR))

    p = sub.add_parser("verify-backup", help="confere hashes de um backup")
    p.add_argument("backup_dir", nargs="?")
    p.add_argument("--backups-dir", default=str(backup.DEFAULT_BACKUPS_DIR))
    return parser


def cmd_init_db(args) -> int:
    db.connect(args.db).close()
    print(f"banco pronto em {args.db}")
    return 0


def cmd_import_legacy(args) -> int:
    conn = db.connect(args.db)
    try:
        res = importer.import_legacy_csv(conn, args.csv)
    finally:
        conn.close()
    print(f"importados {res.players} jogadores, {res.sessions} sessões, {res.attendance} presenças")
    return 0


def cmd_export_legacy(args) -> int:
    conn = db.connect(args.db)
    try:
        data = exporter.export_legacy_csv(
            conn, args.output, strict_legacy_quirk=args.strict_legacy_quirk
        )
    finally:
        conn.close()
    if args.output is None:
        sys.stdout.buffer.write(data)
    else:
        print(f"exportado para {args.output} ({len(data)} bytes)")
    return 0


def cmd_validate_legacy(args) -> int:
    report = validate.validate_roundtrip(args.csv)
    print(f"bytes idênticos (modo estrito): {'sim' if report.bytes_identical else 'não'}")
    print(f"diferenças (modo estrito): {len(report.strict_diffs)}")
    for d in report.strict_diffs[:50]:
        print("  " + d.describe())
    print(
        f"células isoladas pela fórmula desatualizada de Presenças: {len(report.recomputed_diffs)}"
    )
    return 0 if report.ok else 1


def cmd_backup(args) -> int:
    target = backup.create_backup(args.data_dir, args.backups_dir)
    n = len(backup.Manifest.load(target / backup.MANIFEST_NAME).files)
    print(f"backup criado em {target} ({n} arquivos)")
    return 0


def _resolve_backup(args) -> Path | None:
    if args.backup_dir:
        return Path(args.backup_dir)
    return backup.latest_backup(args.backups_dir)


def cmd_restore(args) -> int:
    target = _resolve_backup(args)
    if target is None:
        print("nenhum backup encontrado", file=sys.stderr)
        return 1
    restored = backup.restore_backup(target, args.data_dir)
    print(f"restaurados {len(restored)} arquivos de {target} para {args.data_dir}")
    return 0


def cmd_verify_backup(args) -> int:
    target = _resolve_backup(args)
    if target is None:
        print("nenhum backup encontrado", file=sys.stderr)
        return 1
    bad = backup.verify_backup(target)
    if bad:
        print(f"backup {target} corrompido: {bad}", file=sys.stderr)
        return 1
    print(f"backup {target} íntegro")
    return 0


COMMANDS = {
    "init-db": cmd_init_db,
    "import-legacy": cmd_import_legacy,
    "export-legacy": cmd_export_legacy,
    "validate-legacy": cmd_validate_legacy,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "verify-backup": cmd_verify_backup,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
