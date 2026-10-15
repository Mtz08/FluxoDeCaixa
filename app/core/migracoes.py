"""Aplica as migrações do banco (usado pelo instalar/iniciar e pelos testes)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as ConfigAlembic
from sqlalchemy import Engine

RAIZ = Path(__file__).resolve().parents[2]


def migrar(engine: Engine) -> None:
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    with engine.connect() as conexao:
        cfg.attributes["connection"] = conexao
        command.upgrade(cfg, "head")
        conexao.commit()
