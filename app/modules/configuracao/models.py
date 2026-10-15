"""Configurações editáveis pela dona (chave/valor)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, DataHoraUTC


class Configuracao(Base):
    __tablename__ = "configuracao"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    atualizado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    atualizado_em: Mapped[datetime] = mapped_column(DataHoraUTC())
