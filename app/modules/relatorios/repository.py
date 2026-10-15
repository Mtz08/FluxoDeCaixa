"""Consultas de relatório (só leitura) sobre caixa e fiado."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.caixa.models import MovimentoCaixa, SessaoCaixa, StatusSessao
from app.modules.fiado.models import FiadoLancamento


def movimentos_no_intervalo(db: Session, inicio: datetime, fim: datetime) -> list[MovimentoCaixa]:
    """Movimentos lançados em [inicio, fim) (UTC)."""
    consulta = (
        select(MovimentoCaixa)
        .where(MovimentoCaixa.criado_em >= inicio, MovimentoCaixa.criado_em < fim)
        .order_by(MovimentoCaixa.criado_em, MovimentoCaixa.id)
    )
    return list(db.scalars(consulta).unique())


def fechamentos_no_intervalo(db: Session, inicio: datetime, fim: datetime) -> list[SessaoCaixa]:
    consulta = (
        select(SessaoCaixa)
        .where(
            SessaoCaixa.status == StatusSessao.FECHADA,
            SessaoCaixa.fechada_em >= inicio,
            SessaoCaixa.fechada_em < fim,
        )
        .order_by(SessaoCaixa.fechada_em)
    )
    return list(db.scalars(consulta).unique())


def fiado_no_periodo(db: Session, inicio: date, fim: date) -> list[FiadoLancamento]:
    """Lançamentos do fiado pela data de referência (dia da compra/pagamento)."""
    consulta = (
        select(FiadoLancamento)
        .where(FiadoLancamento.data_referencia >= inicio, FiadoLancamento.data_referencia <= fim)
        .order_by(FiadoLancamento.data_referencia, FiadoLancamento.id)
    )
    return list(db.scalars(consulta).unique())
