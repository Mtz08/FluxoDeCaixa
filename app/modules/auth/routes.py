"""Telas de login, primeiro acesso, troca de senha e cadastro de usuários."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Config
from app.core.deps import exigir_admin, ip_cliente, obter_cfg, obter_db, usuario_logado
from app.core.erros import ErroNegocio
from app.core.seguranca import COOKIE_SESSAO
from app.core.templates import opcoes_cookie, redirecionar, renderizar
from app.modules.auth import service
from app.modules.auth.models import Perfil, Usuario
from app.modules.auth.schemas import EdicaoUsuario, NovoUsuario, primeira_mensagem

router = APIRouter()


def _gravar_cookie_sessao(request: Request, resposta: Response, token: str) -> None:
    # Sem max_age: some ao fechar o navegador. A expiração por inatividade é no servidor.
    resposta.set_cookie(COOKIE_SESSAO, token, **opcoes_cookie(request))


# ---------------------------------------------------------------- primeiro acesso


@router.get("/primeiro-acesso")
def primeiro_acesso_form(request: Request, db: Session = Depends(obter_db)) -> Response:
    if service.existe_usuario(db):
        return redirecionar(request, "/login")
    return renderizar(request, "auth/primeiro_acesso.html", {"dados": {}})


@router.post("/primeiro-acesso")
def primeiro_acesso(
    request: Request,
    nome: str = Form(""),
    login: str = Form(""),
    senha: str = Form(""),
    confirmacao: str = Form(""),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    ip = ip_cliente(request)
    try:
        dados = NovoUsuario(nome=nome, login=login, perfil=Perfil.ADMIN, senha=senha)
        admin = service.criar_primeiro_admin(db, cfg, dados, confirmacao, ip)
    except (ValidationError, ErroNegocio) as erro:
        msg = primeira_mensagem(erro) if isinstance(erro, ValidationError) else erro.mensagem
        contexto = {"erro": msg, "dados": {"nome": nome, "login": login}}
        return renderizar(request, "auth/primeiro_acesso.html", contexto, status=400)
    token = service.iniciar_sessao(db, admin, ip)
    resposta = redirecionar(request, "/", "Administrador criado. Bem-vinda ao sistema!")
    _gravar_cookie_sessao(request, resposta, token)
    return resposta


# ---------------------------------------------------------------- login / sair


@router.get("/login")
def login_form(request: Request, db: Session = Depends(obter_db)) -> Response:
    if not service.existe_usuario(db):
        return redirecionar(request, "/primeiro-acesso")
    return renderizar(request, "auth/login.html")


@router.post("/login")
def login(
    request: Request,
    login: str = Form(""),
    senha: str = Form(""),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    ip = ip_cliente(request)
    limitador = request.app.state.limitador_login
    if limitador.bloqueado(ip):
        msg = "Muitas tentativas a partir deste aparelho. Espere 15 minutos e tente de novo."
        return renderizar(request, "auth/login.html", {"erro": msg, "login": login}, status=429)
    try:
        usuario = service.autenticar(db, cfg, login, senha, ip)
    except ErroNegocio as erro:
        limitador.registrar_falha(ip)
        contexto = {"erro": erro.mensagem, "login": login}
        return renderizar(request, "auth/login.html", contexto, status=400)
    limitador.limpar(ip)
    # Troca rápida de operador: a sessão anterior neste navegador é encerrada.
    service.encerrar_sessao(db, request.cookies.get(COOKIE_SESSAO), ip)
    token = service.iniciar_sessao(db, usuario, ip)
    resposta = redirecionar(request, "/")
    _gravar_cookie_sessao(request, resposta, token)
    return resposta


@router.post("/sair")
def sair(request: Request, db: Session = Depends(obter_db)) -> Response:
    service.encerrar_sessao(db, request.cookies.get(COOKIE_SESSAO), ip_cliente(request))
    resposta = redirecionar(request, "/login")
    resposta.delete_cookie(COOKIE_SESSAO, **opcoes_cookie(request))
    return resposta


# ---------------------------------------------------------------- trocar senha


@router.get("/trocar-senha")
def trocar_senha_form(request: Request, usuario: Usuario = Depends(usuario_logado)) -> Response:
    return renderizar(request, "auth/trocar_senha.html")


@router.post("/trocar-senha")
def trocar_senha(
    request: Request,
    atual: str = Form(""),
    nova: str = Form(""),
    confirmacao: str = Form(""),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    try:
        ip = ip_cliente(request)
        service.trocar_propria_senha(db, cfg, usuario, atual, nova, confirmacao, ip)
    except ErroNegocio as erro:
        return renderizar(request, "auth/trocar_senha.html", {"erro": erro.mensagem}, status=400)
    return redirecionar(request, "/", "Senha alterada.")


# ---------------------------------------------------------------- usuários (admin)


@router.get("/usuarios")
def usuarios(
    request: Request, admin: Usuario = Depends(exigir_admin), db: Session = Depends(obter_db)
) -> Response:
    lista = service.listar_usuarios(db, admin)
    return renderizar(request, "auth/usuarios.html", {"usuarios": lista})


@router.get("/usuarios/novo")
def novo_usuario_form(request: Request, admin: Usuario = Depends(exigir_admin)) -> Response:
    return renderizar(request, "auth/usuario_novo.html", {"dados": {"perfil": "operador"}})


@router.post("/usuarios/novo")
def novo_usuario(
    request: Request,
    nome: str = Form(""),
    login: str = Form(""),
    perfil: str = Form("operador"),
    senha: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    try:
        dados = NovoUsuario(nome=nome, login=login, perfil=perfil, senha=senha)  # type: ignore[arg-type]
        criado = service.criar_usuario(db, cfg, admin, dados, ip_cliente(request))
    except (ValidationError, ErroNegocio) as erro:
        msg = primeira_mensagem(erro) if isinstance(erro, ValidationError) else erro.mensagem
        contexto = {"erro": msg, "dados": {"nome": nome, "login": login, "perfil": perfil}}
        return renderizar(request, "auth/usuario_novo.html", contexto, status=400)
    aviso = f"Usuário '{criado.login}' criado. A senha provisória será trocada no primeiro acesso."
    return redirecionar(request, "/usuarios", aviso)


@router.get("/usuarios/{usuario_id}")
def editar_usuario_form(
    request: Request,
    usuario_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    alvo = service.obter_usuario(db, admin, usuario_id)
    return renderizar(request, "auth/usuario_editar.html", {"alvo": alvo})


@router.post("/usuarios/{usuario_id}")
def editar_usuario(
    request: Request,
    usuario_id: int,
    nome: str = Form(""),
    perfil: str = Form("operador"),
    ativo: str | None = Form(None),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        dados = EdicaoUsuario(nome=nome, perfil=perfil, ativo=ativo is not None)  # type: ignore[arg-type]
        service.editar_usuario(db, admin, usuario_id, dados, ip_cliente(request))
    except (ValidationError, ErroNegocio) as erro:
        db.rollback()
        msg = primeira_mensagem(erro) if isinstance(erro, ValidationError) else erro.mensagem
        alvo = service.obter_usuario(db, admin, usuario_id)
        return renderizar(
            request, "auth/usuario_editar.html", {"alvo": alvo, "erro": msg}, status=400
        )
    return redirecionar(request, "/usuarios", "Usuário atualizado.")


@router.post("/usuarios/{usuario_id}/senha")
def redefinir_senha(
    request: Request,
    usuario_id: int,
    nova: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    try:
        alvo = service.redefinir_senha(db, cfg, admin, usuario_id, nova, ip_cliente(request))
    except ErroNegocio as erro:
        db.rollback()
        alvo = service.obter_usuario(db, admin, usuario_id)
        return renderizar(
            request, "auth/usuario_editar.html", {"alvo": alvo, "erro": erro.mensagem}, status=400
        )
    aviso = f"Senha provisória definida para '{alvo.login}'. Será trocada no próximo acesso."
    return redirecionar(request, "/usuarios", aviso)
