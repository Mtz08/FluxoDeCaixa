"""Trilha de auditoria: só inserção (o banco bloqueia UPDATE/DELETE por trigger).

Nunca gravar senha, hash de senha ou token. Dados pessoais de clientes só
quando forem o próprio objeto auditado.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import Base, DataHoraUTC
from app.core.datas import agora_utc

CAMPOS_PROIBIDOS = frozenset({"senha", "senha_hash", "token", "token_hash", "csrf_token"})


class Auditoria(Base):
    __tablename__ = "auditoria"
    __table_args__ = (
        Index("ix_auditoria_criado_em", "criado_em"),
        Index("ix_auditoria_entidade", "entidade", "entidade_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    acao: Mapped[str] = mapped_column(String(60))
    entidade: Mapped[str] = mapped_column(String(60))
    entidade_id: Mapped[int | None]
    antes_json: Mapped[str | None] = mapped_column(Text)
    depois_json: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC())


def _serializar(dados: dict[str, Any] | None) -> str | None:
    if dados is None:
        return None
    limpo = {k: v for k, v in dados.items() if k not in CAMPOS_PROIBIDOS}
    return json.dumps(limpo, ensure_ascii=False, default=str, sort_keys=True)


def registrar(
    db: Session,
    *,
    acao: str,
    entidade: str,
    entidade_id: int | None = None,
    usuario_id: int | None = None,
    antes: dict[str, Any] | None = None,
    depois: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Adiciona o registro na transação corrente (o commit é de quem chamou)."""
    db.add(
        Auditoria(
            usuario_id=usuario_id,
            acao=acao,
            entidade=entidade,
            entidade_id=entidade_id,
            antes_json=_serializar(antes),
            depois_json=_serializar(depois),
            ip=ip,
            criado_em=agora_utc(),
        )
    )


def ultimo_registro(
    db: Session, *, acao: str, entidade: str, entidade_id: int
) -> dict[str, Any] | None:
    """Dados ("depois") do registro mais recente dessa ação sobre a entidade."""
    consulta = (
        select(Auditoria.depois_json)
        .where(
            Auditoria.acao == acao,
            Auditoria.entidade == entidade,
            Auditoria.entidade_id == entidade_id,
        )
        .order_by(Auditoria.id.desc())
        .limit(1)
    )
    texto = db.scalar(consulta)
    if texto is None:
        return None
    dados: dict[str, Any] = json.loads(texto)
    return dados
