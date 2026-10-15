# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

## [1.1.0] - 2026-10-15

### Adicionado
- Acesso de fora da loja pela Tailscale (rede privada criptografada), sem expor o sistema na
  internet: endereços `100.64.0.0/10` aceitos, endereço e QR Code em Configurações, script
  `liberar_acesso_remoto.bat` (firewall só para essa faixa) e passo a passo no manual.

## [1.0.0] - 2026-10-15

Primeira versão completa (Fase 1: caixa + fiado).

### Adicionado
- `scripts/instalar.bat`: localiza o Python 3.12+, cria o ambiente, instala as versões exatas
  testadas (`constraints.txt`), prepara o banco, cria o atalho e oferece iniciar com o Windows.
  Pode ser rodado de novo para atualizar, sem apagar dados.
- `scripts/iniciar.bat`: liga o servidor minimizado (só se ainda não estiver ligado), espera ficar
  pronto e abre o navegador.
- `scripts/liberar_celular.bat`: regra de firewall só para rede Privada e sub-rede local.
- `scripts/iniciar_com_windows.bat`, `backup_manual.bat`, `recuperar_admin.bat` (senha do admin
  esquecida), `scripts/migrar.py`, `scripts/dados_exemplo.py` (só em modo de desenvolvimento).
- Validação do cabeçalho Host (proteção contra DNS rebinding).
- Testes de segurança que percorrem todas as rotas: login obrigatório, perfil e CSRF.
- Manual da loja e manual técnico no README.

### Alterado
- Dependências atualizadas: FastAPI 0.143, Starlette 1.8 (fixado), uvicorn 0.54, alembic 1.20,
  argon2-cffi 25.1, tzdata 2026. `pip-audit`: nenhuma vulnerabilidade conhecida.
- Log de acesso do servidor desligado (as URLs de busca podem conter nomes de clientes).
- Operações exclusivas do administrador no fiado respondem "sem permissão" (403).

### Segurança
- Corrigidas vulnerabilidades conhecidas do Starlette 0.46 (presentes até a versão 0.5.0).

## [0.5.0] - 2026-10-15

### Adicionado
- Backup criptografado (AES-256-GCM, chave derivada da senha por scrypt) e compactado, feito
  pela API de backup online do SQLite, com verificação de integridade e conferência do arquivo
  gravado (SHA-256). Até 3 pastas de destino (ex.: OneDrive + pendrive).
- Backup automático ao fechar o caixa, diário no horário configurado (tenta de novo a cada hora
  se falhar) e manual por botão.
- Rotação: todos os de hoje + último de cada um dos 30 dias + último de cada um dos 12 meses;
  backups feitos antes de uma restauração nunca são apagados automaticamente.
- Senha do backup guardada no PC protegida pela DPAPI do Windows.
- Restauração (administrador) pela lista de backups ou envio de arquivo, com confirmação dupla
  (digitar RESTAURAR + senha do admin) e cópia automática do estado atual antes de restaurar.
- `scripts/restaurar_backup.bat`: recuperação sem abrir o sistema (banco corrompido).
- Alerta na tela inicial: backup não configurado, último backup há 2+ dias ou falha recente.
- Tela de Configurações: nome da loja, tempo de sessão, formas de pagamento e categorias
  (adicionar/desativar), pastas e horário do backup, campos opcionais de cliente (CPF/endereço,
  desligados por padrão), verificação de integridade dos dados.
- Endereço de acesso pelo celular (IP da rede local) com QR Code.
- `python -m app`: inicia o servidor com host/porta da configuração (os mesmos do QR Code).
- Novas dependências: `cryptography` 50, `segno` 1.6.

### Corrigido
- Auditoria do cadastro de clientes não guarda mais nome/telefone (LGPD: a auditoria é imutável
  e a anonimização não conseguiria apagá-los).

## [0.4.0] - 2026-10-15

### Adicionado
- Resumo do dia na tela inicial do administrador: faturamento por forma, despesas, dinheiro
  esperado na gaveta, fiado anotado e recebido.
- Relatórios (administrador) por dia, semana, mês ou período personalizado (até 1 ano):
  faturamento, despesas, resultado, totais por forma de pagamento, fluxo por dia, despesas por
  categoria, fechamentos com diferenças por operador e fiado (vendas, recebido, abatimentos,
  clientes com 30+ dias em aberto).
- Exportação CSV no padrão do Excel brasileiro (";" e vírgula decimal, UTF-8 com BOM), com
  proteção contra injeção de fórmula: lançamentos, fluxo diário, formas, despesas, fechamentos,
  fiado a receber.
- PDF A4 do relatório do período e do fechamento de caixa.
- Toda exportação (CSV/PDF) é registrada na auditoria.
- Nova dependência: `fpdf2` 2.8 (traz Pillow, fonttools e defusedxml).

### Corrigido
- Nome de quem fechou o caixa não aparecia em resumos montados logo após o fechamento.

## [0.3.0] - 2026-10-14

### Adicionado
- Fiado: cadastro mínimo de clientes (nome, apelido, telefone, observação, limite, dia de pagamento)
  com registro do consentimento (LGPD); sem CPF nem endereço.
- Busca rápida sem acento por nome, apelido ou telefone (atualiza enquanto digita).
- Anotar compra fiado (não entra no caixa), com data e "pagou uma parte agora" (venda mista).
- Receber pagamento total, parcial ou dividido; entra no caixa como "Recebimento de fiado" na
  mesma transação. Pagamento maior que a dívida: escolher troco (só em dinheiro) ou crédito.
- Limite de crédito com bloqueio e autorização do administrador.
- Abatimento/perdão de dívida e saldo trazido do caderno de papel (só administrador).
- Devolução de crédito em dinheiro (saída no caixa + lançamento no fiado).
- Estorno de lançamentos do fiado; quando houve dinheiro no caixa, o caixa é estornado junto.
- Ficha do cliente com extrato, saldo corrente e compra mais antiga em aberto; impressão A4.
- Botão "Enviar extrato no WhatsApp" (link wa.me com mensagem pronta).
- Painel do fiado (administrador): total a receber, clientes devendo, créditos, faixas 30/60/90+.
- LGPD: exportar dados do cliente (JSON) e anonimizar (só com saldo zerado).
- Atalhos F4 (fiado) e F9 (receber fiado).
- Rotina de verificação que recalcula os saldos dos clientes a partir dos lançamentos.

### Alterado
- Lançamentos de caixa gerados pelo fiado só podem ser estornados pela ficha do cliente.

## [0.2.0] - 2026-10-14

### Adicionado
- Caixa: abertura com fundo de troco (um caixa aberto por vez, garantido pelo banco).
- Lançamentos de entrada (com pagamento dividido e cálculo de troco), saída (com categoria),
  sangria (com motivo) e suprimento, sempre com tela de confirmação.
- Formas de pagamento Dinheiro, PIX, Cartão de débito e Cartão de crédito; só Dinheiro mexe na gaveta.
- Estorno com motivo obrigatório; operador precisa da senha do administrador. Estorno de caixa
  já fechado entra no caixa atual sem alterar a gaveta de hoje.
- Fechamento cego em dois passos (contagem → sobra/falta → justificativa), com cada contagem na auditoria.
- Resumo do caixa por forma de pagamento, por tipo e conferência da gaveta, com impressão A4.
- Bloqueio de lançamentos quando o caixa de um dia anterior ficou aberto.
- Alerta de relógio do computador atrasado.
- Chave de idempotência em abertura, lançamentos e estornos (duplo clique não duplica).
- Confirmação extra para valores acima de R$ 50.000,00.
- Impede tirar da gaveta mais dinheiro do que deveria haver nela.
- Lançamentos imutáveis no banco (triggers) e rotina de verificação de consistência.
- Atalhos F2 (entrada), F3 (saída), F6 (sangria), F7 (suprimento), F8 (fechar) e Esc (voltar).
- Mensagem amigável quando o banco está ocupado por outro terminal.

## [0.1.0] - 2026-10-14

### Adicionado
- Base do projeto (FastAPI + SQLite WAL + Alembic + Jinja2/HTMX), sem dependência de internet.
- Assistente de primeiro acesso para criar o administrador.
- Login com bloqueio após 5 tentativas, sessão com expiração por inatividade e troca rápida de operador.
- Perfis Administrador e Operador; cadastro, edição, desativação e redefinição de senha de usuários.
- Senha provisória com troca obrigatória no primeiro acesso.
- Autorização do administrador "na hora" (base para estornos e limite de crédito).
- Auditoria só de inserção, protegida por trigger no banco.
- Proteção CSRF, cookies HttpOnly/SameSite=Strict, cabeçalhos de segurança, sem documentação de API exposta.
- Verificação de integridade do banco na inicialização e migrações automáticas.
