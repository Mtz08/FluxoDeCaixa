"""Tabelas do fiado (caderneta): clientes e lançamentos.

Integridade (CHECKs, imutabilidade, estorno inverso) na migração 0003.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, DataHoraUTC
from app.modules.auth.models import Usuario


class TipoFiado(StrEnum):
    COMPRA = "compra"
    PAGAMENTO = "pagamento"
    ABATIMENTO = "abatimento"  # perdão/desconto/devolução de mercadoria (só admin)
    SALDO_INICIAL = "saldo_inicial"  # dívida trazida do caderno de papel (só admin)
    DEVOLUCAO_CREDITO = "devolucao_credito"  # cliente com crédito recebe o dinheiro de volta
    JUROS = "juros"  # juros por atraso de parcela, lançados no dia do pagamento/abatimento
    ESTORNO = "estorno"


# +1 aumenta a dívida do cliente; -1 diminui. Estorno = inverso do original.
SINAL_FIADO: dict[TipoFiado, int] = {
    TipoFiado.COMPRA: 1,
    TipoFiado.SALDO_INICIAL: 1,
    TipoFiado.DEVOLUCAO_CREDITO: 1,
    TipoFiado.JUROS: 1,
    TipoFiado.PAGAMENTO: -1,
    TipoFiado.ABATIMENTO: -1,
}

ROTULO_FIADO: dict[str, str] = {
    TipoFiado.COMPRA: "Compra",
    TipoFiado.PAGAMENTO: "Pagamento",
    TipoFiado.ABATIMENTO: "Abatimento",
    TipoFiado.SALDO_INICIAL: "Saldo inicial (caderno)",
    TipoFiado.DEVOLUCAO_CREDITO: "Devolução de crédito",
    TipoFiado.JUROS: "Juros por atraso",
    TipoFiado.ESTORNO: "Estorno",
}


class Cliente(Base):
    __tablename__ = "cliente"
    __table_args__ = (Index("ix_cliente_busca", "busca"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80))
    apelido: Mapped[str | None] = mapped_column(String(40))
    telefone: Mapped[str | None] = mapped_column(String(11))  # só dígitos: DDD + número
    observacao: Mapped[str | None] = mapped_column(String(300))
    # Opcionais, desligados por padrão (Configurações → campos extras do cliente).
    cpf: Mapped[str | None] = mapped_column(String(11))
    endereco: Mapped[str | None] = mapped_column(String(200))
    limite_centavos: Mapped[int | None]  # None = sem limite
    dia_pagamento: Mapped[int | None]
    # Cache do saldo (+ deve / − crédito). A fonte da verdade são os lançamentos.
    saldo_centavos: Mapped[int] = mapped_column(default=0)
    ativo: Mapped[bool] = mapped_column(default=True)
    # Texto normalizado (sem acento, minúsculo) para a busca rápida.
    busca: Mapped[str] = mapped_column(String(200))
    consentimento_em: Mapped[datetime] = mapped_column(DataHoraUTC())
    anonimizado_em: Mapped[datetime | None] = mapped_column(DataHoraUTC())
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())

    @property
    def tratamento(self) -> str:
        """Como chamar o cliente numa mensagem: apelido ou primeiro nome."""
        return self.apelido or self.nome.split()[0]

    @property
    def anonimizado(self) -> bool:
        return self.anonimizado_em is not None

    def pode_comprar_centavos(self) -> int | None:
        """Quanto ainda cabe no limite (None = sem limite)."""
        if self.limite_centavos is None:
            return None
        return max(self.limite_centavos - self.saldo_centavos, 0)

    def resumo(self) -> dict[str, object]:
        """Dados para a AUDITORIA: sem dado pessoal (nome, telefone, CPF, endereço).

        A auditoria é imutável; se guardasse dados pessoais, a anonimização (LGPD)
        não conseguiria apagá-los.
        """
        return {
            "limite_centavos": self.limite_centavos,
            "dia_pagamento": self.dia_pagamento,
            "ativo": self.ativo,
            "tem_telefone": self.telefone is not None,
            "tem_cpf": self.cpf is not None,
            "tem_endereco": self.endereco is not None,
        }

    def dados_pessoais(self) -> dict[str, object]:
        """Tudo o que temos do cliente (exportação para o titular, LGPD art. 18)."""
        return {
            "nome": self.nome,
            "apelido": self.apelido,
            "telefone": self.telefone,
            "cpf": self.cpf,
            "endereco": self.endereco,
            "observacao": self.observacao,
            "limite_centavos": self.limite_centavos,
            "dia_pagamento": self.dia_pagamento,
            "ativo": self.ativo,
        }


class FiadoLancamento(Base):
    __tablename__ = "fiado_lancamento"
    __table_args__ = (
        Index("ix_fiado_lancamento_cliente", "cliente_id"),
        Index("ix_fiado_lancamento_criado_em", "criado_em"),
        Index("ix_fiado_lancamento_data", "data_referencia"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    tipo: Mapped[str] = mapped_column(String(20))
    sinal: Mapped[int]
    valor_centavos: Mapped[int]
    descricao: Mapped[str | None] = mapped_column(String(200))
    data_referencia: Mapped[date] = mapped_column(Date)  # dia (local) da compra/pagamento
    sessao_caixa_id: Mapped[int | None] = mapped_column(ForeignKey("sessao_caixa.id"))
    # Pagamento/devolução: movimento no caixa. Compra com "pagou agora": a entrada da parte paga.
    movimento_caixa_id: Mapped[int | None] = mapped_column(
        ForeignKey("movimento_caixa.id"), unique=True
    )
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    autorizado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    estorno_de_id: Mapped[int | None] = mapped_column(
        ForeignKey("fiado_lancamento.id"), unique=True
    )
    chave_idempotencia: Mapped[str] = mapped_column(String(64), unique=True)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())

    usuario: Mapped[Usuario] = relationship(foreign_keys=[usuario_id], lazy="joined")
    original: Mapped[FiadoLancamento | None] = relationship(
        remote_side=[id], foreign_keys=[estorno_de_id], lazy="joined"
    )

    @property
    def tipo_efetivo(self) -> TipoFiado:
        if self.tipo == TipoFiado.ESTORNO and self.original is not None:
            return TipoFiado(self.original.tipo)
        return TipoFiado(self.tipo)

    @property
    def rotulo(self) -> str:
        if self.tipo == TipoFiado.ESTORNO:
            return f"Estorno de {ROTULO_FIADO[self.tipo_efetivo].lower()}"
        return ROTULO_FIADO[self.tipo]

    @property
    def valor_com_sinal(self) -> int:
        return self.sinal * self.valor_centavos


class FiadoParcela(Base):
    """Parcela de uma compra fiado parcelada. Imutável: guarda o "contrato" da compra
    (vencimento, valor, taxa de juros e carência). O que já foi pago é calculado a partir
    dos lançamentos (app/modules/fiado/parcelas.py)."""

    __tablename__ = "fiado_parcela"
    __table_args__ = (
        Index("ix_fiado_parcela_cliente", "cliente_id"),
        Index("ix_fiado_parcela_vencimento", "vencimento"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    compra_id: Mapped[int] = mapped_column(ForeignKey("fiado_lancamento.id"))
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    numero: Mapped[int]
    total: Mapped[int]
    vencimento: Mapped[date] = mapped_column(Date)
    valor_centavos: Mapped[int]
    taxa_juros_ppm: Mapped[int]  # por dia, em partes por milhão (0,033% = 330)
    carencia_dias: Mapped[int]
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())
