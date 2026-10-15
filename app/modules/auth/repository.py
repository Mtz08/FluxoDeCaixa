"""Acesso a dados de usuários e sessões de login. Sem regra de negócio."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.auth.models import Perfil, SessaoLogin, Usuario


def contar_usuarios(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Usuario)) or 0


def contar_admins_ativos(db: Session) -> int:
    consulta = select(func.count()).where(Usuario.perfil == Perfil.ADMIN, Usuario.ativo.is_(True))
    return db.scalar(consulta) or 0


def buscar_por_login(db: Session, login: str) -> Usuario | None:
    return db.scalar(select(Usuario).where(Usuario.login == login))


def buscar_por_id(db: Session, usuario_id: int) -> Usuario | None:
    return db.get(Usuario, usuario_id)


def listar(db: Session) -> list[Usuario]:
    return list(db.scalars(select(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome)))


def buscar_sessao(db: Session, token_hash: str) -> SessaoLogin | None:
    consulta = select(SessaoLogin).where(
        SessaoLogin.token_hash == token_hash, SessaoLogin.encerrada_em.is_(None)
    )
    return db.scalar(consulta)


def encerrar_sessoes_do_usuario(db: Session, usuario_id: int, momento: datetime) -> None:
    db.execute(
        update(SessaoLogin)
        .where(SessaoLogin.usuario_id == usuario_id, SessaoLogin.encerrada_em.is_(None))
        .values(encerrada_em=momento)
    )
