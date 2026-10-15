"""Consultas do caixa. Sem regra de negócio."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.caixa.models import (
    CATEGORIA_DEVOLUCAO_FIADO,
    CategoriaDespesa,
    FormaPagamento,
    MovimentoCaixa,
    MovimentoPagamento,
    SessaoCaixa,
    StatusSessao,
)


def sessao_aberta(db: Session) -> SessaoCaixa | None:
    return db.scalar(select(SessaoCaixa).where(SessaoCaixa.status == StatusSessao.ABERTA))


def buscar_sessao(db: Session, sessao_id: int) -> SessaoCaixa | None:
    return db.get(SessaoCaixa, sessao_id)


def sessao_por_chave(db: Session, chave: str) -> SessaoCaixa | None:
    return db.scalar(select(SessaoCaixa).where(SessaoCaixa.chave_idempotencia == chave))


def buscar_movimento(db: Session, movimento_id: int) -> MovimentoCaixa | None:
    return db.get(MovimentoCaixa, movimento_id)


def movimento_por_chave(db: Session, chave: str) -> MovimentoCaixa | None:
    return db.scalar(select(MovimentoCaixa).where(MovimentoCaixa.chave_idempotencia == chave))


def estorno_de(db: Session, movimento_id: int) -> MovimentoCaixa | None:
    return db.scalar(select(MovimentoCaixa).where(MovimentoCaixa.estorno_de_id == movimento_id))


def ids_estornados(db: Session, movimento_ids: list[int]) -> set[int]:
    if not movimento_ids:
        return set()
    consulta = select(MovimentoCaixa.estorno_de_id).where(
        MovimentoCaixa.estorno_de_id.in_(movimento_ids)
    )
    return {i for i in db.scalars(consulta) if i is not None}


def movimentos_da_sessao(db: Session, sessao_id: int) -> list[MovimentoCaixa]:
    consulta = (
        select(MovimentoCaixa)
        .where(MovimentoCaixa.sessao_id == sessao_id)
        .order_by(MovimentoCaixa.id)
    )
    return list(db.scalars(consulta).unique())


def formas_ativas(db: Session) -> list[FormaPagamento]:
    consulta = (
        select(FormaPagamento)
        .where(FormaPagamento.ativo.is_(True))
        .order_by(FormaPagamento.ordem, FormaPagamento.nome)
    )
    return list(db.scalars(consulta))


def buscar_forma(db: Session, forma_id: int) -> FormaPagamento | None:
    return db.get(FormaPagamento, forma_id)


def forma_dinheiro(db: Session) -> FormaPagamento | None:
    consulta = (
        select(FormaPagamento)
        .where(FormaPagamento.afeta_gaveta.is_(True), FormaPagamento.ativo.is_(True))
        .order_by(FormaPagamento.ordem)
    )
    return db.scalars(consulta).first()


def categorias_ativas(db: Session) -> list[CategoriaDespesa]:
    """Categorias para a saída manual (sem a categoria reservada do fiado)."""
    consulta = (
        select(CategoriaDespesa)
        .where(
            CategoriaDespesa.ativo.is_(True),
            CategoriaDespesa.nome != CATEGORIA_DEVOLUCAO_FIADO,
        )
        .order_by(CategoriaDespesa.nome)
    )
    return list(db.scalars(consulta))


def categoria_devolucao_fiado(db: Session) -> CategoriaDespesa | None:
    consulta = select(CategoriaDespesa).where(CategoriaDespesa.nome == CATEGORIA_DEVOLUCAO_FIADO)
    return db.scalar(consulta)


def buscar_categoria(db: Session, categoria_id: int) -> CategoriaDespesa | None:
    return db.get(CategoriaDespesa, categoria_id)


def ultimo_registro_em(db: Session) -> datetime | None:
    """Data/hora do registro mais recente (para detectar relógio do PC atrasado)."""
    ultimo_mov = db.scalar(select(func.max(MovimentoCaixa.criado_em)))
    ultima_sessao = db.scalar(select(func.max(SessaoCaixa.aberta_em)))
    datas = [d for d in (ultimo_mov, ultima_sessao) if d is not None]
    return max(datas) if datas else None


def movimentos_com_partes_divergentes(db: Session) -> list[int]:
    """Movimentos cuja soma das partes difere do valor (não deveria existir)."""
    soma = (
        select(
            MovimentoPagamento.movimento_id,
            func.sum(MovimentoPagamento.valor_centavos).label("soma"),
        )
        .group_by(MovimentoPagamento.movimento_id)
        .subquery()
    )
    consulta = (
        select(MovimentoCaixa.id)
        .outerjoin(soma, soma.c.movimento_id == MovimentoCaixa.id)
        .where(func.coalesce(soma.c.soma, 0) != MovimentoCaixa.valor_centavos)
        .order_by(MovimentoCaixa.id)
    )
    return list(db.scalars(consulta))


def sessoes_fechadas(db: Session) -> list[SessaoCaixa]:
    consulta = (
        select(SessaoCaixa)
        .where(SessaoCaixa.status == StatusSessao.FECHADA)
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(consulta).unique())
