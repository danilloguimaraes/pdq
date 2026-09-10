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
| `pdq guest-queue` | lista convidados com quatro presenças aguardando decisão |
| `pdq promote-guest JOGADOR {F,M} DATA` | promove convidado pendente com a data da decisão |
| `pdq decline-guest JOGADOR DATA {--keep-guest,--leaves}` | registra recusa mantendo o convidado ou sua saída |
| `pdq show-session DATA` | mostra uma partida gravada e suas presenças |
| `pdq relink DATA ERRADO CERTO [--alias GRAFIA]` | troca o jogador vinculado a uma presença |
| `pdq set-status DATA JOGADOR {X,F,J,-}` | alterna presença / furo / jogou / não jogou |
| `pdq set-section DATA JOGADOR {goleiros,linha,reservas}` | corrige a seção da lista |
| `pdq set-date DATA NOVA_DATA` | move a partida de data (renumera a ordem) |
| `pdq set-venue DATA LOCAL` | corrige o local |
| `pdq delete-session DATA [--yes]` | exclui a partida (sem `--yes` só mostra o que sairia) |
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

## Ciclo de vida do convidado

Todo jogador criado por `confirm` começa como convidado (`classe C`). A quarta
presença, seja `X` ou `J`, o coloca na fila de decisão; `F` e `-` não contam.
O comando não altera a planilha legada além da classe do jogador, e uma recusa
que registra saída preserva jogador, presenças e aliases para manter o histórico.

```sh
python -m pdq guest-queue
python -m pdq promote-guest 'João Pedro' F 2025-09-10
python -m pdq decline-guest 42 2025-09-10 --keep-guest
python -m pdq decline-guest 42 2025-09-10 --leaves
```

## Correção de partida

Errou depois de confirmar? Toda correção localiza a sessão pela data, valida e
grava numa única transação; nada de SQL manual. Jogadores podem ser indicados
por id, nome exato ou alias aprendido.

```sh
python -m pdq show-session 2025-09-04
python -m pdq relink 2025-09-04 'Gustavo Bastos' 'Gustavo Oliveira' --alias Gustavo
python -m pdq set-status 2025-09-04 Danillo X        # F -> X
python -m pdq set-status 2025-09-04 Saulo -          # reserva listado que não jogou
python -m pdq set-section 2025-09-04 Saulo linha
python -m pdq set-date 2025-09-04 2025-09-05         # renumera `ordem`
python -m pdq set-venue 2025-09-05 'Bora Bola'
python -m pdq delete-session 2025-09-05              # mostra e sai com 1
python -m pdq delete-session 2025-09-05 --yes        # exclui presenças e match_meta; jogadores ficam
```

`relink` preserva status, seção e observação da linha; com `--alias`, a grafia da
lista passa a apontar para o jogador certo nas próximas propostas. O status `-`
mantém a linha (o jogador estava na lista) mas é "sem registro" na planilha, e a
seção não é exportada: o export legado continua byte a byte.

## Financeiro

As cobranças não são gravadas: são derivadas de presença + classe a cada consulta.
Só os pagamentos entram no banco (`payment`).

| Classe | Quem | Cobrança |
|--------|------|----------|
| `M` mensalista | paga por mês | mensalidade em todo mês com partida (padrão R$ 60, `--mensalidade`) |
| `F` frequente | habitual sem mensalidade | diária R$ 15 por partida em que jogou (`X`/`J`) |
| `C` convidado (ou `-`/vazio herdado da planilha) | esporádico, trazido por padrinho | diária R$ 15; a dívida é do convidado, o padrinho sai como referência ([ADR 0001](docs/adr/0001-diaria-do-convidado.md)) |

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
Como a cobrança é derivada, uma correção de partida (`set-status`, `relink`,
`delete-session`) ajusta o saldo automaticamente; os pagamentos ficam intactos.

## Exportação e a inconsistência da planilha

A planilha original tem a fórmula de Presenças por jogador desatualizada
(exclui a sessão de 21/08/2025). `export-legacy --strict-legacy-quirk` reproduz
esse comportamento e gera bytes idênticos ao original; sem a flag, apenas as
20 células afetadas mudam (+1 cada).

## Modelo de dados

- `player(pos, classe, posicao, legacy_id, name, guest_status, guest_decision_date)`: uma linha por jogador, `pos` é a
  ordem na planilha. Textos preservados literalmente.
- `session(ordem, date, venue)`: `ordem` 1 = sessão mais recente; `date` em ISO 8601.
- `attendance(player_id, session_id, status, note, section)`: `status` em `X` (presente),
  `F` (furo), `J` (reserva que jogou; exportado como `X`), `-` (sem registro / listado que
  não jogou); imposto por `CHECK`. `note` guarda a observação da linha da lista e `section`
  a seção (`goleiros`, `linha`, `reservas`; vazia nas linhas importadas da planilha).
- `player.padrinho`: texto histórico de quem apresentou o jogador, preservado como foi informado.
- `player.padrinho_id`: vínculo estruturado ao jogador padrinho, quando a referência normalizada
  corresponde a exatamente um nome canônico. A higiene E3 reconhece sufixos legados como
  `(BRUNO)`, `AMIGO BRUNO` e `CONVIDADO BRUNO`; referências ausentes ou ambíguas ficam pendentes
  para decisão manual e não alteram o vínculo.
- `player_alias(alias, player_id)`: apelidos normalizados (sem acento/caixa/pontuação)
  aprendidos nas confirmações.
- `match_meta(session_id, vagas_vazias, observacao, raw_list)`: dados da lista que não
  cabem na planilha.
- `payment(player_id, amount_cents, paid_on, ref, note)`: pagamentos recebidos, em centavos;
  `ref` é a partida (`AAAA-MM-DD`) ou o mês (`AAAA-MM`) a que se refere.

Faltas e Presenças (por jogador e por sessão) são derivados e recalculados na exportação.
O schema é versionado por `PRAGMA user_version` (1 = E0, 2 = E1, 3 = E2, 4 = E4 + E5); bancos antigos
são migrados automaticamente ao abrir, sem perda de dados. Consulte `CONTEXT.md` para o vocabulário.

## Desenvolvimento

```sh
ruff check . && ruff format --check . && pytest -q
```
