# Planilha legada

`Pdq - Frequencia - Historico.csv` é a referência dourada (golden file) da migração:
a exportação do sistema deve reproduzi-la byte a byte.

- Origem: anexo da issue E0 (GitHub user-attachments).
- SHA-256: `21ab1972c47a9858a63eb112b3c685b99a16663752b46e77fa4a01f5008880d7`
- 151 linhas x 134 colunas; UTF-8, separador vírgula, CRLF, sem quebra de linha final.
- 146 jogadores, 127 sessões (23/02/2023 a 28/08/2025); células X=2371, F=16, -=16155.

## Layout

| Linha | Conteúdo |
|-------|----------|
| 1 | local de cada sessão (`Fair Play`, `Bora Bola`) |
| 2 | `Ordem`: 1 = sessão mais recente |
| 3 | `Faltas` por sessão |
| 4 | `Presencas` por sessão |
| 5 | `POS,CLASSE,POSICAO,ID,JOGADORES,Faltas,125,<datas dd/mm/aaaa>` |
| 6+ | um jogador por linha; sessões da mais recente para a mais antiga |

## Inconsistência conhecida

A coluna **Presenças por jogador** (coluna G) usa uma fórmula desatualizada que não
inclui a sessão de **21/08/2025**. Os 20 jogadores presentes nesse dia aparecem com
1 presença a menos do que o histórico registra. O rótulo `125` em G5 também é herdado
e é reproduzido literalmente.

`python -m pdq export-legacy --strict-legacy-quirk` reproduz a planilha exatamente;
sem a flag, as 20 células são corrigidas e nada mais muda
(`python -m pdq validate-legacy` isola essas células).

Este arquivo não deve ser editado.
