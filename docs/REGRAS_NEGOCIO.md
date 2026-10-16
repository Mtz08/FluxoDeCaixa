# Regras de negócio confirmadas

Decisões tomadas com a responsável em 14/10/2026. Qualquer mudança aqui precisa de nova confirmação.

## Dados e integridade

- Dinheiro sempre em centavos inteiros; lançamentos financeiros são imutáveis (correção = estorno).
- Auditoria e lançamentos protegidos por triggers no banco (UPDATE/DELETE bloqueados).
- Datas gravadas em UTC, exibidas em America/Sao_Paulo. "Dia" = calendário de São Paulo.

## Caixa

| # | Regra |
|---|---|
| 1 | Estorno de lançamento de caixa **já fechado** entra no caixa aberto atual, com referência ao original, e **não altera** o valor esperado da gaveta. |
| 2 | Saída (despesa) aceita qualquer forma de pagamento; só Dinheiro mexe na gaveta. |
| 8 | O caixa é compartilhado: vários operadores lançam na mesma sessão; qualquer um pode fechar. |
| 13 | Operador vê apenas o resumo do caixa aberto (nada de dias anteriores). |

### Decisões de implementação da Etapa 2

- Caixa aberto em dia anterior **bloqueia novos lançamentos** até ser fechado.
- Fechamento cego em dois passos: o sistema mostra só sobra/falta; cada contagem vai para a
  auditoria e o fechamento só aceita a última contagem conferida.
- Operador não vê o valor esperado da gaveta; o administrador vê.
- Não é permitido tirar da gaveta (saída em dinheiro, sangria ou estorno de entrada em dinheiro)
  mais do que o valor esperado nela.
- Valores acima de R$ 50.000,00 exigem marcar uma confirmação extra.
- Recebimento de fiado não pode ser estornado pela tela do caixa, só pela ficha do cliente.
- Estornos de caixas anteriores entram no faturamento do dia em que o estorno foi feito.

## Fiado

| # | Regra |
|---|---|
| 3 | Abatimento/perdão de dívida: só administrador, com motivo; não passa pelo caixa. |
| 4 | Dívidas do caderno de papel entram como `saldo_inicial` (só administrador, sem caixa aberto). |
| 5 | Devolver crédito do cliente em dinheiro: administrador **e** operador; gera saída no caixa + lançamento no fiado na mesma transação. |
| 6 | Venda com parte paga e parte fiado é feita numa tela só ("Anotar fiado" com "pagou agora"). |
| 7 | Atraso (30/60/90+) conta desde a compra mais antiga ainda não quitada; pagamentos abatem as compras mais antigas primeiro. |
| 11 | Operador vê o saldo do cliente e quanto ainda pode comprar; não altera limite. |
| — | Sem multa. Fiado comum (sem parcelar) continua **sem juros**. |
| 14 | Compra parcelada (1 a 24x): parcelas mensais no mesmo dia do mês da 1ª (ou no último dia, se o mês for mais curto); centavos que sobram vão para as primeiras. |
| 15 | Juros por atraso **só em parcela**: carência de 10 dias após o vencimento; juros **a partir do 11º dia**, contando só os dias após a carência (vence dia 10 → dia 21 = 1 dia de juros). |
| 16 | Juros simples (fixos por dia) **só sobre o que falta pagar da parcela** — nunca juros sobre juros. Taxa padrão provisória: **0,033% ao dia** (≈ 0,99% ao mês), ajustável pela dona em Configurações. |
| 17 | Pagamento e abatimento quitam **primeiro os juros**, depois a parcela que venceu primeiro. |

### Decisões de implementação da Etapa 3

- Compra fiado exige caixa aberto (registro de quem lançou) e aceita data passada, nunca futura.
- "Pagou agora" igual ao total é recusado (deve ser lançado como Entrada no caixa).
- Passar do limite: o admin marca "autorizo"; o operador precisa da senha do admin.
- Pagamento maior que a dívida: **troco** só quando o excesso foi pago em dinheiro (o troco sai
  da parte em dinheiro); senão, vira **crédito**. Sem dívida, todo pagamento vira crédito.
- Cliente desativado não compra fiado, mas pode pagar. Cliente com saldo não pode ser desativado.
- Estorno de lançamento do fiado exige admin (ou senha do admin); se o lançamento passou pelo
  caixa, o caixa é estornado na mesma transação (precisa de caixa aberto).
- Estornar uma compra com "pagou agora" desfaz também a entrada da parte paga.
- Lançamentos de caixa vindos do fiado guardam `origem = fiado` e `origem_id = id do cliente`;
  a descrição no caixa não leva o nome do cliente (a anonimização não alcançaria o caixa, que é imutável).
- Abatimento só até o valor da dívida. Saldo inicial do caderno não exige caixa aberto.
- Painel com totais do fiado é só do administrador; o operador busca clientes e vê a ficha.

### Decisões de implementação das parcelas (1.5.0)

- Taxa e carência ficam **gravadas em cada parcela** no dia da compra (o combinado com o cliente);
  mudar a configuração vale só para compras novas. As parcelas não podem ser alteradas nem apagadas.
- Os juros são calculados na hora (ficha, recebimento, painel) e **lançados no extrato** como
  "Juros por atraso" no momento do pagamento ou abatimento, antes do próprio pagamento.
- Perdoar juros = estornar o lançamento de juros: o período perdoado não volta a ser cobrado.
- Estornar a compra parcelada cancela as parcelas (e os juros futuros).
- Arredondamento: meio-centavo para cima, por parcela, em inteiros (sem ponto flutuante).
- Base legal a ser confirmada pelo responsável antes de definir a taxa final: CDC (Lei 8.078/1990)
  art. 52 (informar a taxa ao cliente antes da compra), Código Civil art. 406 e Decreto 22.626/1933
  (Lei de Usura). O sistema mostra a taxa na tela da compra e na ficha; a taxa padrão é provisória.

## Relatórios (Etapa 4)

- Só o administrador vê relatórios e exporta CSV/PDF. O operador baixa apenas o PDF do caixa
  que ele mesmo fechou no dia (para imprimir o fechamento).
- **Datas:** caixa pela data/hora do lançamento; fiado pela data de referência (dia da compra
  ou do pagamento informado). Estornos contam no dia em que foram feitos.
- **Faturamento** = entradas + fiado recebido. **Despesas** = saídas. **Resultado** = faturamento
  − despesas. Sangria e suprimento são movimentação de dinheiro, não receita nem despesa.
- **Vendas fiado** = compras anotadas no período (líquidas de estornos). Saldos trazidos do
  caderno aparecem separados (não são vendas do período).
- Semana = segunda-feira até hoje; mês = dia 1 até hoje; período personalizado de até 1 ano.
- O CSV de fiado a receber tem nomes de clientes (dado pessoal); o PDF traz só totais.
- Toda exportação fica registrada na auditoria.

## Backup e impressão

| # | Regra |
|---|---|
| 9 | Backup em formato próprio criptografado (AES-256-GCM); restaura pelo sistema ou por `scripts/restaurar_backup.py`. |
| 10 | Chave do backup guardada no PC (protegida pelo Windows/DPAPI). A dona anota a senha em papel: sem ela, o backup não abre. |
| 12 | Não há impressora térmica: impressão só em A4. |

### Decisões de implementação da Etapa 5

- Rotação: mantém **todos os backups de hoje**, o último de cada um dos 30 dias mais recentes e o
  último de cada um dos 12 meses; backups feitos antes de restaurar nunca são apagados sozinhos.
- O backup diário sai no horário configurado; se o PC estiver desligado, sai quando o sistema abrir.
  Em caso de falha, tenta de novo a cada hora.
- Falha numa pasta (pendrive fora, disco cheio) não impede as outras e **nunca trava o caixa**:
  vira alerta na tela inicial (para admin e operador; o operador é orientado a avisar o admin).
- Trocar a senha do backup exige a senha do administrador. Backups antigos continuam abrindo
  só com a senha antiga.
- Restaurar exige digitar RESTAURAR + senha do admin; o estado atual é guardado antes (cópia
  local sempre, e backup nas pastas se configurado). Todos precisam entrar de novo depois.
- Formas de pagamento novas **não** contam na gaveta (só "Dinheiro"); "Dinheiro" não pode ser
  desativado. Nada é apagado: formas e categorias são desativadas.
- CPF e endereço do cliente: desligados por padrão; quando ligados, CPF é validado (dígitos
  verificadores). Ao desligar, os dados já gravados não são apagados; a anonimização apaga.
- A auditoria de clientes não guarda dados pessoais (só se há telefone/CPF/endereço).

## Usuários (Etapa 1)

- Senha mínima de 6 caracteres, diferente do nome de usuário.
- 5 senhas erradas seguidas bloqueiam o usuário por 15 minutos (o admin pode desbloquear redefinindo a senha).
- Sessão expira após 30 minutos sem uso (`CAIXA_SESSAO_MINUTOS`).
- Usuário criado pelo admin recebe senha provisória e é obrigado a trocá-la no primeiro acesso.
- Sempre existe pelo menos um administrador ativo; ninguém desativa o próprio usuário.
- Usuário nunca é apagado, só desativado.
