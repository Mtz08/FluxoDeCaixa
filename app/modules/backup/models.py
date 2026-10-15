"""Registro de cada tentativa de backup (uma linha por destino)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, DataHoraUTC


class Gatilho(StrEnum):
    FECHAMENTO = "fechamento"
    DIARIO = "diario"
    MANUAL = "manual"
    PRE_RESTAURACAO = "pre_restauracao"


class BackupRegistro(Base):
    __tablename__ = "backup_registro"
    __table_args__ = (Index("ix_backup_registro_criado_em", "criado_em"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gatilho: Mapped[str] = mapped_column(String(20))
    arquivo: Mapped[str | None] = mapped_column(String(200))
    destino: Mapped[str | None] = mapped_column(String(500))
    tamanho_bytes: Mapped[int | None]
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))
    erro: Mapped[str | None] = mapped_column(String(500))
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())

    @property
    def ok(self) -> bool:
        return self.status == "ok"
