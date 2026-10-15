"""Dependências do FastAPI: banco, configuração, usuário logado, CSRF."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Config
from app.core.database import sessao_por_requisicao
from app.core.erros import (
    CsrfInvalido,
    NaoAutenticado,
    PrecisaTrocarSenha,
    SemPermissao,
    SemUsuarios,
)
from app.core.seguranca import CABECALHO_CSRF, CAMPO_CSRF, COOKIE_CSRF, COOKIE_SESSAO, tokens_iguais
from app.modules.auth import service as auth_service
from app.modules.auth.models import Usuario

_METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})


def obter_db(request: Request) -> Iterator[Session]:
    yield from sessao_por_requisicao(request.app.state.fabrica_sessao)


def obter_cfg(request: Request, db: Session = Depends(obter_db)) -> Config:
    """Config da instalação + ajustes da tela de Configurações (nome da loja, sessão)."""
    from app.modules.configuracao.service import config_efetiva  # evita import circular

    return config_efetiva(db, request.app.state.config)


def ip_cliente(request: Request) -> str | None:
    return request.client.host if request.client else None


async def verificar_csrf(request: Request) -> None:
    """Aplicada a todas as rotas: POST sem o código de segurança é recusado."""
    if request.method in _METODOS_SEGUROS:
        return
    enviado = request.headers.get(CABECALHO_CSRF)
    if not enviado:
        formulario = await request.form()
        valor = formulario.get(CAMPO_CSRF)
        enviado = valor if isinstance(valor, str) else None
    if not tokens_iguais(enviado, request.cookies.get(COOKIE_CSRF)):
        raise CsrfInvalido()


def usuario_logado(
    request: Request, db: Session = Depends(obter_db), cfg: Config = Depends(obter_cfg)
) -> Usuario:
    """Usuário com sessão válida (pode estar com senha provisória)."""
    if not auth_service.existe_usuario(db):
        raise SemUsuarios()
    usuario = auth_service.usuario_da_sessao(db, cfg, request.cookies.get(COOKIE_SESSAO))
    if usuario is None:
        raise NaoAutenticado()
    request.state.usuario = usuario
    return usuario


def usuario_atual(usuario: Usuario = Depends(usuario_logado)) -> Usuario:
    """Usuário liberado para usar o sistema (já trocou a senha provisória)."""
    if usuario.trocar_senha:
        raise PrecisaTrocarSenha()
    return usuario


def exigir_admin(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    if not usuario.eh_admin:
        raise SemPermissao()
    return usuario
