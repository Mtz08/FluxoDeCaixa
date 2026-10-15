"""Configurações, backup manual e restauração pelas telas."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Config
from app.modules.auth.models import Usuario
from app.modules.backup import cripto
from tests.conftest import SENHA_ADMIN, SENHA_OPERADOR, entrar, postar

SENHA_BACKUP = "senha-backup-123"


def _configurar_backup(cliente: TestClient, pasta: Path) -> None:
    resposta = postar(
        cliente,
        "/configuracoes/backup/senha",
        {"nova": SENHA_BACKUP, "confirmacao": SENHA_BACKUP, "senha_admin": SENHA_ADMIN},
    )
    assert resposta.status_code == 303
    resposta = postar(cliente, "/configuracoes/backup", {"pastas": str(pasta), "hora": "21:00"})
    assert resposta.status_code == 303


def test_pagina_de_configuracoes_e_permissao(
    cliente: TestClient, admin: Usuario, operador: Usuario
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    assert cliente.get("/configuracoes").status_code == 403
    assert "backup ainda não foi configurado" in cliente.get("/").text  # operador também é avisado
    postar(cliente, "/sair")

    entrar(cliente, "maria", SENHA_ADMIN)
    pagina = cliente.get("/configuracoes").text
    for trecho in (
        "Backup",
        "Acessar pelo celular",
        "De fora da loja",
        "Formas de pagamento",
        "Verificar os dados",
    ):
        assert trecho in pagina


def test_senha_do_backup_exige_senha_do_admin(
    cliente: TestClient, admin: Usuario, cfg: Config
) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    postar(
        cliente,
        "/configuracoes/backup/senha",
        {"nova": SENHA_BACKUP, "confirmacao": SENHA_BACKUP, "senha_admin": "errada"},
    )
    assert "Autorização negada" in cliente.get("/configuracoes").text
    assert cripto.ler_senha(cfg.dir_dados) is None
    postar(
        cliente,
        "/configuracoes/backup/senha",
        {"nova": "curta", "confirmacao": "curta", "senha_admin": SENHA_ADMIN},
    )
    assert "pelo menos 8" in cliente.get("/configuracoes").text


def test_backup_manual_e_restauracao_pela_tela(
    cliente: TestClient, admin: Usuario, tmp_path: Path, cfg: Config
) -> None:
    pasta = tmp_path / "Backups"
    pasta.mkdir()
    entrar(cliente, "maria", SENHA_ADMIN)
    _configurar_backup(cliente, pasta)

    resposta = postar(cliente, "/backup/agora")
    assert resposta.status_code == 303
    assert "Backup feito em 1 pasta" in cliente.get("/configuracoes").text
    inicio = cliente.get("/").text
    assert "Nenhum backup" not in inicio and "não foi configurado" not in inicio
    arquivo = next(pasta.glob("caixa_*.cxbk"))

    # Cadastro feito DEPOIS do backup: deve sumir ao restaurar.
    postar(cliente, "/fiado/clientes/novo", {"nome": "Cliente Novo", "consentimento": "1"})

    tela = cliente.get("/backup/restaurar").text
    assert arquivo.name.split("_")[1][:4] in tela  # backup listado
    sem_confirmar = postar(
        cliente,
        "/backup/restaurar",
        {"origem": str(arquivo), "confirmacao": "sim", "senha_admin": SENHA_ADMIN},
    )
    assert sem_confirmar.status_code == 400 and "RESTAURAR" in sem_confirmar.text

    caminho_falso = postar(
        cliente,
        "/backup/restaurar",
        {"origem": str(cfg.caminho_banco), "confirmacao": "RESTAURAR", "senha_admin": SENHA_ADMIN},
    )
    assert caminho_falso.status_code == 400 and "não encontrado" in caminho_falso.text

    ok = postar(
        cliente,
        "/backup/restaurar",
        {"origem": str(arquivo), "confirmacao": "RESTAURAR", "senha_admin": SENHA_ADMIN},
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/login"
    entrar(cliente, "maria", SENHA_ADMIN)
    assert "Cliente Novo" not in cliente.get("/fiado/busca?q=novo").text


def test_restaurar_enviando_arquivo(
    cliente: TestClient, admin: Usuario, tmp_path: Path, db: Session
) -> None:
    pasta = tmp_path / "Backups"
    pasta.mkdir()
    entrar(cliente, "maria", SENHA_ADMIN)
    _configurar_backup(cliente, pasta)
    postar(cliente, "/backup/agora")
    arquivo = next(pasta.glob("caixa_*.cxbk"))

    csrf = cliente.cookies["caixa_csrf"]
    resposta = cliente.post(
        "/backup/restaurar",
        data={
            "csrf_token": csrf,
            "confirmacao": "RESTAURAR",
            "senha_admin": SENHA_ADMIN,
            "senha_backup": "senha-errada-1",
        },
        files={"arquivo": ("backup.cxbk", arquivo.read_bytes(), "application/octet-stream")},
        follow_redirects=False,
    )
    assert resposta.status_code == 400 and "incorreta" in resposta.text
    resposta = cliente.post(
        "/backup/restaurar",
        data={
            "csrf_token": csrf,
            "confirmacao": "RESTAURAR",
            "senha_admin": SENHA_ADMIN,
            "senha_backup": SENHA_BACKUP,
        },
        files={"arquivo": ("backup.cxbk", arquivo.read_bytes(), "application/octet-stream")},
        follow_redirects=False,
    )
    assert resposta.status_code == 303


def test_formas_categorias_e_verificacao(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    postar(cliente, "/configuracoes/formas", {"nome": "Vale-presente"})
    postar(cliente, "/configuracoes/categorias", {"nome": "Aluguel"})
    pagina = cliente.get("/configuracoes").text
    assert "Vale-presente" in pagina and "Aluguel" in pagina
    forma_id = re.findall(r"/configuracoes/formas/(\d+)/alternar", pagina)[-1]
    postar(cliente, f"/configuracoes/formas/{forma_id}/alternar")
    assert "desativada" in cliente.get("/configuracoes").text

    verificacao = postar(cliente, "/configuracoes/verificar")
    assert verificacao.status_code == 200 and "Tudo certo" in verificacao.text


def test_nome_da_loja_e_campos_extras(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    assert 'name="cpf"' not in cliente.get("/fiado/clientes/novo").text
    postar(
        cliente,
        "/configuracoes/gerais",
        {"nome_loja": "Papelaria Estrela", "sessao_minutos": "60", "campos_extras": "1"},
    )
    assert 'name="cpf"' in cliente.get("/fiado/clientes/novo").text
    resposta = postar(
        cliente,
        "/fiado/clientes/novo",
        {"nome": "Ana Souza", "cpf": "111.111.111-11", "consentimento": "1"},
    )
    assert resposta.status_code == 400 and "CPF inválido" in resposta.text


def test_fechar_caixa_dispara_backup(cliente: TestClient, admin: Usuario, tmp_path: Path) -> None:
    pasta = tmp_path / "Backups"
    pasta.mkdir()
    entrar(cliente, "maria", SENHA_ADMIN)
    _configurar_backup(cliente, pasta)
    chave = re.search(r'name="chave" value="([0-9a-f-]{36})"', cliente.get("/").text)
    assert chave
    postar(cliente, "/caixa/abrir", {"fundo": "0", "chave": chave.group(1)})
    sessao = re.search(r'name="sessao_id" value="(\d+)"', cliente.get("/caixa/fechar").text)
    assert sessao
    for etapa in ("conferir", "gravar"):
        postar(
            cliente, "/caixa/fechar", {"sessao_id": sessao.group(1), "contado": "0", "etapa": etapa}
        )
    assert len(list(pasta.glob("caixa_*_fechamento.cxbk"))) == 1
