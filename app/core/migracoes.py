"""Aplica as migrações do banco (usado pelo instalar/iniciar, pela restauração e pelos testes)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as ConfigAlembic
from sqlalchemy import Connection, Engine

RAIZ = Path(__file__).resolve().parents[2]


def _config_alembic(conexao: Connection) -> ConfigAlembic:
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    cfg.attributes["connection"] = conexao
    return cfg


def migrar(engine: Engine) -> None:
    """Leva o banco à versão atual e confirma (commit)."""
    with engine.connect() as conexao:
        command.upgrade(_config_alembic(conexao), "head")
        conexao.commit()


def migrar_na_transacao(conexao: Connection) -> None:
    """Igual a migrar(), mas dentro da transação de quem chamou (sem commit).

    No PostgreSQL a estrutura também é transacional: se algo falhar depois,
    o rollback desfaz tudo, inclusive as tabelas criadas aqui.
    """
    command.upgrade(_config_alembic(conexao), "head")
