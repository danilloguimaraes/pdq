# Fixtures: listas do WhatsApp

Listas de convocação no formato em que chegam coladas do WhatsApp. Servem aos
testes de `pdq.whatsapp` (parser), `pdq.postgame` (proposta/confirmação) e ao
ponta a ponta sobre a planilha legada. Os nomes são os da planilha
(`legacy/`), para que o vínculo exato seja testado contra dados reais.

| Arquivo | Exercita |
|---------|----------|
| `lista_basica.txt` | título com data completa e local, seções Goleiros/Linha, numeração `1-` |
| `lista_com_vagas_e_padrinho.txt` | data sem ano (`04/09`), numeração `1.`, vagas vazias (`2.`, `6.`, `7. -`), padrinho em dois formatos, jogadores novos |
| `lista_com_reservas_e_obs.txt` | marcação `*negrito*`, numeração `1 -`, seção Reservas (status `J`), observações `(...)` e `- obs:`, furo explícito `(furo)` e `~riscado~` |

Ao encontrar uma lista real que o parser interprete mal, adicione-a aqui
(anonimizada se preciso) junto com o teste que a cobre, antes de ajustar o parser.
