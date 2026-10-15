"""Configuração de infraestrutura (caminhos, porta, tempos).

Lida de variáveis de ambiente com prefixo CAIXA_. Configurações de negócio
editáveis pela dona (nome da loja, formas de pagamento etc.) ficam no banco
(tabela `configuracao`, Etapa 5) e não aqui.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _dir_dados_padrao() -> Path:
    # Fora da pasta do código e de pastas públicas (LGPD).
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "CaixaPapelaria"


def normalizar_url_postgres(url: str) -> str:
    """postgres://... ou postgresql://... (formato do Neon/Render) -> driver psycopg."""
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo) :]
    return url


def _int_env(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome)
    return int(valor) if valor else padrao


@dataclass(frozen=True)
class Config:
    dir_dados: Path = field(default_factory=_dir_dados_padrao)
    host: str = "0.0.0.0"  # noqa: S104 - rede local da loja; ver README (não abrir porta no roteador)
    porta: int = 8765
    sessao_minutos: int = 30
    max_tentativas_login: int = 5
    bloqueio_minutos: int = 15
    senha_tamanho_minimo: int = 6
    busy_timeout_ms: int = 5000
    modo_dev: bool = False
    nome_loja: str = "Papelaria"  # padrão; a dona altera em Configurações
    agendar_backup: bool = True  # backup diário automático (desligado nos testes)
    # Nomes extras aceitos no cabeçalho Host (além de localhost, IPs da rede e nome do PC).
    hosts_extras: tuple[str, ...] = ()
    # ---- Modo nuvem (sistema publicado como site; ver docs/PUBLICAR_NA_NUVEM.md) ----
    url_publica: str | None = None  # ex.: https://minhapapelaria.duckdns.org
    cookies_seguros: bool = False  # cookies só por HTTPS (+ HSTS)
    atras_de_proxy: bool = False  # IP real do visitante vem do proxy (Caddy)
    pasta_backup_padrao: str | None = None  # pasta de backup quando a dona não configurar outra
    # Banco externo (Postgres, ex.: Neon). Sem ele, usa o SQLite local (caixa.db).
    url_banco_externo: str | None = None

    @property
    def modo_nuvem(self) -> bool:
        return self.url_publica is not None

    @property
    def caminho_banco(self) -> Path:
        return self.dir_dados / "caixa.db"

    @property
    def banco_externo(self) -> bool:
        return self.url_banco_externo is not None

    @property
    def url_banco(self) -> str:
        if self.url_banco_externo:
            return normalizar_url_postgres(self.url_banco_externo)
        return f"sqlite:///{self.caminho_banco.as_posix()}"

    @property
    def dir_logs(self) -> Path:
        return self.dir_dados / "logs"


@lru_cache
def obter_config() -> Config:
    dir_env = os.environ.get("CAIXA_DIR_DADOS")
    return Config(
        dir_dados=Path(dir_env) if dir_env else _dir_dados_padrao(),
        # Render (e outras plataformas) definem a porta em PORT.
        porta=_int_env("PORT", _int_env("CAIXA_PORTA", 8765)),
        sessao_minutos=_int_env("CAIXA_SESSAO_MINUTOS", 30),
        modo_dev=os.environ.get("CAIXA_MODO_DEV") == "1",
        nome_loja=os.environ.get("CAIXA_NOME_LOJA", "Papelaria"),
        hosts_extras=tuple(
            h.strip().lower() for h in os.environ.get("CAIXA_HOSTS", "").split(",") if h.strip()
        ),
        url_publica=os.environ.get("CAIXA_URL_PUBLICA") or None,
        cookies_seguros=os.environ.get("CAIXA_COOKIES_SEGUROS") == "1",
        atras_de_proxy=os.environ.get("CAIXA_ATRAS_DE_PROXY") == "1",
        pasta_backup_padrao=os.environ.get("CAIXA_PASTA_BACKUP") or None,
        url_banco_externo=os.environ.get("CAIXA_DATABASE_URL") or None,
    )
