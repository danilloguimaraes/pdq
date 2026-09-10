"""Interface de linha de comando: python -m pdq <comando>."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from pdq import (
    __version__,
    backup,
    correction,
    db,
    exporter,
    finance,
    guest_lifecycle,
    hygiene,
    importer,
    postgame,
    validate,
)

LEGACY_CSV_DEFAULT = "legacy/Pdq - Frequencia - Historico.csv"


def _add_db_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="caminho do banco SQLite")


def _add_finance_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--mensalidade",
        default=finance.fmt_brl(finance.MENSALIDADE_CENTAVOS).removeprefix("R$ "),
        help="valor da mensalidade (padrão: %(default)s)",
    )
    p.add_argument(
        "--since", metavar="AAAA-MM", help="início da contabilidade: ignora cobranças anteriores"
    )


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

    p = sub.add_parser(
        "hygiene-report",
        help="lista referências legadas de padrinho para revisão",
        description=(
            "Mostra referências de padrinho preservadas nos nomes legados e candidatos por "
            "similaridade. Candidatos de duplicidade não são inferências automáticas nem "
            "alteram o banco."
        ),
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=hygiene.DEFAULT_SIMILARITY_CUTOFF,
        help="limiar de similaridade, de 0 a 1 (padrão: %(default)s)",
    )
    _add_db_arg(p)

    p = sub.add_parser(
        "hygiene-apply",
        help="mostra ou aplica decisões revisadas de padrinho",
        description=(
            "Lê JSON com {'decisions': [{'player_id': 2, 'padrinho_id': 1}]}. Sem --yes "
            "mostra somente a prévia; candidatos nunca são aplicados automaticamente."
        ),
    )
    p.add_argument("decisions", help="arquivo JSON de decisões ('-' para stdin)")
    p.add_argument("--yes", action="store_true", help="confirma a gravação das decisões")
    _add_db_arg(p)

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

    p = sub.add_parser("guest-queue", help="lista convidados com 4 presenças aguardando decisão")
    _add_db_arg(p)

    p = sub.add_parser("promote-guest", help="promove convidado pendente a frequente ou mensalista")
    p.add_argument("player", metavar="JOGADOR", help="id, nome exato ou alias")
    p.add_argument("classe", choices=(db.CLASS_FREQUENT, db.CLASS_MONTHLY))
    p.add_argument("date", metavar="DATA", help="data da decisão AAAA-MM-DD")
    _add_db_arg(p)

    p = sub.add_parser("decline-guest", help="registra a recusa de um convidado pendente")
    p.add_argument("player", metavar="JOGADOR", help="id, nome exato ou alias")
    p.add_argument("date", metavar="DATA", help="data da decisão AAAA-MM-DD")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep-guest", action="store_true", help="permanece como convidado")
    group.add_argument("--leaves", action="store_true", help="registra que saiu do grupo")
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

    # --- financeiro (E4) ---------------------------------------------------
    p = sub.add_parser(
        "charges",
        help="lista as cobranças de uma partida (diárias) e do mês (mensalidades)",
        description=(
            "Deriva as cobranças de presença + classe: diária de R$ 15 para frequentes e "
            "convidados que jogaram (X/J) e mensalidade para mensalistas no mês. A diária do "
            "convidado é dele; o padrinho aparece como referência (ADR 0001)."
        ),
    )
    p.add_argument("--date", help="data da partida AAAA-MM-DD (padrão: a mais recente)")
    p.add_argument("--month", help="mês AAAA-MM das mensalidades (padrão: o mês da partida)")
    p.add_argument("--all", action="store_true", help="todas as cobranças do histórico")
    _add_finance_args(p)
    _add_db_arg(p)

    p = sub.add_parser(
        "pay",
        help="registra um pagamento de um jogador",
        description="JOGADOR pode ser o id, o nome da planilha ou um alias aprendido.",
    )
    p.add_argument("jogador", help="id, nome ou alias do jogador")
    p.add_argument("valor", help="valor pago (15 ou 15,50)")
    p.add_argument("--on", dest="paid_on", help="data do pagamento AAAA-MM-DD (padrão: hoje)")
    p.add_argument("--ref", default="", help="a que se refere: partida AAAA-MM-DD ou mês AAAA-MM")
    p.add_argument("--note", default="", help="observação (pix, dinheiro, ...)")
    _add_db_arg(p)

    p = sub.add_parser(
        "balance",
        help="saldo por jogador: cobrado, pago e pendência",
        description=(
            "Sem JOGADOR lista quem tem pendência (saldo positivo); com --all inclui quitados "
            "e créditos. Com JOGADOR detalha cobranças e pagamentos dele."
        ),
    )
    p.add_argument("jogador", nargs="?", help="id, nome ou alias do jogador")
    p.add_argument("--all", action="store_true", help="inclui quem está quitado ou com crédito")
    _add_finance_args(p)
    _add_db_arg(p)

    # --- correção de partida (E2) -------------------------------------------
    p = sub.add_parser("show-session", help="mostra uma partida gravada e suas presenças")
    p.add_argument("date", help="data da partida AAAA-MM-DD")
    _add_db_arg(p)

    p = sub.add_parser(
        "merge",
        help="mescla uma linha legada em uma identidade canônica",
        description=(
            "Preserva presenças e a linha legada de ORIGEM, mas passa seus aliases para "
            "CANONICO. Ambos os argumentos devem ser player_id explícitos."
        ),
    )
    p.add_argument("source", metavar="ORIGEM", type=int, help="player_id da linha a mesclar")
    p.add_argument(
        "canonical", metavar="CANONICO", type=int, help="player_id da identidade canônica"
    )
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


def cmd_hygiene_report(args) -> int:
    conn = db.connect(args.db)
    try:
        reviews = hygiene.padrinho_report(conn, cutoff=args.threshold)
    except hygiene.HygieneError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(hygiene.render_padrinho_report(reviews))
    return 0


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def cmd_hygiene_apply(args) -> int:
    conn = db.connect(args.db)
    try:
        decisions = hygiene.decisions_from_json(_read_text(args.decisions))
        links = hygiene.validate_padrinho_decisions(conn, decisions)
        for link in links:
            print(f"  {link.player_name} [{link.player_id}] -> padrinho [{link.padrinho_id}]")
        if not args.yes:
            print("nada aplicado: repita com --yes para confirmar", file=sys.stderr)
            return 1
        hygiene.apply_padrinho_decisions(conn, decisions)
    except (OSError, hygiene.HygieneError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(f"{len(links)} vínculo(s) de padrinho aplicado(s)")
    return 0


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


def _charge_line(c: finance.Charge) -> str:
    kind = "diária" if c.kind == finance.KIND_DIARIA else "mensalidade"
    valor = finance.fmt_brl(c.amount_cents)
    line = f"  {c.player_name:<30} {finance.classe_label(c.classe):<11} {kind:<11} {valor:>10}"
    if c.padrinho:
        line += f"   (ref. padrinho: {c.padrinho})"
    return line


def cmd_charges(args) -> int:
    conn = db.connect(args.db)
    try:
        mensalidade = finance.parse_brl(args.mensalidade)
        if args.all:
            charges = finance.all_charges(conn, mensalidade, args.since)
            print(f"Cobranças do histórico: {len(charges)}")
            for c in charges:
                print(f"  {c.ref}" + _charge_line(c)[1:])
            print(f"  total: {finance.fmt_brl(sum(c.amount_cents for c in charges))}")
            return 0
        date_iso = args.date or finance.latest_session_date(conn)
        if date_iso is None:
            print("erro: nenhuma partida registrada", file=sys.stderr)
            return 2
        if not conn.execute("SELECT 1 FROM session WHERE date = ?", (date_iso,)).fetchone():
            print(f"erro: não há partida em {date_iso}", file=sys.stderr)
            return 2
        month = args.month or date_iso[:7]
        diarias = finance.charges_for_session(conn, date_iso)
        mensalidades = finance.charges_for_month(conn, month, mensalidade)
    except finance.FinanceError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(f"Diárias da partida {date_iso}: {len(diarias)}")
    for c in diarias:
        print(_charge_line(c))
    print(f"Mensalidades de {month}: {len(mensalidades)}")
    for c in mensalidades:
        print(_charge_line(c))
    total = sum(c.amount_cents for c in diarias + mensalidades)
    print(f"total: {finance.fmt_brl(total)}")
    return 0


def cmd_pay(args) -> int:
    conn = db.connect(args.db)
    try:
        player = finance.find_player(conn, args.jogador)
        cents = finance.parse_brl(args.valor)
        paid_on = args.paid_on or date.today().isoformat()
        pay = finance.record_payment(conn, player["id"], cents, paid_on, args.ref, args.note)
        saldo = finance.balance_of(conn, player["id"]).saldo_cents
    except finance.FinanceError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    ref = f" ref. {pay.ref}" if pay.ref else ""
    print(
        f"pagamento #{pay.id}: {pay.player_name} {finance.fmt_brl(pay.amount_cents)} "
        f"em {pay.paid_on}{ref}"
    )
    print(f"saldo de {pay.player_name}: {_saldo_label(saldo)}")
    return 0


def _saldo_label(saldo: int) -> str:
    if saldo > 0:
        return f"pendência de {finance.fmt_brl(saldo)}"
    if saldo < 0:
        return f"crédito de {finance.fmt_brl(-saldo)}"
    return "quitado"


def cmd_balance(args) -> int:
    conn = db.connect(args.db)
    try:
        mensalidade = finance.parse_brl(args.mensalidade)
        if args.jogador:
            player = finance.find_player(conn, args.jogador)
            pid = player["id"]
            charges = [
                c for c in finance.all_charges(conn, mensalidade, args.since) if c.player_id == pid
            ]
            pays = finance.payments(conn, pid)
            bal = finance.balance_of(conn, pid, mensalidade, args.since)
        else:
            rows = finance.balances(conn, mensalidade, not args.all, args.since)
    except finance.FinanceError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.jogador:
        print(f"{bal.player_name} ({finance.classe_label(bal.classe)})")
        print(f"Cobranças: {len(charges)}")
        for c in charges:
            print(f"  {c.ref}" + _charge_line(c)[1:])
        print(f"Pagamentos: {len(pays)}")
        for p in pays:
            extra = " ".join(x for x in (f"ref. {p.ref}" if p.ref else "", p.note) if x)
            print(f"  {p.paid_on} {finance.fmt_brl(p.amount_cents):>10}   {extra}".rstrip())
        print(
            f"cobrado {finance.fmt_brl(bal.charged_cents)} · pago {finance.fmt_brl(bal.paid_cents)}"
            f" · {_saldo_label(bal.saldo_cents)}"
        )
        return 0

    title = "Saldo por jogador" if args.all else "Pendências"
    print(f"{title}: {len(rows)}")
    for b in rows:
        print(
            f"  {b.player_name:<30} {finance.classe_label(b.classe):<11} "
            f"cobrado {finance.fmt_brl(b.charged_cents):>10}  "
            f"pago {finance.fmt_brl(b.paid_cents):>10}  {_saldo_label(b.saldo_cents)}"
        )
    pend = sum(b.saldo_cents for b in rows if b.saldo_cents > 0)
    print(f"total pendente: {finance.fmt_brl(pend)}")
    return 0


def _guest_lifecycle(args, fn):
    conn = db.connect(args.db)
    try:
        return fn(conn)
    except guest_lifecycle.GuestLifecycleError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def cmd_guest_queue(args) -> int:
    return _guest_lifecycle(
        args,
        lambda conn: (
            print(guest_lifecycle.render_pending(guest_lifecycle.pending_guests(conn))),
            0,
        )[1],
    )


def cmd_promote_guest(args) -> int:
    def run(conn):
        result = guest_lifecycle.promote(conn, args.player, args.classe, args.date)
        print(
            f"{result.name} [{result.player_id}] promovido a {result.classe} "
            f"em {result.decision_date}"
        )
        return 0

    return _guest_lifecycle(args, run)


def cmd_decline_guest(args) -> int:
    def run(conn):
        result = guest_lifecycle.decline(conn, args.player, args.keep_guest, args.date)
        decision = "permanece convidado" if args.keep_guest else "saiu do grupo"
        print(f"recusa de {result.name} [{result.player_id}] em {result.decision_date}: {decision}")
        return 0

    return _guest_lifecycle(args, run)


def _correct(args, fn):
    """Executa uma correção com tratamento uniforme de erro e conexão."""
    conn = db.connect(args.db)
    try:
        return fn(conn)
    except (correction.CorrectionError, hygiene.HygieneError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def cmd_show_session(args) -> int:
    def run(conn):
        print(correction.render_session(correction.show_session(conn, args.date)))
        return 0

    return _correct(args, run)


def cmd_merge(args) -> int:
    def run(conn):
        res = hygiene.merge(conn, args.source, args.canonical)
        print(
            f"mescla: {res.source_name} [{res.source_id}] -> "
            f"{res.canonical_name} [{res.canonical_id}]"
        )
        print(f"  presenças legadas preservadas: {res.attendance_count}")
        print(f"  aliases redirecionados: {', '.join(res.redirected_aliases) or 'nenhum'}")
        print(f"  aliases aprendidos: {', '.join(res.learned_aliases) or 'nenhum'}")
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
    "hygiene-report": cmd_hygiene_report,
    "hygiene-apply": cmd_hygiene_apply,
    "propose": cmd_propose,
    "confirm": cmd_confirm,
    "charges": cmd_charges,
    "pay": cmd_pay,
    "balance": cmd_balance,
    "guest-queue": cmd_guest_queue,
    "promote-guest": cmd_promote_guest,
    "decline-guest": cmd_decline_guest,
    "show-session": cmd_show_session,
    "merge": cmd_merge,
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
