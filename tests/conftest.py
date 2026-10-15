"""Fixtures: cada teste recebe um banco novo, já migrado (com os triggers reais)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Config
from app.core.database import criar_engine, criar_fabrica_sessao
from app.core.migracoes import migrar
from app.core.seguranca import CAMPO_CSRF, COOKIE_CSRF
from app.main import criar_app
from app.modules.auth import service as auth_service
from app.modules.auth.models import Perfil, Usuario
from app.modules.auth.schemas import NovoUsuario

SENHA_ADMIN = "senha-admin-1"
SENHA_OPERADOR = "senha-oper-1"


@pytest.fixture(scope="session")
def banco_modelo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Migra uma vez e copia o arquivo para cada teste (mais rápido)."""
    pasta = tmp_path_factory.mktemp("modelo")
    engine = criar_engine(Config(dir_dados=pasta))
    migrar(engine)
    engine.dispose()
    return pasta / "caixa.db"


@pytest.fixture
def cfg(tmp_path: Path, banco_modelo: Path) -> Config:
    shutil.copy(banco_modelo, tmp_path / "caixa.db")
    # "testserver" é o Host usado pelo TestClient.
    return Config(dir_dados=tmp_path, agendar_backup=False, hosts_extras=("testserver",))


@pytest.fixture
def fabrica(cfg: Config) -> Iterator[sessionmaker[Session]]:
    engine = criar_engine(cfg)
    yield criar_fabrica_sessao(engine)
    engine.dispose()


@pytest.fixture
def db(fabrica: sessionmaker[Session]) -> Iterator[Session]:
    sessao = fabrica()
    yield sessao
    sessao.rollback()
    sessao.close()


@pytest.fixture
def admin(db: Session, cfg: Config) -> Usuario:
    dados = NovoUsuario(nome="Dona Maria", login="maria", perfil=Perfil.ADMIN, senha=SENHA_ADMIN)
    return auth_service.criar_primeiro_admin(db, cfg, dados, SENHA_ADMIN, "127.0.0.1")


@pytest.fixture
def operador(db: Session, cfg: Config, admin: Usuario) -> Usuario:
    """Operador que já trocou a senha provisória."""
    dados = NovoUsuario(nome="João", login="joao", perfil=Perfil.OPERADOR, senha="provisoria1")
    usuario = auth_service.criar_usuario(db, cfg, admin, dados, None)
    auth_service.trocar_propria_senha(
        db, cfg, usuario, "provisoria1", SENHA_OPERADOR, SENHA_OPERADOR, None
    )
    return usuario


@pytest.fixture
def cliente(cfg: Config) -> Iterator[TestClient]:
    with TestClient(criar_app(cfg)) as c:
        yield c


def postar(cliente: TestClient, url: str, dados: dict[str, str] | None = None, **kw):  # type: ignore[no-untyped-def]
    """POST de formulário com o código CSRF (pega o cookie com um GET antes)."""
    if COOKIE_CSRF not in cliente.cookies:
        cliente.get("/static/css/app.css")
        cliente.get("/login")
    corpo = dict(dados or {})
    corpo[CAMPO_CSRF] = cliente.cookies[COOKIE_CSRF]
    return cliente.post(url, data=corpo, follow_redirects=False, **kw)


def entrar(cliente: TestClient, login: str, senha: str) -> None:
    resposta = postar(cliente, "/login", {"login": login, "senha": senha})
    assert resposta.status_code == 303, resposta.text
