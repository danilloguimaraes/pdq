"""Formato da planilha legada "Pdq - Frequencia - Historico.csv".

Layout (todas as linhas com o mesmo número de colunas):

    linha 0: ["", " ", "", "", "", "", "",        venue_1, venue_2, ...]
    linha 1: ["", "", "", "", "", "", "Ordem",     1, 2, ...]   (1 = sessão mais recente)
    linha 2: ["", "", "", "", "", "", "Faltas",    F por sessão ...]
    linha 3: ["", "", "", "", "", "", "Presencas", X por sessão ...]
    linha 4: ["POS","CLASSE","POSICAO","ID","JOGADORES","Faltas","125", dd/mm/aaaa ...]
    linha 5+: uma linha por jogador, sessões da mais recente para a mais antiga.

Células de presença: "X" (presente), "F" (falta) ou "-" (não convocado / sem registro).
Arquivo em UTF-8, separador vírgula, CRLF e sem quebra de linha final.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

FIXED_COLUMNS = 7
HEADER_ROWS = 5
COLUMN_HEADER = ["POS", "CLASSE", "POSICAO", "ID", "JOGADORES", "Faltas"]
# A célula G5 da planilha legada é um rótulo estático herdado ("125"); não é
# recalculada pela planilha e é reproduzida literalmente na exportação.
PRESENCAS_HEADER_LABEL = "125"
LINE_TERMINATOR = "\r\n"

# Inconsistência conhecida da planilha: a fórmula de Presenças por jogador
# ficou desatualizada e não inclui a sessão de 21/08/2025.
STALE_PRESENCAS_EXCLUDED_DATE = date(2025, 8, 21)


@dataclass
class LegacySession:
    ordem: int
    date: date
    venue: str


@dataclass
class LegacyPlayer:
    pos: int
    classe: str
    posicao: str
    legacy_id: str
    name: str
    faltas: str
    presencas: str
    cells: list[str] = field(default_factory=list)  # alinhado a sessions (recente -> antiga)


@dataclass
class LegacySheet:
    sessions: list[LegacySession]  # como na planilha: ordem 1 = mais recente
    players: list[LegacyPlayer]
    session_faltas: list[str]
    session_presencas: list[str]


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%d/%m/%Y").date()


def format_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def read_rows(source: str | Path | bytes) -> list[list[str]]:
    """Lê o CSV como matriz de strings, preservando células intactas."""
    if isinstance(source, bytes):
        text = source.decode("utf-8")
    else:
        text = Path(source).read_bytes().decode("utf-8")
    return list(csv.reader(io.StringIO(text, newline="")))


def write_rows(rows: list[list[str]]) -> bytes:
    """Serializa no formato exato da planilha (CRLF, sem newline final)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=LINE_TERMINATOR)
    writer.writerows(rows)
    text = buf.getvalue()
    if text.endswith(LINE_TERMINATOR):
        text = text[: -len(LINE_TERMINATOR)]
    return text.encode("utf-8")


def parse_sheet(rows: list[list[str]]) -> LegacySheet:
    if len(rows) < HEADER_ROWS:
        raise ValueError(f"planilha precisa de pelo menos {HEADER_ROWS} linhas")
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError(f"linhas com larguras diferentes: {sorted(widths)}")
    header = rows[4]
    if header[:6] != COLUMN_HEADER:
        raise ValueError(f"cabeçalho inesperado: {header[:6]}")

    venues = rows[0][FIXED_COLUMNS:]
    ordens = rows[1][FIXED_COLUMNS:]
    dates = header[FIXED_COLUMNS:]
    sessions = [
        LegacySession(ordem=int(o), date=parse_date(d), venue=v)
        for o, d, v in zip(ordens, dates, venues, strict=True)
    ]

    players = []
    for row in rows[HEADER_ROWS:]:
        cells = row[FIXED_COLUMNS:]
        bad = sorted({c for c in cells if c not in ("X", "F", "-")})
        if bad:
            raise ValueError(f"células inválidas para {row[4]!r}: {bad}")
        players.append(
            LegacyPlayer(
                pos=int(row[0]),
                classe=row[1],
                posicao=row[2],
                legacy_id=row[3],
                name=row[4],
                faltas=row[5],
                presencas=row[6],
                cells=cells,
            )
        )
    return LegacySheet(
        sessions=sessions,
        players=players,
        session_faltas=rows[2][FIXED_COLUMNS:],
        session_presencas=rows[3][FIXED_COLUMNS:],
    )
