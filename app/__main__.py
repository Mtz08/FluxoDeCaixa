"""Inicia o servidor: python -m app

Host e porta vêm da mesma configuração usada no QR Code do celular
(CAIXA_PORTA, padrão 8765), para os dois nunca ficarem diferentes.
"""

from __future__ import annotations

import uvicorn

from app.core.config import obter_config


def principal() -> None:
    cfg = obter_config()
    uvicorn.run(
        "app.main:criar_app",
        factory=True,
        host=cfg.host,
        port=cfg.porta,
        proxy_headers=False,  # não há proxy: ninguém pode forjar o IP pelo cabeçalho
        server_header=False,
        access_log=False,  # as URLs podem ter nome de cliente (busca): não registrar (LGPD)
    )


if __name__ == "__main__":
    principal()
