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
versionada por `PRAGMA user_version` (1 = E0, 2 = E1).

## Módulos

| Módulo | Responsabilidade |
|--------|------------------|
| `pdq.db` | schema, migrações, renumeração de sessões |
| `pdq.legacy` / `importer` / `exporter` / `validate` | planilha legada (E0) |
| `pdq.backup` | backup e restauração de `data/` |
| `pdq.whatsapp` | parser puro da lista (sem banco) |
| `pdq.aliases` | normalização, vínculo exato, sugestões, aprendizado |
| `pdq.postgame` | proposta, JSON, validação e confirmação |
| `pdq.__main__` | CLI (`propose`, `confirm`, ...) |
