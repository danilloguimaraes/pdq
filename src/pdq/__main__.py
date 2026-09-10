"""Interface de linha de comando: python -m pdq <comando>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pdq import __version__, backup, correction, db, exporter, importer, postgame, validate

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

    p = sub.add_parser(
        "propose",
        help="lê a lista do WhatsApp e gera uma proposta de presenças (JSON editável)",
        description=(
            "Interpreta a lista colada do WhatsApp (arquivo ou '-' para stdin), vincula os "
            "nomes aos jogadores e propõe X (presença), F (furo) ou J (reserva que jogou). "
            "Linhas não reconhecidas ficam como action=review e devem ser editadas no JSON "
            "antes de 'pdq confirm'."
        ),
    )
    p.add_argument("lista", help="arquivo com a lista colada ('-' lê da entrada padrão)")
    p.add_argument("-o", "--output", help="arquivo JSON da proposta (padrão: stdout)")
    p.add_argument("--date", help="data da partida AAAA-MM-DD (padrão: extraída da lista)")
    p.add_argument("--venue", help="local (padrão: detectado no título ou o da última sessão)")
    p.add_argument("-q", "--quiet", action="store_true", help="não imprime o resumo")
    _add_db_arg(p)

    p = sub.add_parser(
        "confirm",
        help="grava a partida a partir de uma proposta JSON revisada",
        description=(
            "Valida a proposta (sem pendências, sem sessão duplicada) e grava sessão, "
            "presenças, jogadores novos e aliases numa única transação."
        ),
    )
    p.add_argument("proposta", help="arquivo JSON gerado por 'pdq propose' ('-' para stdin)")
    p.add_argument(
        "--dry-run", action="store_true", help="só valida e mostra o resumo; não grava nada"
    )
    _add_db_arg(p)

    # --- correção de partida (E2) -------------------------------------------
    p = sub.add_parser("show-session", help="mostra uma partida gravada e suas presenças")
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    _add_db_arg(p)

    p = sub.add_parser(
        "relink",
        help="troca o jogador vinculado a uma presença",
        description=(
            "Move a presença de JOGADOR_ERRADO para JOGADOR_CERTO na partida, preservando "
            "status, seção e observação. Jogadores podem ser id, nome exato ou alias. "
            "Com --alias, a grafia da lista passa a apontar para o jogador certo."
        ),
    )
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    p.add_argument("old", metavar="JOGADOR_ERRADO")
    p.add_argument("new", metavar="JOGADOR_CERTO")
    p.add_argument("--alias", help="grafia usada na lista, para aprender o vínculo correto")
    _add_db_arg(p)

    p = sub.add_parser(
        "set-status",
        help="alterna presença (X), furo (F), jogou (J) ou não jogou (-)",
        description=(
            "Corrige o status do jogador na partida. '-' significa que estava na lista "
            "(em geral nas reservas) mas não jogou; a linha e sua seção são preservadas."
        ),
    )
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    p.add_argument("player", metavar="JOGADOR", help="id, nome exato ou alias")
    p.add_argument("status", choices=db.STATUSES)
    _add_db_arg(p)

    p = sub.add_parser("set-section", help="corrige a seção da lista (goleiros/linha/reservas)")
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    p.add_argument("player", metavar="JOGADOR", help="id, nome exato ou alias")
    p.add_argument("section", choices=db.SECTIONS)
    _add_db_arg(p)

    p = sub.add_parser("set-date", help="move a partida para outra data (renumera a ordem)")
    p.add_argument("date", help="data atual AAAA-MM-DD")
    p.add_argument("new_date", metavar="NOVA_DATA", help="nova data AAAA-MM-DD")
    _add_db_arg(p)

    p = sub.add_parser("set-venue", help="corrige o local da partida")
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    p.add_argument("venue", metavar="LOCAL")
    _add_db_arg(p)

    p = sub.add_parser(
        "delete-session",
        help="exclui a partida e suas presenças (exige --yes)",
        description=(
            "Remove a sessão, suas presenças e match_meta; jogadores e aliases ficam. "
            "Sem --yes apenas mostra o que seria excluído."
        ),
    )
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    p.add_argument("--yes", action="store_true", help="confirma a exclusão")
    _add_db_arg(p)
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


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def cmd_propose(args) -> int:
    text = _read_text(args.lista)
    conn = db.connect(args.db)
    try:
        proposal = postgame.build_proposal(conn, text, date_iso=args.date, venue=args.venue)
    except postgame.ProposalError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    payload = proposal.to_json()
    if args.output is None:
        sys.stdout.write(payload)
        summary_stream = sys.stderr
    else:
        Path(args.output).write_text(payload, encoding="utf-8")
        summary_stream = sys.stdout
        print(f"proposta gravada em {args.output}", file=summary_stream)
    if not args.quiet:
        print(postgame.render_summary(proposal), file=summary_stream)
    return 1 if proposal.pending() else 0


def cmd_confirm(args) -> int:
    conn = db.connect(args.db)
    try:
        proposal = postgame.Proposal.from_json(_read_text(args.proposta))
        if args.dry_run:
            postgame.validate(conn, proposal)
            print(postgame.render_summary(proposal))
            print("proposta válida (dry-run: nada gravado)")
            return 0
        res = postgame.confirm(conn, proposal)
    except postgame.ProposalError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(
        f"partida {res.date} gravada (sessão {res.session_id}): {res.attendance} presenças, "
        f"{len(res.created_players)} jogadores novos, {len(res.learned_aliases)} aliases"
    )
    for name in res.created_players:
        print(f"  novo jogador: {name}")
    for alias in res.learned_aliases:
        print(f"  alias aprendido: {alias}")
    return 0


def _correct(args, fn):
    """Executa uma correção com tratamento uniforme de erro e conexão."""
    conn = db.connect(args.db)
    try:
        return fn(conn)
    except correction.CorrectionError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def cmd_show_session(args) -> int:
    def run(conn):
        print(correction.render_session(correction.show_session(conn, args.date)))
        return 0

    return _correct(args, run)


def cmd_relink(args) -> int:
    def run(conn):
        res = correction.relink(conn, args.date, args.old, args.new, alias=args.alias)
        print(f"partida {res.date}: {res.old_player} -> {res.new_player}")
        if res.learned_alias:
            print(f"  alias aprendido: {res.learned_alias}")
        return 0

    return _correct(args, run)


def cmd_set_status(args) -> int:
    def run(conn):
        res = correction.set_status(conn, args.date, args.player, args.status)
        print(f"partida {res.date}: {res.player} {res.old_status} -> {res.new_status}")
        return 0

    return _correct(args, run)


def cmd_set_section(args) -> int:
    def run(conn):
        res = correction.set_section(conn, args.date, args.player, args.section)
        print(f"partida {res.date}: {res.player} {res.old_section or '?'} -> {res.new_section}")
        return 0

    return _correct(args, run)


def cmd_set_date(args) -> int:
    def run(conn):
        res = correction.set_date(conn, args.date, args.new_date)
        print(f"partida {res.old_date} movida para {res.new_date} (sessões renumeradas)")
        return 0

    return _correct(args, run)


def cmd_set_venue(args) -> int:
    def run(conn):
        res = correction.set_venue(conn, args.date, args.venue)
        print(f"partida {res.new_date}: local {res.old_venue!r} -> {res.new_venue!r}")
        return 0

    return _correct(args, run)


def cmd_delete_session(args) -> int:
    def run(conn):
        view = correction.show_session(conn, args.date)
        if not args.yes:
            print(correction.render_session(view))
            print("nada excluído: repita com --yes para confirmar", file=sys.stderr)
            return 1
        res = correction.delete_session(conn, args.date, confirm=True)
        print(f"partida {res.date} excluída ({res.attendance} presenças removidas)")
        return 0

    return _correct(args, run)


COMMANDS = {
    "init-db": cmd_init_db,
    "import-legacy": cmd_import_legacy,
    "export-legacy": cmd_export_legacy,
    "validate-legacy": cmd_validate_legacy,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "verify-backup": cmd_verify_backup,
    "propose": cmd_propose,
    "confirm": cmd_confirm,
    "show-session": cmd_show_session,
    "relink": cmd_relink,
    "set-status": cmd_set_status,
    "set-section": cmd_set_section,
    "set-date": cmd_set_date,
    "set-venue": cmd_set_venue,
    "delete-session": cmd_delete_session,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
