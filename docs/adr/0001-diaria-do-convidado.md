# ADR 0001 — A diária do convidado recai sobre o convidado

Data: 2026-09-09 · Status: aceita · Issue: E4 · Financeiro (#5)

## Contexto

Cada partida gera diárias (R$ 15) para quem jogou sem ser mensalista: frequentes
(classe `F`) e convidados (classe `C`, ou `-`/vazia herdada da planilha). O convidado chega ao grupo por um
padrinho, anotado na lista (`(padrinho: X)`, `- conv. X`) e gravado em
`player.padrinho`. Na hora da cobrança surge a dúvida de quem responde pela
diária do convidado: ele próprio ou quem o trouxe (B3.4).

Sem uma regra única, cada cobrança vira negociação e a pendência fica sem dono.

## Decisão

A diária do convidado é **devida pelo próprio convidado**. O padrinho aparece na
cobrança apenas como **referência** (a quem pedir quando o convidado não está
no grupo do WhatsApp), nunca como devedor.

Consequentemente, `pdq.finance.charges_for_session` gera uma `Charge` com
`player_id` do convidado e `padrinho` preenchido a partir de `player.padrinho`;
nenhuma cobrança é criada em nome do padrinho por presença de terceiros. O saldo
(`balance`) de um jogador soma apenas as cobranças em seu próprio nome.

## Consequências

- Uma regra, aplicada em um único lugar (`finance.py`); a CLI só exibe.
- Se o padrinho quiser pagar pelo convidado, registra-se o pagamento com
  `pdq pay <convidado> 15` — o dinheiro entra na conta de quem devia.
- Convidado que vira frequente ou mensalista (mudança de `classe`) passa a ser
  cobrado pela nova regra sem retroatividade: cobranças são derivadas da
  classe atual, não versionadas.
- Convidados sem padrinho registrado saem na cobrança sem referência; a
  lacuna fica visível e pode ser corrigida em `player.padrinho`.

## Alternativas consideradas

1. **Diária recai sobre o padrinho.** Concentra a cobrança em quem já está no
   grupo, mas mistura contas (o saldo do padrinho passa a depender de terceiros),
   dificulta o convidado virar frequente com histórico limpo e depende de
   `padrinho` estar sempre preenchido, o que a lista nem sempre garante.
2. **Regra configurável por convidado.** Flexível, porém exige um campo extra
   por presença e reabre a negociação a cada partida — exatamente o problema que
   se quer eliminar.
3. **Sem regra explícita (cobrar "quem aparecer").** Estado atual; gera a
   confusão que motivou o E4.
