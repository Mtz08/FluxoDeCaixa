"""Ponto de entrada: cria o app, liga banco, rotas e tratamento de erros."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.core.config import Config, obter_config
from app.core.database import criar_engine, criar_fabrica_sessao, verificar_integridade
from app.core.deps import verificar_csrf
from app.core.erros import (
    CsrfInvalido,
    ErroNegocio,
    NaoAutenticado,
    PrecisaTrocarSenha,
    SemPermissao,
    SemUsuarios,
)
from app.core.limitador import LimitadorTentativas
from app.core.logs import configurar_logs
from app.core.migracoes import migrar
from app.core.seguranca import COOKIE_CSRF, gerar_token, host_permitido
from app.core.templates import opcoes_cookie, redirecionar, renderizar
from app.modules.auth.routes import router as auth_router
from app.modules.backup import agendador
from app.modules.backup.routes import router as backup_router
from app.modules.caixa.routes import router as caixa_router
from app.modules.configuracao.routes import router as configuracao_router
from app.modules.fiado.routes import router as fiado_router
from app.modules.inicio.routes import router as inicio_router
from app.modules.relatorios.routes import router as relatorios_router

log = logging.getLogger(__name__)
DIR_APP = Path(__file__).resolve().parent


def criar_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or obter_config()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        configurar_logs(cfg)
        problemas = verificar_integridade(app.state.engine)
        app.state.banco_corrompido = bool(problemas)
        if problemas:
            log.error("Banco com problema de integridade: %s", problemas[:5])
        else:
            # Atualizações do sistema ajustam o banco sozinhas, sem perder dados.
            migrar(app.state.engine)
        if cfg.pasta_backup_padrao:
            Path(cfg.pasta_backup_padrao).mkdir(parents=True, exist_ok=True)
        tarefa = asyncio.create_task(agendador.rodar(app)) if cfg.agendar_backup else None
        log.info("Sistema iniciado")
        yield
        if tarefa is not None:
            tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa
        app.state.engine.dispose()

    app = FastAPI(
        title="Caixa da Papelaria",
        lifespan=ciclo_de_vida,
        dependencies=[Depends(verificar_csrf)],
        docs_url=None,  # sem documentação da API exposta na rede da loja
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = cfg
    app.state.engine = criar_engine(cfg)
    app.state.fabrica_sessao = criar_fabrica_sessao(app.state.engine)
    app.state.banco_corrompido = False
    app.state.limitador_login = LimitadorTentativas()

    app.mount("/static", StaticFiles(directory=DIR_APP / "static"), name="static")
    app.include_router(auth_router)
    app.include_router(inicio_router)
    app.include_router(caixa_router)
    app.include_router(fiado_router)
    app.include_router(relatorios_router)
    app.include_router(configuracao_router)
    app.include_router(backup_router)
    _registrar_middlewares(app)
    _registrar_tratamento_erros(app)
    return app


def _registrar_middlewares(app: FastAPI) -> None:
    @app.middleware("http")
    async def seguranca_basica(
        request: Request, chamar: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not host_permitido(request.headers.get("host", ""), app.state.config):
            # Proteção contra "DNS rebinding": só atende pelo nome/IP local da loja.
            return PlainTextResponse("Endereço não permitido.", status_code=400)
        if app.state.banco_corrompido and not request.url.path.startswith("/static"):
            return renderizar(request, "banco_corrompido.html", status=503)

        token_csrf = request.cookies.get(COOKIE_CSRF)
        novo = token_csrf is None
        request.state.csrf_token = token_csrf or gerar_token()

        resposta = await chamar(request)

        if novo:
            resposta.set_cookie(COOKIE_CSRF, request.state.csrf_token, **opcoes_cookie(request))
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "same-origin"
        resposta.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if app.state.config.cookies_seguros:
            # Site em HTTPS: o navegador nunca mais tenta abrir por HTTP.
            resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
        if "text/html" in resposta.headers.get("content-type", ""):
            resposta.headers["Cache-Control"] = "no-store"
        return resposta


def _registrar_tratamento_erros(app: FastAPI) -> None:
    @app.exception_handler(SemUsuarios)
    async def _sem_usuarios(request: Request, _erro: Exception) -> Response:
        return redirecionar(request, "/primeiro-acesso")

    @app.exception_handler(NaoAutenticado)
    async def _nao_autenticado(request: Request, _erro: Exception) -> Response:
        return redirecionar(request, "/login")

    @app.exception_handler(PrecisaTrocarSenha)
    async def _trocar_senha(request: Request, _erro: Exception) -> Response:
        return redirecionar(request, "/trocar-senha", "Crie uma senha sua antes de continuar.")

    @app.exception_handler(CsrfInvalido)
    async def _csrf(request: Request, _erro: Exception) -> Response:
        msg = "A página ficou desatualizada. Volte, recarregue (F5) e tente de novo."
        return renderizar(request, "erro.html", {"mensagem": msg}, status=403)

    @app.exception_handler(SemPermissao)
    async def _sem_permissao(request: Request, erro: Exception) -> Response:
        msg = erro.mensagem if isinstance(erro, ErroNegocio) else str(erro)
        return renderizar(request, "erro.html", {"mensagem": msg}, status=403)

    @app.exception_handler(OperationalError)
    async def _banco_ocupado(request: Request, erro: Exception) -> Response:
        # Ex.: "database is locked" quando PC e celular gravam ao mesmo tempo.
        log.warning("Erro operacional do banco: %s", type(erro).__name__)
        msg = "O sistema está ocupado. Nada foi gravado. Espere alguns segundos e tente de novo."
        return renderizar(request, "erro.html", {"mensagem": msg}, status=503)

    @app.exception_handler(ErroNegocio)
    async def _negocio(request: Request, erro: Exception) -> Response:
        msg = erro.mensagem if isinstance(erro, ErroNegocio) else str(erro)
        return renderizar(request, "erro.html", {"mensagem": msg}, status=400)


# Execução: uvicorn app.main:criar_app --factory  (ver scripts/iniciar.bat)
