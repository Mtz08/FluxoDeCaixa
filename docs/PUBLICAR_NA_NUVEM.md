# Publicar o Caixa da Papelaria na nuvem (Oracle Cloud grátis + endereço grátis)

Resultado: o sistema abre em `https://SEUNOME.duckdns.org`, de qualquer lugar, com HTTPS.

> **Leia antes:**
> - Se a internet da loja cair, o caixa para: anote no papel e lance depois (com a data certa).
> - Servidor gratuito **pode sumir** (a Oracle pode recolher servidores parados ou mudar as
>   regras). Por isso: **toda semana, baixe o backup** (Configurações › Baixar backup) e guarde
>   no computador e no pendrive. O sistema avisa na tela inicial se passar de 7 dias.
> - Os dados dos clientes passam a ficar num servidor da Oracle (LGPD): escolha a região
>   **Brazil East (São Paulo)** se estiver disponível.
> - As telas e regras da Oracle mudam com o tempo; se algum nome abaixo estiver diferente,
>   procure a opção equivalente.

Tempo estimado: 1 a 2 horas na primeira vez.

---

## 1. Criar a conta na Oracle Cloud

1. Acesse **oracle.com/cloud/free** › *Start for free*.
2. Preencha os dados. Será pedido um **cartão de crédito só para verificação** (o plano
   *Always Free* não cobra).
3. **Home Region:** escolha **Brazil East (São Paulo)** — não dá para trocar depois.
4. Ative a **verificação em 2 etapas** da conta Oracle (é ela que protege o servidor).

**Sobre o "recolhimento" de servidores parados:** segundo a política da Oracle, servidores
*Always Free* com pouco uso por vários dias podem ser recolhidos em contas gratuitas. Contas
convertidas para **Pay As You Go** (Billing › Upgrade) não sofrem esse recolhimento e continuam
sem cobrança enquanto você usar só recursos *Always Free*. Se converter, crie um **alerta de
orçamento** (Billing › Budgets, ex.: R$ 1) para ser avisado se algo começar a cobrar.
Confira as regras atuais na página da Oracle antes de decidir.

## 2. Criar o servidor

1. Menu › **Compute › Instances › Create instance**.
2. **Image:** *Canonical Ubuntu 24.04*.
3. **Shape:** *Ampere* › **VM.Standard.A1.Flex** com **1 OCPU e 6 GB** de memória
   (sobra para este sistema e fica dentro do grátis). Se aparecer *Out of capacity*, tente
   de novo mais tarde.
4. **SSH keys:** *Generate a key pair for me* › **baixe a chave privada** e guarde bem
   (é a "chave da porta" do servidor).
5. *Create*. Anote o **Public IP address** que aparece quando ficar *Running*.

## 3. Liberar as portas do site (80 e 443)

1. Na página da instância › *Primary VNIC* › **Subnet** › **Security Lists** › *Default Security
   List* › **Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, protocolo TCP, porta de destino **80**
   - Source CIDR `0.0.0.0/0`, protocolo TCP, porta de destino **443**
2. **Não** libere a porta 8765 nem outras.

## 4. Criar o endereço grátis (DuckDNS)

1. Acesse **duckdns.org** e entre (Google ou GitHub).
2. Crie um subdomínio, ex.: `papelariaestrela` → fica `papelariaestrela.duckdns.org`.
3. No campo *current ip*, coloque o **IP público** do servidor › *update ip*.

## 5. Entrar no servidor e instalar o Docker

No Windows, abra o **PowerShell** na pasta onde está a chave baixada:

```powershell
ssh -i .\ssh-key-XXXX.key ubuntu@IP_DO_SERVIDOR
```

Já dentro do servidor:

```bash
# Libera 80/443 no firewall interno das imagens Ubuntu da Oracle
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# Atualizações de segurança automáticas + Docker
sudo apt update && sudo apt -y upgrade && sudo apt -y install unattended-upgrades git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

Entre de novo com o mesmo `ssh ...` (para o grupo do Docker valer).

## 6. Baixar o sistema e publicar

```bash
git clone https://github.com/Mtz08/FluxoDeCaixa.git
cd FluxoDeCaixa
cp .env.exemplo .env
nano .env        # troque DOMINIO pelo seu (ex.: papelariaestrela.duckdns.org); Ctrl+O, Enter, Ctrl+X
docker compose up -d --build
```

> Se o repositório do GitHub for **privado**, o `git clone` pede usuário e senha: use o seu
> usuário e um **token** (GitHub › Settings › Developer settings › Personal access tokens ›
> *Fine-grained*, só leitura deste repositório) no lugar da senha.

Em 1 a 2 minutos, abra **https://SEUNOME.duckdns.org** — o cadeado do HTTPS aparece sozinho.
Para acompanhar: `docker compose logs -f` (Ctrl+C para sair).

## 7. Primeiro acesso (no navegador)

1. Crie o usuário da dona (anote a senha em papel).
2. **Configurações › Backup:** defina a **senha do backup** (anote em papel, guarde fora da loja).
   A pasta do backup no servidor já vem configurada.
3. Clique em **Baixar backup para este aparelho** e guarde o arquivo no computador e no pendrive.
4. Cadastre os funcionários (Usuários) e as dívidas do caderno (Fiado).

**Já usava o sistema no computador da loja?** Baixe/copie o backup mais recente de lá (`.cxbk`)
e, no site, use **Configurações › Restaurar um backup…** enviando o arquivo e informando a
senha do backup antiga.

## Rotina

| Quando | O quê |
|---|---|
| Toda semana | Configurações › **Baixar backup** › guardar no computador **e** no pendrive |
| Todo mês | Entrar no painel da Oracle e conferir se a instância está *Running* |
| Ao receber uma atualização do sistema | No servidor: `cd FluxoDeCaixa && git pull && docker compose up -d --build` |

## Problemas

| Problema | O que fazer |
|---|---|
| Site não abre | `docker compose ps` (os dois devem estar *running*); confira as portas 80/443 (passos 3 e 5) e o IP no DuckDNS. |
| Cadeado/HTTPS não aparece | O DuckDNS precisa apontar para o IP certo; veja `docker compose logs caddy`. |
| "Endereço não permitido" | O `DOMINIO` do `.env` precisa ser exatamente o endereço digitado. Depois de corrigir: `docker compose up -d`. |
| Dona esqueceu a senha | No servidor: `docker compose exec caixa python scripts/recuperar_admin.py` |
| IP do servidor mudou | Atualize o IP no duckdns.org. |
| O servidor sumiu | Crie outro (passos 2 a 6) e restaure o último backup baixado (passo 7). |

## Segurança (resumo)

- HTTPS automático (Let's Encrypt), cookies só por HTTPS e HSTS.
- Só as portas 80/443 abertas; o sistema não fica exposto diretamente (só o Caddy o alcança).
- Login com bloqueio por usuário (5 erros) **e** por aparelho (20 erros em 15 minutos).
- O sistema roda sem privilégios de administrador dentro do contêiner.
- O servidor só aceita o seu domínio (acessos pelo IP direto são recusados).
- Atualizações de segurança do Ubuntu automáticas (`unattended-upgrades`).
