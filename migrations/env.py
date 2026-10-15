"""Ambiente do Alembic. Usa a mesma engine (e PRAGMAs) do sistema."""

from __future__ import annotations

from alembic import context

# Importa os modelos para o autogenerate enxergar as tabelas.
from app.core import auditoria  # noqa: F401
from app.core.config import obter_config
from app.core.database import Base, criar_engine
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.backup import models as backup_models  # noqa: F401
from app.modules.caixa import models as caixa_models  # noqa: F401
from app.modules.configuracao import models as configuracao_models  # noqa: F401
from app.modules.fiado import models as fiado_models  # noqa: F401

alvo_metadata = Base.metadata


def rodar_migracoes() -> None:
    conexao_externa = context.config.attributes.get("connection")
    if conexao_externa is not None:
        _migrar(conexao_externa)
        return
    engine = criar_engine(obter_config())
    with engine.connect() as conexao:
        _migrar(conexao)
    engine.dispose()


def _migrar(conexao) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=conexao,
        target_metadata=alvo_metadata,
        render_as_batch=True,  # SQLite não tem ALTER TABLE completo
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


rodar_migracoes()
