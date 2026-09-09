"""Parser puro da lista de presença colada do WhatsApp.

Formato aceito (tolerante a variações reais):

    ⚽ PDQ - Quinta 28/08/2025 - 20h - Fair Play      <- título: data e local opcionais

    Goleiros:                                        <- seção (goleiros | linha | reservas)
    1- Guilherme GK
    2.                                                <- vaga vazia
    Linha
    1. Rodrigo
    2. Joãozinho (padrinho: Danillo)                  <- padrinho / conv. / convidado de
    3. Amelio - obs: só o primeiro tempo              <- observação livre
    4. Miguel (furo)                                  <- furo explícito
    5. ~Saulo~                                        <- riscado no WhatsApp: furo
    Reservas
    1. Dantas

Linhas numeradas antes de qualquer seção pertencem a "linha". Marcação do
WhatsApp (*negrito*, _itálico_, ~riscado~) é ignorada, exceto ~riscado~, que
marca furo. Nada aqui consulta o banco: o vínculo com jogadores fica em
`pdq.aliases` e a proposta em `pdq.postgame`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_GOALKEEPERS = "goleiros"
SECTION_FIELD = "linha"
SECTION_RESERVES = "reservas"
SECTIONS = (SECTION_GOALKEEPERS, SECTION_FIELD, SECTION_RESERVES)

_SECTION_WORDS = {
    SECTION_GOALKEEPERS: ("goleiro", "goleiros", "gk", "gks", "gol"),
    SECTION_FIELD: ("linha", "jogadores", "titulares", "time", "campo"),
    SECTION_RESERVES: ("reserva", "reservas", "espera", "lista de espera", "suplentes", "banco"),
}

_ENTRY_RE = re.compile(r"^\s*(\d{1,3})\s*[-.):]*\s*(.*?)\s*$")
_EMPTY_SLOT_RE = re.compile(r"^[\s\-_.–—]*$")
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_STRIKE_RE = re.compile(r"~([^~]+)~")
_MARKUP_RE = re.compile(r"[*_`]")
_PAREN_RE = re.compile(r"\(([^()]*)\)")
_PADRINHO_RE = re.compile(
    r"^(?:padrinho|madrinha|conv(?:idado|ite)?\.?|indicad[oa]|trazid[oa])"
    r"(?:\s+(?:do|da|de|por|pelo|pela)\b)?\s*[:\-–]?\s*(.+)$",
    re.IGNORECASE,
)
_FURO_RE = re.compile(r"^(?:furo|furou|furão|desistiu|saiu|não vai|nao vai)\b", re.IGNORECASE)
_OBS_PREFIX_RE = re.compile(r"^(?:obs\.?|observa[cç][aã]o)\s*[:\-]?\s*", re.IGNORECASE)
_TAIL_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
_NON_WORD_EDGE_RE = re.compile(r"^[\W_]+|[\W_]+$")


@dataclass(frozen=True)
class Entry:
    """Uma linha numerada com nome."""

    section: str
    number: int
    name: str
    padrinho: str = ""
    observacao: str = ""
    furo: bool = False
    raw: str = ""


@dataclass
class ParsedList:
    title: str = ""
    day: int | None = None
    month: int | None = None
    year: int | None = None
    entries: list[Entry] = field(default_factory=list)
    empty_slots: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SECTIONS, 0))
    unparsed: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def vagas_vazias(self) -> int:
        return sum(self.empty_slots.values())

    def by_section(self, section: str) -> list[Entry]:
        return [e for e in self.entries if e.section == section]


def _clean_markup(line: str) -> tuple[str, bool]:
    """Remove *_` e detecta ~riscado~ (furo)."""
    struck = False
    if _STRIKE_RE.search(line):
        struck = True
        line = _STRIKE_RE.sub(r"\1", line)
    return _MARKUP_RE.sub("", line), struck


def _section_of(line: str) -> str | None:
    text = _NON_WORD_EDGE_RE.sub("", line).casefold()
    text = re.sub(r"[:\-–—]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    for section, words in _SECTION_WORDS.items():
        if text in words:
            return section
    return None


def _parse_tail(fragment: str, acc: dict) -> None:
    """Classifica um trecho anexo ao nome: padrinho, furo ou observação."""
    fragment = fragment.strip()
    if not fragment:
        return
    if m := _PADRINHO_RE.match(fragment):
        acc["padrinho"] = m.group(1).strip(" .:-")
        return
    if _FURO_RE.match(fragment):
        acc["furo"] = True
        rest = _FURO_RE.sub("", fragment).strip(" :-,;")
        if rest:
            acc["obs"].append(rest)
        return
    acc["obs"].append(_OBS_PREFIX_RE.sub("", fragment).strip())


def _parse_name(rest: str) -> tuple[str, dict]:
    acc: dict = {"padrinho": "", "furo": False, "obs": []}

    def _paren(m: re.Match) -> str:
        _parse_tail(m.group(1), acc)
        return " "

    rest = _PAREN_RE.sub(_paren, rest)
    head, *tails = _TAIL_SPLIT_RE.split(rest)
    for t in tails:
        _parse_tail(t, acc)
    # "Nome obs: ..." sem separador
    if m := re.search(r"\s(?=(?:obs\.?|observa[cç][aã]o)\s*[:\-])", head, re.IGNORECASE):
        _parse_tail(head[m.start() :], acc)
        head = head[: m.start()]
    name = re.sub(r"\s+", " ", head).strip(" -–—.:,;")
    return name, acc


def parse_title(title: str, parsed: ParsedList) -> None:
    parsed.title = title
    if m := _DATE_RE.search(title):
        parsed.day, parsed.month = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            parsed.year = y + 2000 if y < 100 else y


def parse(text: str) -> ParsedList:
    parsed = ParsedList(raw_text=text)
    section: str | None = None
    saw_title = False

    for raw_line in text.splitlines():
        line, struck = _clean_markup(raw_line)
        if not line.strip():
            continue

        if (found := _section_of(line)) is not None:
            section = found
            continue

        m = _ENTRY_RE.match(line)
        if m is None:
            if not saw_title and section is None and not parsed.entries:
                parse_title(line.strip(), parsed)
                saw_title = True
            else:
                parsed.unparsed.append(raw_line.strip())
            continue

        current = section or SECTION_FIELD
        number, rest = int(m.group(1)), m.group(2)
        if _EMPTY_SLOT_RE.match(rest):
            parsed.empty_slots[current] += 1
            continue

        name, acc = _parse_name(rest)
        if not name:
            parsed.unparsed.append(raw_line.strip())
            continue
        parsed.entries.append(
            Entry(
                section=current,
                number=number,
                name=name,
                padrinho=acc["padrinho"],
                observacao="; ".join(o for o in acc["obs"] if o),
                furo=struck or acc["furo"],
                raw=raw_line.strip(),
            )
        )
    return parsed
