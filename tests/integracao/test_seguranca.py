"""Revisão de segurança automatizada: vale para TODAS as rotas, inclusive as futuras."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.config import Config
from app.core.seguranca import host_permitido
from app.main import criar_app
from app.modules.auth.models import Usuario
from tests.conftest import SENHA_OPERADOR, entrar, postar

# Únicas páginas abertas sem login.
PUBLICAS = {"/login", "/primeiro-acesso", "/sair"}
# Páginas que o operador pode abrir (o resto é só do administrador).
DO_OPERADOR = re.compile(
    r"^/($|trocar-senha|caixa/|fiado($|/(busca|clientes/\{cliente_id\}($|/(compra|receber|editar|"
    r"operacao/\{tipo\}))|clientes/novo|lancamentos/))|caixa/sessoes/)"
)


def _rotas(app_cfg: Config) -> list[tuple[str, str]]:
    """Todas as rotas HTTP do app (o FastAPI recente agrupa as de cada router incluído)."""
    app = criar_app(app_cfg)
    encontradas: set[tuple[str, str]] = set()
    for rota in app.routes:
        if isinstance(rota, APIRoute):
            encontradas |= {(m, rota.path) for m in rota.methods}
        elif hasattr(rota, "effective_route_contexts"):
            for contexto in rota.effective_route_contexts():
                encontradas |= {(m, contexto.path) for m in contexto.methods}
    return sorted(encontradas)


def _url(caminho: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", caminho)


def test_nenhuma_rota_funciona_sem_login(cliente: TestClient, admin: Usuario, cfg: Config) -> None:
    rotas = _rotas(cfg)
    assert len(rotas) > 40
    cliente.get("/login")  # pega o cookie CSRF: o bloqueio tem que vir do login, não do CSRF
    for metodo, caminho in rotas:
        if caminho in PUBLICAS:
            continue
        if metodo == "GET":
            resposta = cliente.get(_url(caminho), follow_redirects=False)
        else:
            resposta = postar(cliente, _url(caminho))
        assert resposta.status_code == 303, f"{metodo} {caminho} -> {resposta.status_code}"
        assert resposta.headers["location"] == "/login", f"{metodo} {caminho}"


def test_operador_nao_alcanca_rotas_de_administrador(
    cliente: TestClient, operador: Usuario, cfg: Config
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    for metodo, caminho in _rotas(cfg):
        if caminho in PUBLICAS or DO_OPERADOR.match(caminho):
            continue
        if metodo == "GET":
            resposta = cliente.get(_url(caminho), follow_redirects=False)
        else:
            resposta = postar(cliente, _url(caminho))
        assert resposta.status_code == 403, f"{metodo} {caminho} -> {resposta.status_code}"


def test_todo_post_exige_csrf(cliente: TestClient, admin: Usuario, cfg: Config) -> None:
    for metodo, caminho in _rotas(cfg):
        if metodo != "GET":
            resposta = cliente.post(_url(caminho), data={}, follow_redirects=False)
            assert resposta.status_code == 403, f"{caminho} aceitou POST sem CSRF"


@pytest.mark.parametrize(
    ("host", "aceito"),
    [
        ("localhost:8765", True),
        ("127.0.0.1:8765", True),
        ("[::1]:8765", True),
        ("192.168.1.202:8765", True),
        ("10.0.0.5", True),
        ("172.16.3.4:8765", True),
        ("site-malicioso.com", False),  # DNS rebinding
        ("site-malicioso.com:8765", False),
        ("8.8.8.8:8765", False),
        ("", False),
    ],
)
def test_host_permitido(host: str, aceito: bool, cfg: Config) -> None:
    assert host_permitido(host, Config(dir_dados=cfg.dir_dados)) is aceito


def test_host_estranho_e_recusado(cliente: TestClient) -> None:
    resposta = cliente.get("/login", headers={"Host": "site-malicioso.com"})
    assert resposta.status_code == 400


def test_login_nao_expoe_detalhes_tecnicos(cliente: TestClient, admin: Usuario) -> None:
    resposta = cliente.get("/login")
    assert "server" not in {
        k.lower() for k in resposta.headers
    } or "uvicorn" not in resposta.headers.get("server", "")
    for caminho in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(caminho).status_code == 404
