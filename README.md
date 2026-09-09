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

## Exportação e a inconsistência da planilha

A planilha original tem a fórmula de Presenças por jogador desatualizada
(exclui a sessão de 21/08/2025). `export-legacy --strict-legacy-quirk` reproduz
esse comportamento e gera bytes idênticos ao original; sem a flag, apenas as
20 células afetadas mudam (+1 cada).

## Modelo de dados

- `player(pos, classe, posicao, legacy_id, name)`: uma linha por jogador, `pos` é a
  ordem na planilha. Textos preservados literalmente.
- `session(ordem, date, venue)`: `ordem` 1 = sessão mais recente; `date` em ISO 8601.
- `attendance(player_id, session_id, status)`: `status` em `X` (presente), `F` (falta),
  `-` (sem registro); imposto por `CHECK`.

Faltas e Presenças (por jogador e por sessão) são derivados e recalculados na exportação.

## Desenvolvimento

```sh
ruff check . && ruff format --check . && pytest -q
```
