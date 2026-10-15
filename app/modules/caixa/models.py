"""Tabelas do caixa: sessões, movimentações e suas partes de pagamento.

As restrições de integridade (CHECKs, triggers de imutabilidade, índice de
"só um caixa aberto") estão na migração 0002 — o banco é a última barreira.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, DataHoraUTC
from app.modules.auth.models import Usuario


class TipoMovimento(StrEnum):
    ENTRADA = "entrada"
    SAIDA = "saida"
    SANGRIA = "sangria"
    SUPRIMENTO = "suprimento"
    RECEBIMENTO_FIADO = "recebimento_fiado"
    ESTORNO = "estorno"


# Efeito no caixa de cada tipo (o estorno usa o inverso do original).
SINAL_DO_TIPO: dict[TipoMovimento, int] = {
    TipoMovimento.ENTRADA: 1,
    TipoMovimento.RECEBIMENTO_FIADO: 1,
    TipoMovimento.SUPRIMENTO: 1,
    TipoMovimento.SAIDA: -1,
    TipoMovimento.SANGRIA: -1,
}

ROTULO_TIPO: dict[str, str] = {
    TipoMovimento.ENTRADA: "Entrada",
    TipoMovimento.SAIDA: "Saída",
    TipoMovimento.SANGRIA: "Sangria",
    TipoMovimento.SUPRIMENTO: "Suprimento",
    TipoMovimento.RECEBIMENTO_FIADO: "Recebimento de fiado",
    TipoMovimento.ESTORNO: "Estorno",
}


# Categoria reservada (criada na migração 0003) para a devolução de crédito do fiado.
# Não aparece na tela de saída manual.
CATEGORIA_DEVOLUCAO_FIADO = "Devolução de crédito a cliente (fiado)"


class StatusSessao(StrEnum):
    ABERTA = "aberta"
    FECHADA = "fechada"


class Origem(StrEnum):
    MANUAL = "manual"
    FIADO = "fiado"
    PDV = "pdv"  # reservado para o PDV futuro


class FormaPagamento(Base):
    __tablename__ = "forma_pagamento"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(40), unique=True)
    afeta_gaveta: Mapped[bool]
    ativo: Mapped[bool] = mapped_column(default=True)
    ordem: Mapped[int] = mapped_column(default=0)


class CategoriaDespesa(Base):
    __tablename__ = "categoria_despesa"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), unique=True)
    ativo: Mapped[bool] = mapped_column(default=True)


class SessaoCaixa(Base):
    __tablename__ = "sessao_caixa"
    __table_args__ = (Index("ix_sessao_caixa_aberta_em", "aberta_em"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(10))
    aberta_por: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    aberta_em: Mapped[datetime] = mapped_column(DataHoraUTC())
    fundo_troco_centavos: Mapped[int]
    fechada_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    fechada_em: Mapped[datetime | None] = mapped_column(DataHoraUTC())
    valor_contado_centavos: Mapped[int | None]
    valor_esperado_centavos: Mapped[int | None]
    diferenca_centavos: Mapped[int | None]
    justificativa: Mapped[str | None] = mapped_column(String(500))
    chave_idempotencia: Mapped[str] = mapped_column(String(36), unique=True)

    usuario_abertura: Mapped[Usuario] = relationship(foreign_keys=[aberta_por], lazy="joined")
    usuario_fechamento: Mapped[Usuario | None] = relationship(
        foreign_keys=[fechada_por], lazy="joined"
    )

    @property
    def aberta(self) -> bool:
        return self.status == StatusSessao.ABERTA


class MovimentoCaixa(Base):
    __tablename__ = "movimento_caixa"
    __table_args__ = (
        Index("ix_movimento_caixa_sessao", "sessao_id"),
        Index("ix_movimento_caixa_criado_em", "criado_em"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sessao_id: Mapped[int] = mapped_column(ForeignKey("sessao_caixa.id"))
    tipo: Mapped[str] = mapped_column(String(20))
    sinal: Mapped[int]
    valor_centavos: Mapped[int]
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categoria_despesa.id"))
    descricao: Mapped[str | None] = mapped_column(String(200))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    autorizado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    estorno_de_id: Mapped[int | None] = mapped_column(ForeignKey("movimento_caixa.id"), unique=True)
    # False só no estorno de lançamento de caixa já fechado (não mexe na gaveta de hoje).
    considera_gaveta: Mapped[bool] = mapped_column(default=True)
    origem: Mapped[str] = mapped_column(String(10), default=Origem.MANUAL)
    origem_id: Mapped[int | None]
    chave_idempotencia: Mapped[str] = mapped_column(String(64), unique=True)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())

    partes: Mapped[list[MovimentoPagamento]] = relationship(
        lazy="selectin", order_by="MovimentoPagamento.id"
    )
    categoria: Mapped[CategoriaDespesa | None] = relationship(lazy="joined")
    usuario: Mapped[Usuario] = relationship(foreign_keys=[usuario_id], lazy="joined")
    original: Mapped[MovimentoCaixa | None] = relationship(
        remote_side=[id], foreign_keys=[estorno_de_id], lazy="joined"
    )

    @property
    def tipo_efetivo(self) -> TipoMovimento:
        """Para estorno, o tipo do lançamento original (ex.: estorno de entrada)."""
        if self.tipo == TipoMovimento.ESTORNO and self.original is not None:
            return TipoMovimento(self.original.tipo)
        return TipoMovimento(self.tipo)

    @property
    def rotulo(self) -> str:
        if self.tipo == TipoMovimento.ESTORNO:
            return f"Estorno de {ROTULO_TIPO[self.tipo_efetivo].lower()}"
        return ROTULO_TIPO[self.tipo]

    @property
    def valor_com_sinal(self) -> int:
        return self.sinal * self.valor_centavos


class MovimentoPagamento(Base):
    __tablename__ = "movimento_pagamento"
    __table_args__ = (Index("ix_movimento_pagamento_movimento", "movimento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movimento_id: Mapped[int] = mapped_column(ForeignKey("movimento_caixa.id"))
    forma_pagamento_id: Mapped[int] = mapped_column(ForeignKey("forma_pagamento.id"))
    valor_centavos: Mapped[int]

    forma: Mapped[FormaPagamento] = relationship(lazy="joined")
