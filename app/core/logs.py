"""Log da aplicação em arquivo com rotação. Nunca registrar senha nem dado de cliente."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.core.config import Config


def configurar_logs(cfg: Config) -> None:
    cfg.dir_logs.mkdir(parents=True, exist_ok=True)
    arquivo = RotatingFileHandler(
        cfg.dir_logs / "caixa.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8"
    )
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in raiz.handlers):
        raiz.addHandler(arquivo)
