"""Consultas do fiado. Sem regra de negócio."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.fiado.models import Cliente, FiadoLancamento

LIMITE_BUSCA = 50


def buscar_cliente(db: Session, cliente_id: int) -> Cliente | None:
    return db.get(Cliente, cliente_id)


def buscar_clientes(db: Session, termo_normalizado: str, incluir_inativos: bool) -> list[Cliente]:
    consulta = select(Cliente).order_by(Cliente.nome).limit(LIMITE_BUSCA)
    for palavra in termo_normalizado.split():
        consulta = consulta.where(Cliente.busca.contains(palavra, autoescape=True))
    if not incluir_inativos:
        consulta = consulta.where(Cliente.ativo.is_(True))
    return list(db.scalars(consulta))


def clientes_com_saldo(db: Session) -> list[Cliente]:
    consulta = select(Cliente).where(Cliente.saldo_centavos != 0).order_by(Cliente.nome)
    return list(db.scalars(consulta))


def todos_clientes(db: Session) -> list[Cliente]:
    """Relê do disco (populate_existing): usado pela verificação de saldos."""
    consulta = select(Cliente).order_by(Cliente.id).execution_options(populate_existing=True)
    return list(db.scalars(consulta))


def lancamentos_do_cliente(db: Session, cliente_id: int) -> list[FiadoLancamento]:
    consulta = (
        select(FiadoLancamento)
        .where(FiadoLancamento.cliente_id == cliente_id)
        .order_by(FiadoLancamento.data_referencia, FiadoLancamento.id)
    )
    return list(db.scalars(consulta).unique())


def lancamentos_de_clientes(db: Session, cliente_ids: list[int]) -> list[FiadoLancamento]:
    if not cliente_ids:
        return []
    consulta = select(FiadoLancamento).where(FiadoLancamento.cliente_id.in_(cliente_ids))
    return list(db.scalars(consulta).unique())


def buscar_lancamento(db: Session, lancamento_id: int) -> FiadoLancamento | None:
    return db.get(FiadoLancamento, lancamento_id)


def lancamento_por_chave(db: Session, chave: str) -> FiadoLancamento | None:
    return db.scalar(select(FiadoLancamento).where(FiadoLancamento.chave_idempotencia == chave))


def estorno_de(db: Session, lancamento_id: int) -> FiadoLancamento | None:
    consulta = select(FiadoLancamento).where(FiadoLancamento.estorno_de_id == lancamento_id)
    return db.scalar(consulta)


def saldo_calculado(db: Session, cliente_id: int) -> int:
    consulta = select(
        func.coalesce(func.sum(FiadoLancamento.sinal * FiadoLancamento.valor_centavos), 0)
    ).where(FiadoLancamento.cliente_id == cliente_id)
    return int(db.scalar(consulta) or 0)
