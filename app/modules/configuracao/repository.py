"""Acesso à tabela de configurações e aos cadastros de formas/categorias."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.caixa.models import CategoriaDespesa, FormaPagamento
from app.modules.configuracao.models import Configuracao


def todas(db: Session) -> dict[str, str]:
    return {c.chave: c.valor for c in db.scalars(select(Configuracao))}


def gravar(db: Session, chave: str, valor: str, usuario_id: int, momento: datetime) -> None:
    item = db.get(Configuracao, chave)
    if item is None:
        db.add(
            Configuracao(chave=chave, valor=valor, atualizado_por=usuario_id, atualizado_em=momento)
        )
    else:
        item.valor = valor
        item.atualizado_por = usuario_id
        item.atualizado_em = momento


def formas(db: Session) -> list[FormaPagamento]:
    return list(
        db.scalars(select(FormaPagamento).order_by(FormaPagamento.ordem, FormaPagamento.nome))
    )


def categorias(db: Session) -> list[CategoriaDespesa]:
    return list(db.scalars(select(CategoriaDespesa).order_by(CategoriaDespesa.nome)))


def forma_por_nome(db: Session, nome: str) -> FormaPagamento | None:
    return db.scalar(select(FormaPagamento).where(FormaPagamento.nome == nome))


def categoria_por_nome(db: Session, nome: str) -> CategoriaDespesa | None:
    return db.scalar(select(CategoriaDespesa).where(CategoriaDespesa.nome == nome))
