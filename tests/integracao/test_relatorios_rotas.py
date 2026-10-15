"""Relatórios pelas telas: permissões, downloads CSV/PDF e auditoria das exportações."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auditoria import Auditoria
from app.modules.auth.models import Usuario
from tests.conftest import SENHA_ADMIN, SENHA_OPERADOR, entrar, postar


def _chave(html: str) -> str:
    achado = re.search(r'name="chave" value="([0-9a-f-]{36})"', html)
    assert achado
    return achado.group(1)


def _dia_com_caixa_fechado(cliente: TestClient) -> str:
    postar(cliente, "/caixa/abrir", {"fundo": "10,00", "chave": _chave(cliente.get("/").text)})
    form = cliente.get("/caixa/lancar/entrada").text
    dinheiro = re.findall(r'name="(parte_\d+)"', form)[0]
    postar(
        cliente,
        "/caixa/lancar/entrada",
        {dinheiro: "25,00", "chave": _chave(form), "etapa": "gravar"},
    )
    sessao_id = re.search(r'name="sessao_id" value="(\d+)"', cliente.get("/caixa/fechar").text)
    assert sessao_id
    for etapa in ("conferir", "gravar"):
        postar(
            cliente,
            "/caixa/fechar",
            {"sessao_id": sessao_id.group(1), "contado": "35,00", "etapa": etapa},
        )
    return sessao_id.group(1)


def test_admin_ve_relatorio_e_resumo_do_dia(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    _dia_com_caixa_fechado(cliente)
    inicio = cliente.get("/").text
    assert "Faturamento hoje" in inicio and "R$ 25,00" in inicio

    for preset in ("hoje", "semana", "mes"):
        pagina = cliente.get(f"/relatorios?preset={preset}")
        assert pagina.status_code == 200
        assert "R$ 25,00" in pagina.text
    personalizado = cliente.get("/relatorios?preset=personalizado&inicio=2026-01-02&fim=2026-01-01")
    assert personalizado.status_code == 200 and "anterior à inicial" in personalizado.text


def test_downloads_csv_e_pdf(cliente: TestClient, admin: Usuario, db: Session) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    sessao_id = _dia_com_caixa_fechado(cliente)

    csv = cliente.get("/relatorios/csv/lancamentos?preset=hoje")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    assert csv.content.startswith(b"\xef\xbb\xbf")  # BOM: Excel abre com acentos
    assert ";25,00;" in csv.text
    assert cliente.get("/relatorios/csv/inexistente").status_code == 400

    pdf = cliente.get("/relatorios/pdf?preset=mes")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    pdf = cliente.get(f"/caixa/sessoes/{sessao_id}/pdf")
    assert pdf.content.startswith(b"%PDF")
    assert f"/caixa/sessoes/{sessao_id}/pdf" in cliente.get(f"/caixa/sessoes/{sessao_id}").text

    exportados = list(
        db.scalars(select(Auditoria.depois_json).where(Auditoria.acao == "relatorio_exportado"))
    )
    assert len(exportados) == 3


def test_operador_nao_acessa_relatorios(
    cliente: TestClient, operador: Usuario, admin: Usuario
) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    sessao_id = _dia_com_caixa_fechado(cliente)
    postar(cliente, "/sair")

    entrar(cliente, "joao", SENHA_OPERADOR)
    assert "Faturamento hoje" not in cliente.get("/").text
    assert cliente.get("/relatorios").status_code == 403
    assert cliente.get("/relatorios/csv/lancamentos").status_code == 403
    assert cliente.get("/relatorios/pdf").status_code == 403
    # Caixa fechado por outra pessoa: nem tela nem PDF.
    assert cliente.get(f"/caixa/sessoes/{sessao_id}/pdf").status_code == 403


def test_operador_baixa_pdf_do_caixa_que_fechou(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    sessao_id = _dia_com_caixa_fechado(cliente)
    assert cliente.get(f"/caixa/sessoes/{sessao_id}/pdf").content.startswith(b"%PDF")
