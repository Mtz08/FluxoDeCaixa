"""Banco de dados: engine, travas de segurança e sessão por requisição.

Dois bancos suportados, com as MESMAS garantias:
- SQLite (instalação na loja, arquivo caixa.db, funciona sem internet);
- PostgreSQL (publicação na nuvem, ex.: Neon), via CAIXA_DATABASE_URL.

SQLite:
- WAL: leitores não bloqueiam o escritor (PC + celular ao mesmo tempo).
- synchronous=FULL: após o COMMIT o dado está no disco, mesmo com queda de energia.
- foreign_keys=ON: SQLite vem com isso DESLIGADO por padrão.
- busy_timeout: espera o outro terminar em vez de falhar na hora.
- BEGIN IMMEDIATE: a transação pega a trava de escrita já no início, evitando
  erro de "database is locked" no meio de uma operação de dinheiro.

PostgreSQL:
- cada transação pega uma trava exclusiva (pg_advisory_xact_lock) logo no início:
  as operações ficam em fila, como no SQLite — nada de duas gravações de dinheiro
  se cruzando. Espera no máximo busy_timeout_ms (depois: "sistema ocupado").
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Engine, MetaData, create_engine, event, text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.core.config import Config

# Nomes previsíveis para restrições (o Alembic precisa deles no SQLite).
CONVENCAO_NOMES = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCAO_NOMES)


class DataHoraUTC(TypeDecorator[datetime]):
    """Grava sempre em UTC (texto ordenável) e devolve datetime com fuso UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Data/hora sem fuso horário: use agora_utc().")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


# Número fixo da trava única de escrita no PostgreSQL.
TRAVA_ESCRITA_PG = 72_011_001


def eh_sqlite(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def criar_engine(config: Config) -> Engine:
    config.dir_dados.mkdir(parents=True, exist_ok=True)
    if config.banco_externo:
        return _engine_postgres(config)
    return _engine_sqlite(config)


def _engine_sqlite(config: Config) -> Engine:
    engine = create_engine(
        config.url_banco,
        connect_args={"check_same_thread": False, "timeout": config.busy_timeout_ms / 1000},
    )

    @event.listens_for(engine, "connect")
    def _ao_conectar(dbapi_conn: Any, _registro: Any) -> None:
        # Controle manual de transação (o sqlite3 do Python faz BEGIN implícito errado).
        dbapi_conn.isolation_level = None
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={int(config.busy_timeout_ms)}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _ao_iniciar(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def _engine_postgres(config: Config) -> Engine:
    engine = create_engine(
        config.url_banco,
        pool_pre_ping=True,  # o Neon desliga quando parado: testa a conexão antes de usar
        pool_recycle=300,
        pool_size=3,
        max_overflow=2,
    )

    @event.listens_for(engine, "begin")
    def _ao_iniciar(conn: Any) -> None:
        conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{int(config.busy_timeout_ms)}ms'")
        conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({TRAVA_ESCRITA_PG})")

    return engine


def criar_fabrica_sessao(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def verificar_integridade(engine: Engine) -> list[str]:
    """SQLite: PRAGMA integrity_check. Postgres: só confere a conexão (o servidor cuida
    da integridade física). Lista vazia = banco íntegro."""
    if not eh_sqlite(engine):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.rollback()
        return []
    with engine.connect() as conn:
        linhas = [str(r[0]) for r in conn.execute(text("PRAGMA integrity_check"))]
        conn.rollback()
    return [] if linhas == ["ok"] else linhas


def sessao_por_requisicao(fabrica: sessionmaker[Session]) -> Iterator[Session]:
    """Abre uma sessão; o que não foi confirmado com commit() é descartado."""
    db = fabrica()
    try:
        yield db
    finally:
        # close() desfaz a transação pendente sem "expirar" os objetos já lidos,
        # que ainda podem ser usados ao montar páginas de erro.
        db.close()
