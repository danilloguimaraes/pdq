# Contexto de domínio — pdq

Vocabulário compartilhado do projeto. Os termos abaixo são usados com este
significado no código, nos testes, nos commits e nas issues.

## Linguagem

**Pdq**:
O grupo de pelada cujo histórico de frequência este sistema controla.

**Planilha legada**:
`legacy/Pdq - Frequencia - Historico.csv`, referência dourada da migração. A
exportação deve reproduzi-la byte a byte (`validate-legacy`).
_Evitar_: "CSV antigo", "Excel".

**Sessão** (`session`):
Uma partida realizada, identificada pela data. `ordem` 1 é a mais recente e é
recalculada sempre que uma sessão é inserida (`db.renumber_sessions`).
_Evitar_: "jogo", "rodada" no código.

**Jogador** (`player`):
Pessoa cadastrada. `name` é preservado literalmente como na planilha;
`legacy_id` é `AAMM` da primeira partida; `padrinho` é quem o apresentou.

**Presença** (`attendance`, status `X`):
O jogador estava na lista (goleiros ou linha) e foi.

**Furo** (status `F`):
O jogador estava na lista e não foi. Na planilha aparece como Falta.
_Evitar_: "falta" fora do contexto da planilha exportada.

**Jogou** (status `J`):
O jogador estava na seção Reservas e entrou. Conta como presença na planilha
(exportado como `X`), mas o banco preserva a distinção.

**Sem registro** (status `-`):
O jogador não estava na lista daquela sessão.

**Lista** (do WhatsApp):
Texto colado da mensagem de convocação. Tem título (data/local), seções
Goleiros/Linha/Reservas e linhas numeradas. É a entrada de `pdq propose`.

**Vaga vazia**:
Linha numerada sem nome na lista (`7.`). Contada em `match_meta.vagas_vazias`.

**Padrinho**:
Jogador que convidou um novo integrante, anotado na lista
(`(padrinho: X)`, `- conv. X`). Vai para `player.padrinho` do convidado.

**Observação**:
Texto livre anexo a uma linha (`- obs: chega tarde`). Vai para `attendance.note`.

**Alias**:
Forma como um nome foi escrito na lista, normalizada por `aliases.normalize`
(sem acentos, caixa ou pontuação). `player_alias` guarda os aliases aprendidos
em confirmações e permite o **vínculo exato** na próxima lista.

**Sugestão**:
Candidato a vínculo por similaridade (`difflib`) quando não há alias exato.
Nunca é aplicada sozinha: gera uma linha em revisão.

**Proposta** (`Proposal`):
JSON editável produzido por `propose`: sessão, meta e uma entrada por linha da
lista com `status` (X/F/J) e `action`.

**Ação** (`action`):
Decisão de vínculo de uma entrada da proposta: `link` (usa `player_id`),
`create` (cria `new_player`), `review` (pendente; bloqueia a confirmação) ou
`skip` (ignora a linha).

**Confirmação** (`confirm`):
Gravação atômica da proposta: sessão, `match_meta`, presenças, jogadores novos e
aliases. Recusa pendências e datas já registradas.

**Migração aditiva**:
Evolução do schema que preserva dados e o comportamento do export legado,
versionada por `PRAGMA user_version` (1 = E0, 2 = E1, 3 = E4).

**Classe** (`player.classe`):
Como o jogador se relaciona com o Pdq, herdada da coluna CLASSE da planilha:
`M` mensalista, `F` frequente, `-` ou vazio convidado. É o estado atual, não
versionado: a cobrança deriva sempre da classe de hoje.

**Mensalista** (classe `M`):
Paga **mensalidade** por mês com partida, jogue ou não. Nunca paga diária.

**Frequente** (classe `F`):
Jogador habitual sem mensalidade. Paga **diária** quando joga (X ou J).

**Convidado** (classe `-` ou vazia):
Quem joga esporadicamente, em geral trazido por um padrinho. Paga diária quando
joga; a diária é dele, não do padrinho (ADR 0001). O padrinho aparece na
cobrança apenas como referência.

**Diária** (`Charge` com `kind="diaria"`):
R$ 15 devidos por partida por frequente ou convidado presente (X ou J). Furo
(`F`) não gera diária. Referência (`ref`): a data da partida.

**Mensalidade** (`Charge` com `kind="mensalidade"`):
Valor fixo por mês (padrão R$ 60, ajustável com `--mensalidade`) devido por
mensalista em todo mês com partida, a partir do mês do seu primeiro registro
(X/F/J). Referência (`ref`): `AAAA-MM`.

**Cobrança** (`Charge`):
Diária ou mensalidade devida por um jogador. Não é gravada: é derivada de
presença + classe a cada consulta (`finance.all_charges`), então corrigir uma
presença corrige a cobrança.
_Evitar_: "débito", "fatura".

**Pagamento** (`payment`):
Valor efetivamente recebido de um jogador, em centavos, com data (`paid_on`) e
referência opcional (`ref`: partida ou mês). É a única informação financeira
gravada. Registrado por `pdq pay`.

**Saldo** (`Balance`):
Cobrado − pago por jogador. Positivo é **pendência**, negativo é **crédito**,
zero é quitado. Sempre em nome do próprio jogador (o padrinho não herda saldo).

**Pendência**:
Saldo positivo: o que o jogador ainda deve. `pdq balance` lista só quem tem
pendência; `--all` inclui quitados e créditos.

**Início da contabilidade** (`--since AAAA-MM`):
Mês a partir do qual as cobranças são consideradas; o que vem antes é
histórico de presença sem efeito financeiro.

## Módulos

| Módulo | Responsabilidade |
|--------|------------------|
| `pdq.db` | schema, migrações, renumeração de sessões |
| `pdq.legacy` / `importer` / `exporter` / `validate` | planilha legada (E0) |
| `pdq.backup` | backup e restauração de `data/` |
| `pdq.whatsapp` | parser puro da lista (sem banco) |
| `pdq.aliases` | normalização, vínculo exato, sugestões, aprendizado |
| `pdq.postgame` | proposta, JSON, validação e confirmação |
| `pdq.finance` | cobranças derivadas (diária, mensalidade), pagamentos e saldo |
| `pdq.__main__` | CLI (`propose`, `confirm`, `charges`, `pay`, `balance`, ...) |

## Decisões

| ADR | Decisão |
|-----|---------|
| [0001](docs/adr/0001-diaria-do-convidado.md) | A diária do convidado recai sobre o convidado; padrinho é referência |
