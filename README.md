# pdq

Controle de frequência do Pdq. Python 3.11+ apenas com biblioteca padrão
(`sqlite3`, `csv`); `pytest` e `ruff` para desenvolvimento.

Os dados vivem em `data/` (banco SQLite `data/pdq.db` + fotos em `data/photos/`)
e nunca são versionados. A planilha legada em `legacy/` é a referência dourada
da migração (ver `legacy/README.md`).

## Instalação

```sh
python -m pip install -e '.[dev]'
python -m pdq --help
```

## Comandos

| Comando | Função |
|---------|--------|
| `pdq init-db` | cria `data/pdq.db` vazio |
| `pdq import-legacy [CSV]` | substitui o conteúdo do banco pela planilha legada |
| `pdq export-legacy [-o ARQ] [--strict-legacy-quirk]` | regenera a planilha no layout legado |
| `pdq validate-legacy [CSV]` | importa em memória, exporta e compara célula a célula |
| `pdq backup` | copia `data/` para `backups/<YYYYMMDD-HHMMSS>/` com `manifest.json` (SHA-256) |
| `pdq restore [DIR]` | recria `data/` a partir de um backup (padrão: o mais recente), conferindo hashes |
| `pdq verify-backup [DIR]` | confere a integridade de um backup |
| `pdq propose LISTA [-o ARQ] [--date] [--venue]` | lê a lista do WhatsApp e gera a proposta de presenças (JSON) |
| `pdq confirm PROPOSTA [--dry-run]` | grava a partida a partir da proposta revisada |
| `pdq charges [--date] [--month] [--all] [--since] [--mensalidade]` | cobranças da partida (diárias) e do mês (mensalidades) |
| `pdq pay JOGADOR VALOR [--on] [--ref] [--note]` | registra um pagamento |
| `pdq balance [JOGADOR] [--all] [--since] [--mensalidade]` | pendências, ou saldo detalhado de um jogador |

`scripts/backup.sh` é um atalho para `pdq backup`.

## Fluxo de migração (Via C)

```sh
python -m pdq import-legacy 'legacy/Pdq - Frequencia - Historico.csv'
python -m pdq validate-legacy 'legacy/Pdq - Frequencia - Historico.csv'   # 0 diferenças
python -m pdq backup
```

Recuperação após perda total de `data/`:

```sh
rm -rf data/
python -m pdq restore            # usa backups/<mais recente>
python -m pdq validate-legacy    # a planilha continua reproduzível
```

## Registro pós-jogo (lista do WhatsApp)

Dois passos, com um JSON editável no meio:

```sh
python -m pdq propose lista.txt -o proposta.json      # ou: pbpaste | python -m pdq propose - -o proposta.json
# revisar/editar proposta.json (furos, nomes novos, vínculos pendentes)
python -m pdq confirm proposta.json --dry-run
python -m pdq confirm proposta.json
```

O parser aceita seções `Goleiros` / `Linha` / `Reservas`, numeração `1.`, `1-`, `1)`,
vagas vazias (`7.`), padrinho (`João (padrinho: Danillo)`, `- conv. Rodrigo`),
observação (`- obs: chega tarde`, `(só 1º tempo)`) e furo (`(furo)` ou `~riscado~`).
Data e local vêm do título (`Pdq 04/09 - Fair Play`) ou de `--date`/`--venue`.

A proposta atribui um status por linha e uma ação de vínculo:

| Campo | Valores |
|-------|---------|
| `status` | `X` presença (padrão), `F` furo, `J` reserva que jogou (seção Reservas) |
| `action` | `link` (alias exato, usa `player_id`), `create` (jogador novo: `new_player.name` editável, `posicao`, `padrinho`), `review` (nome parecido com jogadores existentes: escolha `link` + `player_id`, `create` ou `skip`), `skip` |

`propose` sai com código 1 enquanto houver linhas em `review`; `confirm` recusa a
proposta com pendências ou com data já registrada e grava tudo numa única transação:
sessão, `match_meta` (vagas vazias, texto original), presenças com observação,
jogadores novos (`legacy_id` = AAMM da partida) e aliases aprendidos, que fazem a
próxima lista casar sozinha.

## Financeiro

As cobranças não são gravadas: são derivadas de presença + classe a cada consulta.
Só os pagamentos entram no banco (`payment`).

| Classe | Quem | Cobrança |
|--------|------|----------|
| `M` mensalista | paga por mês | mensalidade em todo mês com partida (padrão R$ 60, `--mensalidade`) |
| `F` frequente | habitual sem mensalidade | diária R$ 15 por partida em que jogou (`X`/`J`) |
| `-`/vazio convidado | esporádico, trazido por padrinho | diária R$ 15; a dívida é do convidado, o padrinho sai como referência ([ADR 0001](docs/adr/0001-diaria-do-convidado.md)) |

```sh
python -m pdq charges                              # diárias da última partida + mensalidades do mês
python -m pdq pay zico 15 --ref 2025-09-04 --note pix
python -m pdq pay Rodrigo 60 --ref 2025-09
python -m pdq balance                              # quem ainda deve
python -m pdq balance Rodrigo                      # cobranças e pagamentos dele
```

`JOGADOR` aceita id, nome da planilha ou alias aprendido. Como a planilha legada
traz anos de histórico, use `--since AAAA-MM` para começar a contabilidade num mês
(cobranças anteriores são ignoradas). Saldo positivo é pendência, negativo é crédito.

## Exportação e a inconsistência da planilha

A planilha original tem a fórmula de Presenças por jogador desatualizada
(exclui a sessão de 21/08/2025). `export-legacy --strict-legacy-quirk` reproduz
esse comportamento e gera bytes idênticos ao original; sem a flag, apenas as
20 células afetadas mudam (+1 cada).

## Modelo de dados

- `player(pos, classe, posicao, legacy_id, name)`: uma linha por jogador, `pos` é a
  ordem na planilha. Textos preservados literalmente.
- `session(ordem, date, venue)`: `ordem` 1 = sessão mais recente; `date` em ISO 8601.
- `attendance(player_id, session_id, status, note)`: `status` em `X` (presente), `F` (furo),
  `J` (reserva que jogou; exportado como `X`), `-` (sem registro); imposto por `CHECK`.
  `note` guarda a observação da linha da lista.
- `player.padrinho`: quem apresentou o jogador (texto da lista).
- `player_alias(alias, player_id)`: apelidos normalizados (sem acento/caixa/pontuação)
  aprendidos nas confirmações.
- `match_meta(session_id, vagas_vazias, observacao, raw_list)`: dados da lista que não
  cabem na planilha.
- `payment(player_id, amount_cents, paid_on, ref, note)`: pagamentos recebidos, em centavos;
  `ref` é a partida (`AAAA-MM-DD`) ou o mês (`AAAA-MM`) a que se refere.

Faltas e Presenças (por jogador e por sessão) são derivados e recalculados na exportação.
O schema é versionado por `PRAGMA user_version`; bancos anteriores (versões 1 e 2) são migrados
automaticamente ao abrir, sem perda de dados. Consulte `CONTEXT.md` para o vocabulário.

## Desenvolvimento

```sh
ruff check . && ruff format --check . && pytest -q
```
