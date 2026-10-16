"""Formato dos dados de entrada do fiado. Regras de negócio ficam no service."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.modules.caixa.schemas import Parte
from app.modules.fiado.calculo import normalizar_cpf, normalizar_telefone


def _texto_opcional(valor: str | None, maximo: int, campo: str) -> str | None:
    if valor is None:
        return None
    valor = " ".join(valor.split())
    if len(valor) > maximo:
        raise ValueError(f"{campo} pode ter no máximo {maximo} caracteres.")
    return valor or None


class DadosCliente(BaseModel):
    nome: str
    apelido: str | None = None
    telefone: str | None = None
    observacao: str | None = None
    limite_centavos: int | None = Field(default=None, ge=0)
    dia_pagamento: int | None = Field(default=None, ge=1, le=31)
    consentimento: bool = False
    # Opcionais: só gravados se a dona ligar "campos extras" nas configurações.
    cpf: str | None = None
    endereco: str | None = None

    @field_validator("nome")
    @classmethod
    def _nome(cls, valor: str) -> str:
        valor = " ".join(valor.split())
        if not 2 <= len(valor) <= 80:
            raise ValueError("O nome deve ter entre 2 e 80 letras.")
        return valor

    @field_validator("apelido")
    @classmethod
    def _apelido(cls, valor: str | None) -> str | None:
        return _texto_opcional(valor, 40, "O apelido")

    @field_validator("observacao")
    @classmethod
    def _observacao(cls, valor: str | None) -> str | None:
        return _texto_opcional(valor, 300, "A observação")

    @field_validator("telefone")
    @classmethod
    def _telefone(cls, valor: str | None) -> str | None:
        return normalizar_telefone(valor)

    @field_validator("cpf")
    @classmethod
    def _cpf(cls, valor: str | None) -> str | None:
        return normalizar_cpf(valor)

    @field_validator("endereco")
    @classmethod
    def _endereco(cls, valor: str | None) -> str | None:
        return _texto_opcional(valor, 200, "O endereço")


class NovaCompra(BaseModel):
    cliente_id: int
    valor_total_centavos: int = Field(gt=0)
    # "Pagou agora" (venda mista): o que entra no caixa na hora.
    partes_pagas: list[Parte] = Field(default_factory=list)
    descricao: str | None = None
    data_referencia: date
    chave: UUID
    confirmar_valor_alto: bool = False
    # Parcelamento: 0 = fiado comum (sem vencimento e sem juros); 1..24 = parcelas mensais.
    parcelas: int = Field(default=0, ge=0, le=24)
    primeiro_vencimento: date | None = None

    @field_validator("descricao")
    @classmethod
    def _descricao(cls, valor: str | None) -> str | None:
        return _texto_opcional(valor, 200, "A descrição")

    @property
    def pago_agora_centavos(self) -> int:
        return sum(p.valor_centavos for p in self.partes_pagas)

    @property
    def valor_fiado_centavos(self) -> int:
        return self.valor_total_centavos - self.pago_agora_centavos


class Excedente(StrEnum):
    """O que fazer quando o pagamento é maior que a dívida."""

    TROCO = "troco"
    CREDITO = "credito"


class NovoPagamento(BaseModel):
    cliente_id: int
    partes: list[Parte] = Field(min_length=1)
    excedente: Excedente | None = None
    chave: UUID
    confirmar_valor_alto: bool = False

    @property
    def total_centavos(self) -> int:
        return sum(p.valor_centavos for p in self.partes)
