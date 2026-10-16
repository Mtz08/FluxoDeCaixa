# Caixa da Papelaria

Controle de caixa e de fiado (caderneta) para a papelaria. Roda no computador da loja,
**funciona sem internet** e pode ser consultado pelo celular no Wi-Fi da loja.

> Sistema **gerencial**: não emite nota fiscal. A nota, quando necessária, é emitida por fora.

- [Parte 1 — Manual da loja](#parte-1--manual-da-loja)
- [Parte 2 — Manual técnico](#parte-2--manual-técnico)

---

# Parte 1 — Manual da loja

## Abrir o sistema

Dê dois cliques no atalho **Caixa da Papelaria** na área de trabalho. O navegador abre sozinho.

- Uma janela preta chamada **"Caixa da Papelaria - NÃO FECHE esta janela"** fica minimizada na
  barra de tarefas. **Não feche essa janela** enquanto estiver usando o sistema.
- No **primeiro uso**, o sistema pede para criar o usuário da dona (administrador).
  **Anote a senha em papel** e guarde em lugar seguro.

### Quem pode fazer o quê

| | Administrador (dona) | Operador (balcão) |
|---|:---:|:---:|
| Abrir/fechar caixa, vendas, despesas, sangria, suprimento | ✔ | ✔ |
| Anotar fiado, receber fiado, cadastrar cliente | ✔ | ✔ |
| Estornar (desfazer) lançamento | ✔ | só com a senha da dona |
| Passar do limite de crédito do cliente | ✔ | só com a senha da dona |
| Relatórios, usuários, configurações, backup | ✔ | ✘ |

O sistema sai sozinho depois de 30 minutos sem uso. Para trocar de funcionário, clique em
**Trocar operador / Sair**.

## O dia a dia no caixa

### 1. Abrir o caixa (de manhã)
Conte o dinheiro de troco que está na gaveta, digite o valor e clique em **Abrir caixa**.

### 2. Lançar uma venda — **F2**
- Digite só os números: **1 2 5 0** vira **12,50**.
- Pagou com mais de uma forma (ex.: R$ 30 no PIX + R$ 20 em dinheiro)? Preencha as duas.
- Para calcular o troco, preencha "Cliente entregou em dinheiro".
- Confira o resumo e aperte **Enter** para gravar.

### 3. Outras operações
| Botão | Tecla | Para quê |
|---|:---:|---|
| **Saída** | F3 | Despesa (fornecedor, limpeza, lanche...). Escolha a categoria. |
| **Fiado** | F4 | Cliente levou e vai pagar depois. |
| **Sangria** | F6 | Tirar dinheiro da gaveta (ex.: levar ao banco). Motivo obrigatório. |
| **Suprimento** | F7 | Colocar mais troco na gaveta. |
| **Fechar caixa** | F8 | Fim do dia. |
| **Receber fiado** | F9 | Cliente veio pagar o que devia. |
| — | Esc | Cancelar e voltar ao início. |

### 4. Errou um lançamento? Use **Estornar**
Nada é apagado: o estorno grava um lançamento contrário e o original fica riscado, com o motivo.
O funcionário precisa da senha da dona para estornar.

### 5. Fechar o caixa (fim do dia) — **F8**
1. Conte **todo** o dinheiro da gaveta (notas, moedas e o troco).
2. Digite o total. O sistema **não mostra antes** quanto deveria ter (fechamento cego).
3. Ele mostra se **sobrou** ou **faltou**. Se houver diferença, escreva o motivo.
4. Clique em **Fechar caixa definitivamente**. Dá para imprimir ou baixar o PDF do resumo.

> Esqueceu o caixa aberto de um dia para o outro? O sistema avisa e pede para fechar antes de
> lançar qualquer coisa nova.

## Fiado (caderneta)

- **Cadastrar cliente:** nome e, se quiser, apelido e telefone. Marque que o cliente autorizou o
  cadastro. Não pedimos CPF nem endereço.
- **Anotar compra (F4):** busque o cliente pelo nome, apelido ou telefone, digite o valor e o
  que levou. Se pagou uma parte na hora, abra **"Pagou uma parte agora?"**.
- **Receber (F9):** digite quanto o cliente pagou (pode ser parcial). Se pagar **mais** do que
  deve, o sistema pergunta: devolver o troco ou deixar como crédito para a próxima compra.
- **Limite de crédito:** a dona define na ficha do cliente. Passou do limite, só com a senha dela.
- **Enviar extrato no WhatsApp:** abre o WhatsApp com uma mensagem educada e o saldo. Confira e
  aperte enviar no próprio WhatsApp.
- **Dívidas do caderno de papel** (dona): na ficha do cliente, **Lançar saldo do caderno**, com a
  data da compra mais antiga.
- **Painel do fiado** (menu Fiado, dona): total a receber e quem está devendo há 30, 60 ou 90+ dias.
- **Parcelar (ex.: mochila em 3x):** em "Anotar compra", abra **"Parcelar?"**, escolha as
  parcelas e o vencimento da 1ª. As demais vencem no mesmo dia dos meses seguintes. Avise o
  cliente da taxa que aparece na tela.
- **Juros por atraso (só parcelas):** 10 dias de carência após o vencimento; a partir do 11º dia,
  juros fixos por dia sobre o que falta pagar da parcela, até a baixa. Ao receber, os juros são
  pagos primeiro. Taxa padrão provisória **0,033% ao dia**; a dona muda em Configurações › Juros
  (vale para compras novas). Para perdoar, estorne o lançamento de juros.
- Fiado comum (sem parcelar) não tem juros. Não há multa.

## Relatórios (dona)

Menu **Relatórios**: hoje, semana, mês ou outro período. Mostra faturamento, despesas, totais por
forma de pagamento, despesas por categoria, sobras e faltas de cada fechamento, e o fiado.
Dá para baixar **PDF** e **planilhas (CSV)** que abrem no Excel — úteis para mandar ao escritório
de contabilidade. A planilha de fiado tem nomes de clientes: envie só a quem precisa.

## Usar no celular

1. Uma vez só, no computador: dê dois cliques em `scripts\liberar_celular.bat` e aceite o pedido
   de permissão do Windows.
2. Confira se o Wi-Fi da loja está como **Rede privada** no Windows
   (Configurações › Rede e Internet › Wi-Fi › propriedades da rede).
3. No sistema, vá em **Configurações › Acessar pelo celular** e aponte a câmera do celular para o
   **QR Code** (o celular precisa estar no mesmo Wi-Fi). Entre com o mesmo usuário e senha.

> **Nunca** peça para "abrir a porta" do sistema no roteador. Para acessar de fora da loja, use
> a Tailscale (abaixo), que é segura.

## Acessar de fora da loja (casa, rua) — com Tailscale

A **Tailscale** cria uma rede particular e criptografada entre o computador da loja e o seu
celular. Só os aparelhos entrados na **sua conta** conseguem abrir o sistema; para o resto da
internet ele continua invisível. É gratuita para uso pessoal.

**No computador da loja (uma vez):**
1. Baixe em **tailscale.com/download**, instale e entre com a sua conta (Google ou Microsoft).
2. No ícone da Tailscale (perto do relógio), deixe marcado para **iniciar com o Windows**.
3. Dê dois cliques em `scripts\liberar_acesso_remoto.bat` e aceite o pedido de permissão.
4. No painel da Tailscale na internet (login.tailscale.com › Machines), clique nos três pontinhos
   do computador da loja › **Disable key expiry**, para ele não desconectar sozinho a cada 6 meses.

**No celular:**
1. Instale o app **Tailscale** (Play Store / App Store) e entre com a **mesma conta**.
2. Ligue a Tailscale no app.
3. No sistema, em **Configurações › Acessar pelo celular › De fora da loja**, aponte a câmera para
   o QR Code (ou digite o endereço que começa com `http://100.`). Entre com seu usuário e senha.

**Cuidados:**
- O computador da loja precisa estar **ligado** e com o sistema aberto.
- Não adicione à sua conta Tailscale aparelhos de outras pessoas.
- Se perder o celular: no painel da Tailscale, remova o aparelho (Machines › Remove) e troque a
  sua senha do sistema.

## Backup (cópia de segurança) — configure no primeiro dia!

Vá em **Configurações › Backup**:
1. **Crie a senha do backup** (mínimo 8 caracteres) e **anote em papel, guardada fora da loja**.
   Sem ela, nenhum backup pode ser aberto.
2. Informe **duas pastas**: uma do **Google Drive ou OneDrive** (cópia na nuvem) e uma do
   **pendrive**.
3. Clique em **Fazer backup agora** para testar.

Depois disso o sistema faz backup **sozinho**: ao fechar o caixa e uma vez por dia. Se algo
falhar (pendrive fora, disco cheio) ou se o último backup tiver mais de 2 dias, aparece um
**aviso** na tela inicial — resolva no mesmo dia.

### Restaurar um backup (dona)
**Configurações › Restaurar um backup…** Escolha o backup, digite **RESTAURAR** e a sua senha.
Atenção: tudo o que foi lançado **depois** daquele backup deixa de aparecer. O sistema guarda
antes uma cópia do estado atual.

## Problemas comuns

| O que aconteceu | O que fazer |
|---|---|
| Funcionário esqueceu a senha | A dona entra em **Usuários › Alterar › Redefinir senha**. |
| **A dona** esqueceu a senha | No computador da loja, dois cliques em `scripts\recuperar_admin.bat`. Ele mostra uma senha provisória. |
| "Muitas tentativas erradas" | Espere 15 minutos ou peça à dona para redefinir a senha. |
| "O sistema está ocupado" | Espere alguns segundos e tente de novo. Nada foi gravado pela metade. |
| Clicou duas vezes em Gravar | Sem problema: o sistema grava uma vez só. |
| Aviso de **data/hora errada** | Corrija o relógio do Windows (clique no relógio › Ajustar data/hora). |
| Aviso de **backup** na tela inicial | Configurações › Backup: veja qual pasta falhou (pendrive conectado? disco cheio?). |
| Tela **"O banco de dados está com problema"** | Não desligue à força. Ligue para o responsável técnico. Para recuperar: arraste o backup mais recente para cima de `scripts\restaurar_backup.bat`. |
| Celular não abre o sistema | Mesmo Wi-Fi? Rodou `liberar_celular.bat`? Rede marcada como Privada? O computador está ligado com o sistema aberto? |
| A página "ficou desatualizada" | Aperte F5 e tente de novo. |

---

# Parte 2 — Manual técnico

## Visão geral

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12+ |
| Web | FastAPI + Starlette, Jinja2, HTMX (local), CSS próprio — sem build de front-end |
| Banco | SQLite na loja (WAL, `synchronous=FULL`, `BEGIN IMMEDIATE`) **ou** PostgreSQL na nuvem (Neon, trava `pg_advisory_xact_lock`), via SQLAlchemy 2.0 + Alembic; mesmos gatilhos nos dois (`app/core/gatilhos.py`) |
| Segurança | argon2 (senhas), CSRF double-submit, cookies HttpOnly/SameSite=Strict, CSP, validação de Host |
| Relatórios | fpdf2 (PDF), CSV padrão Excel-BR |
| Backup | cryptography (AES-256-GCM + scrypt), DPAPI do Windows via ctypes |
| QR Code | segno |

Publicar como site: [Render + Neon, grátis](docs/PUBLICAR_RENDER_NEON.md) ou [servidor próprio / Oracle](docs/PUBLICAR_NA_NUVEM.md).
Versões exatas testadas: `constraints.txt`. Regras de negócio confirmadas: [docs/REGRAS_NEGOCIO.md](docs/REGRAS_NEGOCIO.md).

### Estrutura

```
app/
  __main__.py      python -m app (host/porta da configuração)
  main.py          criar_app(): middlewares, rotas, tratamento de erros, ciclo de vida
  core/            config, banco, segurança, auditoria, dinheiro, datas, migrações, templates
  modules/
    auth/          usuários, login, sessões, perfis
    caixa/         sessões de caixa, movimentos, partes de pagamento, fechamento cego
    fiado/         clientes, lançamentos, extrato, WhatsApp, LGPD
    relatorios/    relatório por período, CSV, PDF
    backup/        backup/restauração, criptografia, agendador
    configuracao/  preferências, formas, categorias, celular/QR
    inicio/        tela inicial
  templates/  static/
migrations/        Alembic (0001..0004), escritas à mão (inclui triggers)
scripts/           .bat para a loja + utilitários Python
tests/             unit/ (regras) e integracao/ (telas, segurança)
```

Cada módulo: `models` (tabelas) · `schemas` (validação) · `repository` (consultas) ·
`service` (regras; **único que faz commit**) · `routes` (HTTP).

### Regras invioláveis (garantidas no código **e** no banco)

- Dinheiro em **centavos inteiros**; `CHECK (valor_centavos > 0)`, sinal vem do tipo.
- Lançamentos de caixa e fiado, partes de pagamento, auditoria e registros de backup são
  **imutáveis** (triggers `BEFORE UPDATE/DELETE → RAISE(ABORT)`). Correção = estorno.
- Estorno é verificado no banco como **inverso exato** do original e só pode ocorrer uma vez.
- Só **um caixa aberto** (índice único parcial); caixa fechado não aceita lançamentos (trigger).
- Toda operação de dinheiro é **uma transação**; idempotência por `chave_idempotencia UNIQUE`.
- Saldos são **calculados**; caches (saldo do cliente) são conferidos em Configurações › Verificar.
- Datas em **UTC**; exibição em America/Sao_Paulo.

## Instalação e atualização

```bat
scripts\instalar.bat          :: instala ou ATUALIZA (os dados não são apagados)
scripts\liberar_celular.bat   :: regra de firewall: porta 8765, perfil Privado, só sub-rede local
scripts\liberar_acesso_remoto.bat :: acesso de fora da loja: só a faixa da Tailscale (100.64.0.0/10)
scripts\iniciar.bat           :: liga o servidor (se preciso) e abre o navegador
scripts\iniciar_com_windows.bat
```

Para atualizar o sistema: substitua os arquivos do programa e rode `instalar.bat` de novo.
As migrações do banco rodam sozinhas ao iniciar.

### Onde ficam os dados
`%LOCALAPPDATA%\CaixaPapelaria\` — `caixa.db` (+ `-wal`/`-shm`), `logs\`, `backup.senha` (DPAPI),
`pre_restauracao\` (cópias feitas antes de cada restauração). Fora da pasta do programa.

### Variáveis de ambiente

| Variável | Padrão | Uso |
|---|---|---|
| `CAIXA_DIR_DADOS` | `%LOCALAPPDATA%\CaixaPapelaria` | Pasta dos dados |
| `CAIXA_PORTA` | `8765` | Porta (a mesma do QR Code e do firewall) |
| `CAIXA_SESSAO_MINUTOS` | `30` | Padrão de inatividade (a dona altera em Configurações) |
| `CAIXA_NOME_LOJA` | `Papelaria` | Padrão do nome (a dona altera em Configurações) |
| `CAIXA_HOSTS` | — | Nomes extras aceitos no cabeçalho Host (separados por vírgula) |
| `CAIXA_MODO_DEV` | — | `1` libera `scripts\dados_exemplo.py` (nunca na pasta da loja) |

### Scripts de suporte

| Script | Uso |
|---|---|
| `recuperar_admin.bat` | Admin esqueceu a senha: gera senha provisória (troca obrigatória). Sem admin ativo, promove um usuário. Fica na auditoria. |
| `restaurar_backup.bat ARQ.cxbk` | Restaura com o sistema **fechado** (banco corrompido). `--saida x.db` só extrai. |
| `backup_manual.bat` | Backup imediato sem abrir o navegador. |
| `scripts\migrar.py` | Cria/atualiza a estrutura do banco. |
| `scripts\dados_exemplo.py` | Dados de treino (exige `CAIXA_MODO_DEV=1` e outra pasta de dados). |

### Backup (formato)
`.cxbk` = `CXBK` + byte de versão 1 + sal(16) + nonce(12) + AES-256-GCM(gzip(banco)). Chave por
scrypt (n=2^16, r=8, p=1); o cabeçalho é dado autenticado. Cópia feita com a API de backup online
do SQLite, verificada com `integrity_check`; o arquivo gravado é relido e conferido por SHA-256.
Rotação: todos de hoje + último de cada um de 30 dias + último de cada um de 12 meses; backups
`pre_restauracao` nunca são apagados automaticamente.

## Desenvolvimento

```bat
python -m venv .venv
.venv\Scripts\pip install -c constraints.txt -e ".[dev]"
.venv\Scripts\python -m pytest --cov=app      :: 290+ testes (banco real com triggers)
:: Mesma bateria no PostgreSQL (servidor de TESTE; cada teste cria/apaga um banco):
set CAIXA_TESTE_POSTGRES=postgresql://usuario@127.0.0.1:5432
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check .  &&  .venv\Scripts\ruff format --check .
.venv\Scripts\mypy app                        :: modo strict
```

Os testes migram um banco novo uma vez por sessão e copiam o arquivo para cada teste, então
triggers e restrições são testados de verdade. `tests/integracao/test_seguranca.py` percorre
**todas** as rotas (inclusive futuras) e exige login, perfil correto e CSRF.

### Nova migração
```bat
.venv\Scripts\alembic revision -m "descricao"
```
Escreva a migração à mão em `migrations/versions/` (SQLite: `render_as_batch`; inclua triggers e
CHECKs). Registre o módulo novo em `migrations/env.py`.

### Atualizar dependências com segurança
1. Ajuste as faixas no `pyproject.toml`.
2. Crie um ambiente limpo, `pip install .`, rode a suíte completa.
3. `pip-audit -r constraints.txt` (deve dar "No known vulnerabilities").
4. Regere `constraints.txt` com `pip freeze --exclude-editable`.

Atenção: o FastAPI aceita versões antigas do Starlette; por isso o `starlette` é fixado
explicitamente no `pyproject.toml`.

## Como adicionar o PDV (produtos, estoque, vendas) no futuro

A arquitetura já está preparada:
1. Crie `app/modules/produtos/`, `estoque/` e `vendas/` no mesmo padrão de camadas, com
   migrações próprias (0005+).
2. A venda do PDV registra o dinheiro no **mesmo caixa** usando a API do módulo de caixa, na mesma
   transação da baixa de estoque:
   ```python
   sessao = caixa.exigir_sessao_para_lancar(db)
   mov = caixa.montar_movimento(sessao, usuario, TipoMovimento.ENTRADA, partes,
                                chave=f"{chave}:caixa", origem=Origem.PDV, origem_id=venda.id)
   caixa.inserir_movimento(db, mov, usuario, "lancamento_caixa", ip)   # sem commit
   # ... grava venda/itens/estoque ...
   db.commit()
   ```
3. Venda a prazo usa o fiado do mesmo jeito (`fiado.anotar_compra` com a venda na descrição).
4. Fechamento, relatórios, backup e auditoria passam a incluir o PDV sem mudanças.
5. Integração fiscal (NFC-e) exige análise própria (certificado, SEFAZ, regime tributário).

## Segurança — decisões e limites conhecidos

**Feito:** login com argon2 e bloqueio (5 tentativas / 15 min); sessão no servidor com expiração
por inatividade; CSRF em todo POST; cookies HttpOnly + SameSite=Strict; CSP sem scripts inline;
X-Frame-Options; sem `/docs`; sem cabeçalho `Server`; validação do cabeçalho Host (contra DNS
rebinding); consultas parametrizadas (ORM); busca com LIKE escapado; CSV com proteção contra
injeção de fórmula; restauração só de arquivos da lista ou enviados; logs sem senhas nem dados
de clientes (log de acesso desligado); auditoria imutável e sem dados pessoais de clientes;
dependências fixadas e auditadas com pip-audit.

**Limites aceitos (avalie se mudarem as condições):**
- **HTTP sem TLS na rede local:** a senha trafega sem criptografia **dentro** da rede da loja.
  Proteção: Wi-Fi com WPA2/WPA3 e senha forte, sem rede de clientes misturada. HTTPS com
  certificado próprio exigiria instalar o certificado em cada celular. (O acesso de fora da loja
  pela Tailscale já é criptografado de ponta a ponta pelo WireGuard.)
- **Acesso remoto pela Tailscale:** o Host `100.64.0.0/10` é aceito e a regra de firewall
  (`liberar_acesso_remoto.bat`) só libera essa faixa. A segurança depende da conta Tailscale
  (use 2 fatores na conta Google/Microsoft) e de não compartilhar a tailnet. O servidor de
  coordenação da Tailscale vê só metadados (quais aparelhos), não o conteúdo.
- **Bloqueio do admin por terceiros:** 5 senhas de admin erradas (ex.: ao pedir autorização de
  estorno) bloqueiam o admin por 15 minutos. Recurso: esperar ou `recuperar_admin.bat`.
- **Senha do backup no PC:** protegida pela DPAPI (vale para o usuário do Windows). Protege as
  cópias na nuvem/pendrive, não um PC já invadido. Use senha no Windows da loja.
- **Upload de restauração:** limitado a 200 MB após o recebimento (uso só na rede local, por admin).
