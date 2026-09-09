from pathlib import Path

import pytest

from pdq import whatsapp
from pdq.whatsapp import SECTION_FIELD, SECTION_GOALKEEPERS, SECTION_RESERVES

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"


def load(name: str) -> whatsapp.ParsedList:
    return whatsapp.parse((FIXTURES / name).read_text(encoding="utf-8"))


def test_lista_basica_sections_and_title():
    p = load("lista_basica.txt")
    assert (p.day, p.month, p.year) == (28, 8, 2025)
    assert "Fair Play" in p.title
    assert [e.name for e in p.by_section(SECTION_GOALKEEPERS)] == ["Guilherme GK", "Naaldi"]
    linha = p.by_section(SECTION_FIELD)
    assert len(linha) == 10
    assert [e.number for e in linha] == list(range(1, 11))
    assert linha[3].name == "Gustavo Bastos"
    assert p.by_section(SECTION_RESERVES) == []
    assert p.vagas_vazias == 0
    assert p.unparsed == []
    assert not any(e.furo or e.padrinho or e.observacao for e in p.entries)


def test_lista_com_vagas_e_padrinho():
    p = load("lista_com_vagas_e_padrinho.txt")
    assert (p.day, p.month, p.year) == (4, 9, None)
    assert p.empty_slots == {SECTION_GOALKEEPERS: 1, SECTION_FIELD: 2, SECTION_RESERVES: 0}
    assert p.vagas_vazias == 3
    by_name = {e.name: e for e in p.entries}
    assert by_name["Joãozinho"].padrinho == "Danillo"
    assert by_name["Pedrão"].padrinho == "Rodrigo"
    assert by_name["Rodrigo"].padrinho == ""
    assert [e.number for e in p.by_section(SECTION_FIELD)] == [1, 2, 3, 4, 5, 8]


def test_lista_com_reservas_obs_e_furos():
    p = load("lista_com_reservas_e_obs.txt")
    assert p.title == "PDQ 11/09/2025 - Bora Bola"  # marcação *negrito* removida
    by_name = {e.name: e for e in p.entries}
    assert by_name["Guilherme GK"].observacao == "chega 20h30"
    assert by_name["Amelio"].observacao == "só o primeiro tempo"
    assert by_name["Miguel"].furo is True and by_name["Miguel"].observacao == ""
    assert by_name["Saulo"].furo is True  # ~riscado~
    assert [e.name for e in p.by_section(SECTION_RESERVES)] == ["Dantas", "Andre Tome"]
    assert by_name["Dantas"].raw == "1 - Dantas"


@pytest.mark.parametrize(
    "line, expected",
    [
        ("1- Rodrigo", "Rodrigo"),
        ("1. Rodrigo", "Rodrigo"),
        ("1) Rodrigo", "Rodrigo"),
        ("1 - Rodrigo", "Rodrigo"),
        ("01: Rodrigo", "Rodrigo"),
        ("12 Rodrigo Silva", "Rodrigo Silva"),
        ("3 -  Rodrigo   Silva ", "Rodrigo Silva"),
    ],
)
def test_numbering_styles(line, expected):
    p = whatsapp.parse(line)
    assert [e.name for e in p.entries] == [expected]
    assert p.entries[0].section == SECTION_FIELD  # sem seção declarada -> linha


@pytest.mark.parametrize("line", ["1.", "2-", "3 -", "4. -", "5) ___", "6"])
def test_empty_slots(line):
    p = whatsapp.parse(f"Linha\n{line}")
    assert p.entries == []
    assert p.empty_slots[SECTION_FIELD] == 1


@pytest.mark.parametrize(
    "header, section",
    [
        ("Goleiros", SECTION_GOALKEEPERS),
        ("GOLEIROS:", SECTION_GOALKEEPERS),
        ("🧤 Goleiro", SECTION_GOALKEEPERS),
        ("*GK*", SECTION_GOALKEEPERS),
        ("Linha", SECTION_FIELD),
        ("Jogadores -", SECTION_FIELD),
        ("Reservas", SECTION_RESERVES),
        ("Lista de espera:", SECTION_RESERVES),
        ("_Suplentes_", SECTION_RESERVES),
    ],
)
def test_section_headers(header, section):
    p = whatsapp.parse(f"{header}\n1. Fulano")
    assert p.entries[0].section == section


@pytest.mark.parametrize(
    "line, padrinho",
    [
        ("1. João (padrinho: Danillo)", "Danillo"),
        ("1. João (padrinho Danillo)", "Danillo"),
        ("1. João - conv. Danillo", "Danillo"),
        ("1. João (convidado do Danillo)", "Danillo"),
        ("1. João - convite Danillo", "Danillo"),
        ("1. João (indicado pelo Danillo)", "Danillo"),
    ],
)
def test_padrinho_variants(line, padrinho):
    e = whatsapp.parse(line).entries[0]
    assert (e.name, e.padrinho, e.observacao) == ("João", padrinho, "")


def test_obs_and_furo_variants():
    p = whatsapp.parse(
        "1. A - obs: chega tarde\n"
        "2. B (só 1º tempo)\n"
        "3. C obs: leva bola\n"
        "4. D (furo)\n"
        "5. E - furou, sem carro\n"
        "6. ~F~\n"
        "7. G (padrinho: A) (chega 21h)\n"
    )
    e = {x.name: x for x in p.entries}
    assert e["A"].observacao == "chega tarde"
    assert e["B"].observacao == "só 1º tempo"
    assert e["C"].observacao == "leva bola"
    assert e["D"].furo and e["D"].observacao == ""
    assert e["E"].furo and e["E"].observacao == "sem carro"
    assert e["F"].furo and e["F"].name == "F"
    assert e["G"].padrinho == "A" and e["G"].observacao == "chega 21h"


def test_unparsed_lines_are_reported_not_lost():
    p = whatsapp.parse("PDQ 01/01\nLinha\n1. A\nquem for levar bola avisa\n2. B\n")
    assert [e.name for e in p.entries] == ["A", "B"]
    assert p.unparsed == ["quem for levar bola avisa"]
    assert (p.day, p.month) == (1, 1)


def test_two_digit_year_and_no_date():
    assert whatsapp.parse("Pdq 5/9/25").year == 2025
    p = whatsapp.parse("Pelada de quinta")
    assert (p.day, p.month, p.year) == (None, None, None)
    assert p.title == "Pelada de quinta"


def test_empty_text():
    p = whatsapp.parse("")
    assert p.entries == [] and p.vagas_vazias == 0 and p.title == ""
