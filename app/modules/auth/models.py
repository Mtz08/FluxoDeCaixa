"""Tabelas de usuários e sessões de login."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, DataHoraUTC


class Perfil(StrEnum):
    ADMIN = "admin"
    OPERADOR = "operador"


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (CheckConstraint("perfil IN ('admin', 'operador')", name="perfil"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80))
    login: Mapped[str] = mapped_column(String(40), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    perfil: Mapped[str] = mapped_column(String(20))
    ativo: Mapped[bool] = mapped_column(default=True)
    trocar_senha: Mapped[bool] = mapped_column(default=False)
    falhas_login: Mapped[int] = mapped_column(default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DataHoraUTC())
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())

    @property
    def eh_admin(self) -> bool:
        return self.perfil == Perfil.ADMIN

    def resumo(self) -> dict[str, object]:
        """Dados para auditoria (sem senha)."""
        return {"nome": self.nome, "login": self.login, "perfil": self.perfil, "ativo": self.ativo}


class SessaoLogin(Base):
    __tablename__ = "sessao_login"
    __table_args__ = (Index("ix_sessao_login_usuario", "usuario_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    # Guardamos só o hash: quem copiar o banco não consegue usar o cookie.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())
    ultimo_acesso: Mapped[datetime] = mapped_column(DataHoraUTC())
    encerrada_em: Mapped[datetime | None] = mapped_column(DataHoraUTC())
    ip: Mapped[str | None] = mapped_column(String(45))
