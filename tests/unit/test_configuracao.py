"""Configurações: preferências, pastas de backup, formas/categorias, celular, CPF/LGPD."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auditoria import Auditoria
from app.core.config import Config
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as caixa_repo
from app.modules.configuracao import service
from app.modules.fiado import service as fiado
from app.modules.fiado.calculo import formatar_cpf, normalizar_cpf
from app.modules.fiado.schemas import DadosCliente


def test_padroes_e_config_efetiva(db: Session, cfg: Config, admin: Usuario) -> None:
    prefs = service.carregar(db, cfg)
    assert (prefs.nome_loja, prefs.sessao_minutos, prefs.backup_hora) == ("Papelaria", 30, "21:00")
    assert not prefs.cliente_campos_extras and prefs.backup_pastas == ()

    service.salvar_gerais(db, admin, "  Papelaria   Estrela ", 45, True, None)
    efetiva = service.config_efetiva(db, cfg)
    assert (efetiva.nome_loja, efetiva.sessao_minutos) == ("Papelaria Estrela", 45)
    assert service.carregar(db, cfg).cliente_campos_extras
    assert "configuracao_alterada" in list(db.scalars(select(Auditoria.acao)))


@pytest.mark.parametrize(
    ("nome", "minutos", "erro"),
    [
        ("P", 30, "nome da loja"),
        ("Papelaria", 2, "entre 5 e 480"),
        ("Papelaria", 999, "entre 5 e 480"),
    ],
)
def test_validacao_gerais(db: Session, admin: Usuario, nome: str, minutos: int, erro: str) -> None:
    with pytest.raises(ErroNegocio, match=erro):
        service.salvar_gerais(db, admin, nome, minutos, False, None)


def test_operador_nao_configura(db: Session, operador: Usuario) -> None:
    with pytest.raises(SemPermissao):
        service.salvar_gerais(db, operador, "Loja", 30, False, None)
    with pytest.raises(SemPermissao):
        service.criar_forma(db, operador, "Vale", None)


def test_pastas_de_backup(db: Session, cfg: Config, admin: Usuario, tmp_path: Path) -> None:
    pasta = tmp_path / "Backup"
    pasta.mkdir()
    service.salvar_backup(db, admin, f'"{pasta}"\n\n{pasta}', "22:30", None)
    prefs = service.carregar(db, cfg)
    assert prefs.backup_pastas == (str(pasta),)  # aspas removidas, repetida ignorada
    assert prefs.hora_backup.hour == 22
    for texto, hora, erro in (
        ("pasta-relativa", "21:00", "caminho completo"),
        (str(tmp_path / "nao-existe"), "21:00", "não existe"),
        (str(pasta), "25:00", "HH:MM"),
        ("\n".join([str(pasta)] * 4), "21:00", "no máximo 3"),
    ):
        with pytest.raises(ErroNegocio, match=erro):
            service.salvar_backup(db, admin, texto, hora, None)


def test_formas_de_pagamento(db: Session, admin: Usuario) -> None:
    vale = service.criar_forma(db, admin, "Vale-presente", None)
    assert not vale.afeta_gaveta  # formas novas não contam na gaveta
    with pytest.raises(ErroNegocio, match="Já existe"):
        service.criar_forma(db, admin, "Vale-presente", None)
    dinheiro = next(f for f in service.listar_formas(db) if f.afeta_gaveta)
    with pytest.raises(ErroNegocio, match="Dinheiro não pode"):
        service.alternar_forma(db, admin, dinheiro.id, None)
    assert not service.alternar_forma(db, admin, vale.id, None).ativo
    assert vale.id not in [f.id for f in caixa_repo.formas_ativas(db)]
    assert service.alternar_forma(db, admin, vale.id, None).ativo


def test_categorias(db: Session, admin: Usuario) -> None:
    aluguel = service.criar_categoria(db, admin, "Aluguel", None)
    assert not service.alternar_categoria(db, admin, aluguel.id, None).ativo
    nomes = [c.nome for c in service.listar_categorias(db)]
    assert "Aluguel" in nomes and not any("fiado" in n for n in nomes)
    reservada = caixa_repo.categoria_devolucao_fiado(db)
    assert reservada is not None
    with pytest.raises(ErroNegocio, match="não encontrada"):
        service.alternar_categoria(db, admin, reservada.id, None)


def test_endereco_e_qr_code_para_celular() -> None:
    for url in service.enderecos_locais(8765):
        assert url.startswith("http://") and url.endswith(":8765")
    assert service.qr_code("http://192.168.0.10:8765").startswith("data:image/svg+xml")
    assert service._ip_privado("192.168.1.5") and service._ip_privado("172.20.0.1")
    assert not service._ip_privado("8.8.8.8") and not service._ip_privado("172.32.0.1")
    assert service._ip_tailscale("100.64.0.1") and service._ip_tailscale("100.127.255.254")
    assert not service._ip_tailscale("100.128.0.1") and not service._ip_tailscale("192.168.0.1")
    for url in service.enderecos_remotos(8765):
        assert url.startswith("http://100.")


# ---------------------------------------------------------------- CPF / endereço (opcionais)


def test_cpf() -> None:
    assert normalizar_cpf("529.982.247-25") == "52998224725"
    assert normalizar_cpf("  ") is None
    assert formatar_cpf("52998224725") == "529.982.247-25"
    for invalido in ("111.111.111-11", "529.982.247-24", "123"):
        with pytest.raises(ValueError, match="CPF inválido"):
            normalizar_cpf(invalido)


def test_campos_extras_so_gravam_quando_ligados(db: Session, admin: Usuario) -> None:
    dados = DadosCliente(
        nome="Ana Souza", consentimento=True, cpf="529.982.247-25", endereco="Rua A, 10"
    )
    desligado = fiado.criar_cliente(db, admin, dados, None, campos_extras=False)
    assert desligado.cpf is None and desligado.endereco is None
    ligado = fiado.criar_cliente(db, admin, dados, None, campos_extras=True)
    assert (ligado.cpf, ligado.endereco) == ("52998224725", "Rua A, 10")

    fiado.anonimizar(db, admin, ligado.id, None)
    assert ligado.cpf is None and ligado.endereco is None


def test_auditoria_de_cliente_sem_dados_pessoais(db: Session, admin: Usuario) -> None:
    dados = DadosCliente(
        nome="Sebastião Lima",
        telefone="11988887777",
        consentimento=True,
        cpf="529.982.247-25",
        endereco="Rua Secreta, 99",
    )
    cliente = fiado.criar_cliente(db, admin, dados, None, campos_extras=True)
    fiado.editar_cliente(db, admin, cliente.id, dados, True, None, campos_extras=True)
    textos = " ".join(
        (a.antes_json or "") + (a.depois_json or "") for a in db.scalars(select(Auditoria))
    )
    for pessoal in ("Sebastião", "11988887777", "52998224725", "Rua Secreta"):
        assert pessoal not in textos
    exportado = fiado.exportar_dados(db, admin, cliente.id, None)["cliente"]
    assert exportado["cpf"] == "52998224725" and exportado["endereco"] == "Rua Secreta, 99"
