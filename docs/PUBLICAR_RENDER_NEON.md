# Publicar o Caixa da Papelaria no Render + Neon (tudo grátis, sem cartão)

Resultado: o sistema abre em `https://caixa-papelaria.onrender.com` (ou nome parecido), de
qualquer lugar, com HTTPS. O sistema roda no **Render** e o banco de dados fica no **Neon**.

> **Leia antes — limites do plano gratuito (confira as regras atuais nos sites):**
> - **Neon:** 100 horas de processamento por mês. Se acabar, **o banco para até o mês virar**
>   (o caixa fica fora do ar). O banco desliga sozinho após 5 min parado e o sistema foi feito
>   para não acordá-lo à toa; numa loja aberta ~10 h/dia o uso fica em torno de 75 h/mês.
>   **Acompanhe o consumo no painel do Neon** (passo 6).
> - **Render:** depois de 15 min sem acesso o site "dorme" e o primeiro acesso demora ~1 minuto.
>   O passo 5 (acordador) evita isso.
> - **Backup:** no Render os arquivos somem; o backup é o botão **Baixar backup**, **toda semana**
>   (o sistema avisa se passar de 7 dias). O Neon também guarda um histórico curto para recuperar.
> - Os dados ficam nos Estados Unidos (LGPD: os serviços são operadores dos dados).
> - Se a internet da loja cair, o caixa para: anote no papel e lance depois.

Tempo estimado: 40 a 60 minutos.

---

## 1. Criar o banco no Neon

1. Acesse **neon.com** › *Sign up* (pode entrar com a conta do GitHub).
2. **Create project**:
   - Nome: `caixa-papelaria`
   - Postgres version: a mais recente oferecida (17 ou superior)
   - Região: **AWS US East (N. Virginia)** — a mesma do Render (mais rápido)
3. Em **Settings › Compute** (ou no botão de editar a compute do branch `main`), deixe o tamanho
   **fixo em 0.25 CU** (mínimo e máximo 0.25). Isso faz as 100 h grátis durarem o mês todo.
4. Clique em **Connect** e copie a **connection string**:
   - desmarque a opção **Connection pooling** (use a conexão direta, sem `-pooler` no endereço);
   - ela tem o formato `postgresql://usuario:senha@ep-xxxx.us-east-1.aws.neon.tech/neondb?sslmode=require`.
   - **Guarde em lugar seguro:** essa linha dá acesso total aos dados.

## 2. Publicar no Render

1. Acesse **render.com** › *Get Started* › entre **com a conta do GitHub** (Mtz08).
2. Autorize o Render a ver o repositório **FluxoDeCaixa**.
3. **New › Blueprint** › escolha o repositório **FluxoDeCaixa** › *Connect*.
   O Render lê o arquivo `render.yaml` e pede três valores:
   | Campo | O que colocar |
   |---|---|
   | `CAIXA_DATABASE_URL` | a connection string do Neon (passo 1.4) |
   | `CAIXA_SENHA_BACKUP` | uma senha para os backups, **mínimo 8 caracteres — anote em papel** |
   | `CAIXA_URL_PUBLICA` | por enquanto `https://caixa-papelaria.onrender.com` (ajuste no passo 3) |
4. *Apply*. A primeira publicação leva de 5 a 10 minutos (acompanhe em *Logs*).

## 3. Conferir o endereço

1. No painel do Render, abra o serviço **caixa-papelaria** e veja o endereço no topo
   (ex.: `https://caixa-papelaria-ab12.onrender.com`).
2. Se for **diferente** do que você colocou em `CAIXA_URL_PUBLICA`: *Environment* › edite o valor
   para o endereço exato › *Save* (o Render publica de novo sozinho).
3. Abra o endereço: deve aparecer a tela de **primeiro acesso**.
   Se aparecer "Endereço não permitido", o `CAIXA_URL_PUBLICA` está diferente do endereço real.

## 4. Primeiro acesso

1. Crie o usuário da dona (anote a senha em papel).
2. **Configurações › Baixar backup para este aparelho** — guarde no computador e no pendrive.
3. Cadastre os funcionários (Usuários) e as dívidas do caderno (Fiado).

**Já usava o sistema no computador da loja?** Pegue o backup mais recente de lá (`.cxbk`) e, no
site, use **Configurações › Restaurar um backup…**, enviando o arquivo e informando a **senha do
backup antiga** (a do computador).

## 5. Acordador (evita a espera de 1 minuto)

1. Acesse **uptimerobot.com** › crie uma conta grátis.
2. **Add New Monitor** › tipo **HTTP(s)** › URL: `https://SEU-ENDERECO.onrender.com/saude`
   › intervalo **5 minutos** › *Create*.

O endereço `/saude` **não consulta o banco**: mantém o site acordado sem gastar a cota do Neon.
(O Render dá 750 h/mês grátis — suficiente para o mês inteiro ligado.)

## 6. Rotina

| Quando | O quê |
|---|---|
| Toda semana | Configurações › **Baixar backup** › guardar no computador **e** no pendrive |
| Toda semana | Painel do Neon › **Usage**: conferir as horas de compute usadas no mês (limite 100) |
| Atualizações | Automáticas: cada mudança enviada ao GitHub é publicada pelo Render |

## Problemas

| Problema | O que fazer |
|---|---|
| "Não foi possível falar com o banco de dados" | Veja o painel do Neon: projeto ativo? Cota do mês esgotada? Connection string certa no Render? |
| Cota do Neon acabou no meio do mês | Use o caderno até o mês virar, ou passe o Neon para um plano pago só naquele mês. Depois, avalie reduzir o uso. |
| Site demora ~1 min para abrir | O acordador (passo 5) não está ativo ou está com o endereço errado. |
| "Endereço não permitido" | `CAIXA_URL_PUBLICA` precisa ser exatamente o endereço do site (com `https://`). |
| Dona esqueceu a senha | Render › serviço › **Shell** › `python scripts/recuperar_admin.py` (o Shell pode não existir no plano grátis; nesse caso, peça ajuda ao responsável técnico). |
| Perdi tudo (Render ou Neon apagados) | Crie de novo (passos 1 a 3) e restaure o último backup baixado (passo 4). |

## Segurança (resumo)

- HTTPS do próprio Render; cookies só por HTTPS e HSTS.
- Login com bloqueio por usuário (5 erros) **e** por aparelho (20 erros em 15 minutos).
- O site só responde pelo seu endereço (`CAIXA_URL_PUBLICA`).
- A connection string do Neon e a senha do backup ficam só nas variáveis secretas do Render
  (nunca no GitHub).
- Use **verificação em 2 etapas** nas contas do GitHub, do Render e do Neon: quem entra nelas
  tem acesso aos dados.
