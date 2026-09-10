import pytest

from pdq import correction, db, finance


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "pdq.db")
    c.executemany(
        "INSERT INTO player (id, pos, classe, name, padrinho) VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, "M", "MENSAL", ""),
            (2, 2, "F", "FREQ", ""),
            (3, 3, "-", "CONV", "Freq"),
            (4, 4, "", "CONV SEM CLASSE", ""),
            (5, 5, "M", "MENSAL NOVO", ""),
            (6, 6, "M", "MENSAL SEM REGISTRO", ""),
        ],
    )
    c.executemany(
        "INSERT INTO session (id, ordem, date) VALUES (?, ?, ?)",
        [(10, 3, "2025-08-28"), (11, 2, "2025-09-04"), (12, 1, "2025-09-11")],
    )
    c.executemany(
        "INSERT INTO attendance (player_id, session_id, status) VALUES (?, ?, ?)",
        [
            (1, 10, "X"), (2, 10, "X"), (3, 10, "-"), (4, 10, "-"), (5, 10, "-"),
            (1, 11, "F"), (2, 11, "F"), (3, 11, "X"), (4, 11, "J"), (5, 11, "X"),
            (1, 12, "X"), (2, 12, "J"), (3, 12, "F"), (4, 12, "-"), (5, 12, "X"),
        ],
    )  # fmt: skip
    c.commit()
    yield c
    c.close()


def test_charges_for_session_diarias_only_for_present_non_mensalistas(conn):
    charges = finance.charges_for_session(conn, "2025-09-04")
    assert [(c.player_name, c.kind, c.amount_cents) for c in charges] == [
        ("CONV", "diaria", 1500),
        ("CONV SEM CLASSE", "diaria", 1500),
    ]
    assert all(c.ref == "2025-09-04" for c in charges)
    # frequente com furo não paga; mensalista presente não paga diária
    assert [c.player_name for c in finance.charges_for_session(conn, "2025-08-28")] == ["FREQ"]
    assert [c.player_name for c in finance.charges_for_session(conn, "2025-09-11")] == ["FREQ"]
    assert finance.charges_for_session(conn, "2030-01-01") == []


def test_convidado_owes_his_own_diaria_with_padrinho_as_reference(conn):
    """ADR 0001: a diária recai sobre o convidado; padrinho é só referência."""
    conv = next(c for c in finance.charges_for_session(conn, "2025-09-04") if c.player_id == 3)
    assert conv.padrinho == "Freq" and conv.amount_cents == 1500
    assert not any(c.player_name == "FREQ" for c in finance.charges_for_session(conn, "2025-09-04"))
    freq = finance.charges_for_session(conn, "2025-08-28")[0]
    assert freq.padrinho == ""  # frequente não exibe padrinho


def test_charges_for_month_mensalidades(conn):
    aug = finance.charges_for_month(conn, "2025-08")
    assert [(c.player_name, c.kind, c.ref, c.amount_cents) for c in aug] == [
        ("MENSAL", "mensalidade", "2025-08", finance.MENSALIDADE_CENTAVOS)
    ]
    sep = finance.charges_for_month(conn, "2025-09", mensalidade_cents=5000)
    assert [(c.player_name, c.amount_cents) for c in sep] == [
        ("MENSAL", 5000),
        ("MENSAL NOVO", 5000),
    ]
    assert finance.charges_for_month(conn, "2025-10") == []  # sem partida no mês
    with pytest.raises(finance.FinanceError):
        finance.charges_for_month(conn, "2025/09")


def test_all_charges_and_balances(conn):
    charges = finance.all_charges(conn)
    kinds = {}
    for c in charges:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    assert kinds == {"diaria": 4, "mensalidade": 3}

    bal = {b.player_name: b for b in finance.balances(conn)}
    assert bal["MENSAL"].charged_cents == 2 * finance.MENSALIDADE_CENTAVOS
    assert bal["MENSAL NOVO"].charged_cents == finance.MENSALIDADE_CENTAVOS
    assert bal["FREQ"].charged_cents == 3000
    assert bal["CONV"].charged_cents == 1500
    assert "MENSAL SEM REGISTRO" not in bal  # sem histórico nem pagamento

    # início da contabilidade: ignora agosto
    sept = finance.all_charges(conn, since="2025-09")
    assert all(c.ref >= "2025-09" for c in sept) and len(sept) == 3 + 2
    bal = {b.player_name: b for b in finance.balances(conn, since="2025-09")}
    assert bal["MENSAL"].charged_cents == finance.MENSALIDADE_CENTAVOS
    assert bal["FREQ"].charged_cents == 1500
    assert finance.balance_of(conn, 2, since="2025-09").saldo_cents == 1500
    with pytest.raises(finance.FinanceError):
        finance.all_charges(conn, since="2025")


def test_record_payment_and_balance(conn):
    p = finance.record_payment(conn, 2, 1500, "2025-09-05", ref="2025-09-11", note="pix")
    assert p.id and p.player_name == "FREQ" and p.ref == "2025-09-11"
    finance.record_payment(conn, 2, 3000, "2025-09-06")
    b = finance.balance_of(conn, 2)
    assert (b.charged_cents, b.paid_cents, b.saldo_cents) == (3000, 4500, -1500)  # crédito
    assert [x.amount_cents for x in finance.payments(conn, 2)] == [1500, 3000]
    assert len(finance.payments(conn)) == 2

    pending = [b.player_name for b in finance.balances(conn, only_pending=True)]
    assert pending == ["MENSAL", "CONV", "CONV SEM CLASSE", "MENSAL NOVO"]
    assert finance.balance_of(conn, 6).saldo_cents == 0


def test_correction_updates_derived_charges_but_keeps_payments(conn):
    """Cobrança é derivada: corrigir a presença corrige o saldo; pagamentos ficam."""
    finance.record_payment(conn, 2, 1500, "2025-09-05", ref="2025-09-11")
    assert finance.balance_of(conn, 2).saldo_cents == 1500  # FREQ: 3 diárias - 1 paga

    # furo -> presença: FREQ passa a dever a diária de 04/09
    correction.set_status(conn, "2025-09-04", 2, db.STATUS_PRESENT)
    assert finance.balance_of(conn, 2).saldo_cents == 3000

    # a presença de 28/08 era de outro jogador (relink): a cobrança muda de dono, o pagamento não
    correction.relink(conn, "2025-08-28", 2, 6)
    assert finance.balance_of(conn, 2).saldo_cents == 1500
    # mensalista que passa a ter registro em agosto deve as mensalidades de ago e set
    assert finance.balance_of(conn, 6).charged_cents == 2 * finance.MENSALIDADE_CENTAVOS
    assert [x.amount_cents for x in finance.payments(conn, 2)] == [1500]

    # excluir a partida remove as cobranças dela; agosto deixa de ter mensalidade
    correction.delete_session(conn, "2025-08-28", confirm=True)
    assert finance.balance_of(conn, 6).charged_cents == 0
    assert finance.balance_of(conn, 1).charged_cents == finance.MENSALIDADE_CENTAVOS
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 1


def test_record_payment_validation(conn):
    with pytest.raises(finance.FinanceError):
        finance.record_payment(conn, 2, 0, "2025-09-05")
    with pytest.raises(finance.FinanceError):
        finance.record_payment(conn, 2, 100, "05/09/2025")
    with pytest.raises(finance.FinanceError):
        finance.record_payment(conn, 2, 100, "2025-09-05", ref="setembro")
    with pytest.raises(finance.FinanceError):
        finance.record_payment(conn, 99, 100, "2025-09-05")
    assert conn.execute("SELECT COUNT(*) FROM payment").fetchone()[0] == 0


def test_find_player_by_id_name_or_alias(conn):
    assert finance.find_player(conn, "3")["name"] == "CONV"
    assert finance.find_player(conn, "conv sem classe")["id"] == 4
    assert finance.find_player(conn, "Mensál")["id"] == 1
    conn.execute("INSERT INTO player_alias VALUES ('freqzinho', 2)")
    assert finance.find_player(conn, "Freqzinho")["id"] == 2
    with pytest.raises(finance.FinanceError, match="não encontrado"):
        finance.find_player(conn, "ninguem")
    with pytest.raises(finance.FinanceError, match="não existe"):
        finance.find_player(conn, "99")
    conn.execute("INSERT INTO player (id, pos, name) VALUES (7, 7, 'conv')")
    with pytest.raises(finance.FinanceError, match="ambíguo"):
        finance.find_player(conn, "CONV")


@pytest.mark.parametrize(
    "text, cents",
    [("15", 1500), ("15,50", 1550), ("R$ 15.5", 1550), (" 60 ", 6000), ("0,05", 5)],
)
def test_parse_brl(text, cents):
    assert finance.parse_brl(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "-5", "0", "1,234", "15,"])
def test_parse_brl_rejects(text):
    with pytest.raises(finance.FinanceError):
        finance.parse_brl(text)


def test_fmt_brl():
    assert finance.fmt_brl(1500) == "R$ 15,00"
    assert finance.fmt_brl(5) == "R$ 0,05"
    assert finance.fmt_brl(-1550) == "-R$ 15,50"


def test_classe_helpers():
    assert finance.classe_label("M") == "mensalista"
    assert finance.classe_label("F") == "frequente"
    assert finance.classe_label("-") == finance.classe_label("") == "convidado"
