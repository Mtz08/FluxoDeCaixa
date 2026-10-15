"""Modo nuvem: sistema publicado como site (HTTPS por trás do Caddy)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Config
from app.core.limitador import LimitadorTentativas
from app.core.seguranca import COOKIE_SESSAO, host_permitido
from app.main import criar_app
from app.modules.auth.models import Usuario
from app.modules.backup import cripto
from app.modules.backup import service as backup
from app.modules.configuracao import service as configuracao
from tests.conftest import SENHA_ADMIN, entrar, postar

DOMINIO = "minhapapelaria.duckdns.org"


@pytest.fixture
def cfg_nuvem(cfg: Config, tmp_path: Path) -> Config:
    return replace(
        cfg,
        url_publica=f"https://{DOMINIO}",
        cookies_seguros=True,
        atras_de_proxy=True,
        pasta_backup_padrao=str(tmp_path / "backups"),
    )


@pytest.fixture
def site(cfg_nuvem: Config) -> Iterator[TestClient]:
    # O site é acessado por HTTPS e pelo domínio público.
    with TestClient(criar_app(cfg_nuvem), base_url=f"https://{DOMINIO}") as c:
        yield c


def test_cookies_seguros_e_hsts(site: TestClient, admin: Usuario) -> None:
    resposta = postar(site, "/login", {"login": "maria", "senha": SENHA_ADMIN})
    assert resposta.status_code == 303
    cookie = next(h for h in resposta.headers.get_list("set-cookie") if h.startswith(COOKIE_SESSAO))
    assert "secure" in cookie.lower() and "httponly" in cookie.lower()
    assert "max-age=31536000" in site.get("/login").headers["Strict-Transport-Security"]


def test_dominio_publico_aceito_e_ip_direto_recusado(cfg_nuvem: Config) -> None:
    assert host_permitido(DOMINIO, cfg_nuvem)
    assert host_permitido(f"{DOMINIO}:443", cfg_nuvem)
    assert not host_permitido("outro-site.duckdns.org", cfg_nuvem)
    assert not host_permitido("150.230.10.20", cfg_nuvem)  # IP público do servidor, sem domínio


def test_limite_de_tentativas_por_ip(site: TestClient, admin: Usuario) -> None:
    site.app.state.limitador_login = LimitadorTentativas(maximo=3)  # type: ignore[attr-defined]
    for _ in range(3):
        assert postar(site, "/login", {"login": "robo", "senha": "x"}).status_code == 400
    bloqueado = postar(site, "/login", {"login": "maria", "senha": SENHA_ADMIN})
    assert bloqueado.status_code == 429 and "Muitas tentativas" in bloqueado.text


def test_limitador_janela_e_limpeza() -> None:
    limitador = LimitadorTentativas(maximo=2, janela_segundos=60)
    limitador.registrar_falha("1.2.3.4")
    assert not limitador.bloqueado("1.2.3.4")
    limitador.registrar_falha("1.2.3.4")
    assert limitador.bloqueado("1.2.3.4") and not limitador.bloqueado("5.6.7.8")
    limitador.limpar("1.2.3.4")
    assert not limitador.bloqueado("1.2.3.4")
    assert not limitador.bloqueado(None)


def test_pasta_de_backup_padrao_e_qr_publico(
    site: TestClient, db: Session, cfg_nuvem: Config, admin: Usuario
) -> None:
    assert configuracao.carregar(db, cfg_nuvem).backup_pastas == (cfg_nuvem.pasta_backup_padrao,)
    assert Path(cfg_nuvem.pasta_backup_padrao or "").is_dir()  # criada ao iniciar
    db.rollback()  # libera o banco para o site (cada transação reserva a escrita)
    entrar(site, "maria", SENHA_ADMIN)
    pagina = site.get("/configuracoes").text
    assert f"https://{DOMINIO}" in pagina and "Toda semana, baixe uma cópia" in pagina


def test_baixar_backup_e_alerta_semanal(
    site: TestClient, db: Session, cfg_nuvem: Config, admin: Usuario
) -> None:
    entrar(site, "maria", SENHA_ADMIN)
    assert "senha do backup" in (backup.alerta(db, cfg_nuvem) or "")
    db.rollback()
    sem_senha = site.get("/backup/baixar", follow_redirects=False)
    assert sem_senha.status_code == 303

    cripto.guardar_senha(cfg_nuvem.dir_dados, "senha-backup-123")
    assert "Baixe uma cópia" in (backup.alerta(db, cfg_nuvem) or "")
    db.rollback()  # libera o banco para o site
    arquivo = site.get("/backup/baixar")
    assert arquivo.status_code == 200
    assert "attachment" in arquivo.headers["content-disposition"]
    assert "_baixado.cxbk" in arquivo.headers["content-disposition"]
    assert cripto.decifrar(arquivo.content, "senha-backup-123").startswith(b"SQLite format 3")
    db.expire_all()
    alerta = backup.alerta(db, cfg_nuvem) or ""
    assert "Baixe uma cópia" not in alerta and "baixada" not in alerta
