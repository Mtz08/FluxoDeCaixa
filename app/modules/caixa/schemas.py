"""Formato dos dados de entrada do caixa. Regras de negócio ficam no service."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.modules.caixa.models import TipoMovimento

# Tipos que a tela do caixa lança diretamente (recebimento de fiado vem do módulo fiado).
TIPOS_MANUAIS = (
    TipoMovimento.ENTRADA,
    TipoMovimento.SAIDA,
    TipoMovimento.SANGRIA,
    TipoMovimento.SUPRIMENTO,
)


class Parte(BaseModel):
    forma_id: int
    valor_centavos: int = Field(gt=0)


class NovoLancamento(BaseModel):
    tipo: TipoMovimento
    partes: list[Parte] = Field(min_length=1)
    categoria_id: int | None = None
    descricao: str | None = None
    chave: UUID
    confirmar_valor_alto: bool = False

    @field_validator("descricao")
    @classmethod
    def _limpar_descricao(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        valor = " ".join(valor.split())
        if len(valor) > 200:
            raise ValueError("A descrição pode ter no máximo 200 caracteres.")
        return valor or None

    @property
    def total_centavos(self) -> int:
        return sum(p.valor_centavos for p in self.partes)
