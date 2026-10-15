"""Relatórios: períodos, totais, despesas por categoria, fechamentos, fiado, CSV e PDF."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.datas import hoje_local
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa import service as caixa
from app.modules.caixa.models import TipoMovimento as T
from app.modules.caixa.schemas import NovoLancamento, Parte
from app.modules.fiado import service as fiado
from app.modules.fiado.schemas import DadosCliente, NovaCompra, NovoPagamento
from app.modules.relatorios import exportar_csv, exportar_pdf, service
from app.modules.relatorios.service import montar_periodo


def forma(db: Session, nome: str) -> int:
    return next(f.id for f in caixa_repo.formas_ativas(db) if f.nome == nome)


def categoria(db: Session, nome: str) -> int:
    return next(c.id for c in caixa_repo.categorias_ativas(db) if c.nome == nome)


def lancar(db: Session, u: Usuario, tipo: T, partes: list[tuple[str, int]], **kw):  # type: ignore[no-untyped-def]
    dados = NovoLancamento(
        tipo=tipo,
        partes=[Parte(forma_id=forma(db, n), valor_centavos=v) for n, v in partes],
        chave=uuid4(),
        **kw,
    )
    return caixa.lancar(db, u, dados, None)[0]


def fechar(db: Session, u: Usuario, contado: int) -> None:
    sessao = caixa.sessao_aberta(db)
    assert sessao is not None
    caixa.conferir_fechamento(db, u, sessao.id, contado, None)
    caixa.fechar_caixa(db, u, sessao.id, contado, "diferença de teste" if contado else None, None)


@pytest.fixture
def dia_movimentado(db: Session, admin: Usuario, operador: Usuario) -> None:
    """Caixa de hoje com vendas, despesas, estorno, fiado e um fechamento com falta."""
    caixa.abrir_caixa(db, operador, 10000, uuid4(), None)
    lancar(db, operador, T.ENTRADA, [("Dinheiro", 5000), ("PIX", 3000)])
    lancar(db, operador, T.ENTRADA, [("Cartão de débito", 2000)])
    despesa = lancar(
        db, operador, T.SAIDA, [("Dinheiro", 1500)], categoria_id=categoria(db, "Limpeza")
    )
    lancar(db, operador, T.SAIDA, [("PIX", 4000)], categoria_id=categoria(db, "Fornecedor"))
    lancar(db, operador, T.SANGRIA, [("Dinheiro", 2000)], descricao="banco")
    caixa.estornar(db, admin, despesa.id, "lançada errada", uuid4(), None, None)

    cliente = fiado.criar_cliente(
        db, admin, DadosCliente(nome="Ana Souza", consentimento=True), None
    )
    compra = NovaCompra(
        cliente_id=cliente.id,
        valor_total_centavos=6000,
        data_referencia=hoje_local(),
        chave=uuid4(),
    )
    fiado.anotar_compra(db, operador, compra, None, False, None)
    pagamento = NovoPagamento(
        cliente_id=cliente.id,
        partes=[Parte(forma_id=forma(db, "Dinheiro"), valor_centavos=1000)],
        chave=uuid4(),
    )
    fiado.receber_pagamento(db, operador, pagamento, None)
    # Esperado: 100 + 50 + 10 (fiado) − 20 (sangria) = 140; contou 139 → falta de R$ 1.
    fechar(db, operador, 13900)


# ---------------------------------------------------------------- período


def test_periodos_prontos() -> None:
    hoje = hoje_local()
    assert montar_periodo("hoje").inicio == hoje
    semana = montar_periodo("semana")
    assert semana.inicio.weekday() == 0 and semana.fim == hoje
    assert montar_periodo("mes").inicio == hoje.replace(day=1)
    assert montar_periodo("qualquer-coisa").preset == "hoje"


def test_periodo_personalizado() -> None:
    p = montar_periodo("personalizado", "2026-10-01", "2026-10-03")
    assert p.dias() == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
    assert p.rotulo == "01/10/2026 a 03/10/2026"
    for ini, fim, msg in (
        ("", "", "Informe"),
        ("2026-10-05", "2026-10-01", "anterior"),
        ("2024-01-01", "2026-01-01", "até 1 ano"),
    ):
        with pytest.raises(ErroNegocio, match=msg):
            montar_periodo("personalizado", ini, fim)


# ---------------------------------------------------------------- relatório


def test_relatorio_do_dia(db: Session, admin: Usuario, dia_movimentado: None) -> None:
    rel = service.relatorio_periodo(db, admin, montar_periodo("hoje"))
    t = rel.total
    # Faturamento = 80 (entrada dividida) + 20 (débito) + 10 (fiado recebido) = 110
    assert t.faturamento_centavos == 11000
    # Despesas = 15 (estornada) − 15 + 40 (fornecedor no PIX) = 40
    assert t.despesas_centavos == 4000
    assert rel.resultado_centavos == 7000
    assert t.por_tipo[T.SANGRIA] == 2000
    assert {f.forma: f.recebido_centavos for f in t.por_forma} == {
        "Dinheiro": 6000,
        "PIX": 3000,
        "Cartão de débito": 2000,
    }
    # Despesa estornada some da categoria.
    assert [(c.categoria, c.total_centavos) for c in rel.categorias] == [("Fornecedor", 4000)]
    assert len(rel.fechamentos) == 1
    operador = rel.operadores[0]
    assert operador.nome == "João"
    assert rel.fechamentos[0].usuario_fechamento is not None
    assert (operador.fechamentos, operador.faltas_centavos, operador.saldo_centavos) == (
        1,
        100,
        -100,
    )
    assert (rel.fiado.compras_centavos, rel.fiado.recebido_centavos) == (6000, 1000)
    assert rel.a_receber.total_a_receber_centavos == 5000


def test_periodo_sem_movimento(db: Session, admin: Usuario, dia_movimentado: None) -> None:
    ontem = hoje_local() - timedelta(days=1)
    rel = service.relatorio_periodo(
        db, admin, montar_periodo("personalizado", str(ontem), str(ontem))
    )
    assert rel.total.faturamento_centavos == 0
    assert rel.fechamentos == [] and rel.categorias == []


def test_relatorio_e_so_do_admin(db: Session, operador: Usuario) -> None:
    with pytest.raises(SemPermissao):
        service.relatorio_periodo(db, operador, montar_periodo("hoje"))
    with pytest.raises(SemPermissao):
        service.resumo_do_dia(db, operador)


def test_resumo_do_dia(db: Session, admin: Usuario, dia_movimentado: None) -> None:
    dia = service.resumo_do_dia(db, admin)
    assert dia.caixa.faturamento_centavos == 11000
    assert dia.gaveta_centavos is None  # caixa já fechado
    assert dia.fiado.compras_centavos == 6000


# ---------------------------------------------------------------- CSV


def test_csv_formato_excel_brasileiro(db: Session, admin: Usuario, dia_movimentado: None) -> None:
    rel = service.relatorio_periodo(db, admin, montar_periodo("hoje"))
    conteudo = exportar_csv.movimentos(rel)
    assert conteudo.startswith(exportar_csv.BOM)
    linhas = conteudo.lstrip(exportar_csv.BOM).splitlines()
    assert linhas[0].startswith("Data/hora;Lançamento;Caixa;Tipo")
    assert any(";50,00;" in linha for linha in linhas)  # dinheiro da venda dividida
    assert any(";-15,00;" in linha for linha in linhas)  # despesa (saída negativa)
    for nome, exportador in exportar_csv.EXPORTADORES.items():
        assert exportador(rel).count("\r\n") >= 1, nome


def test_csv_bloqueia_injecao_de_formula() -> None:
    assert exportar_csv.texto('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert exportar_csv.texto("+55 11") == "'+55 11"
    assert exportar_csv.texto("@soma") == "'@soma"
    assert exportar_csv.texto("caderno") == "caderno"
    assert exportar_csv.numero(-123456) == "-1234,56"
    assert exportar_csv.numero(None) == ""


# ---------------------------------------------------------------- PDF


def test_pdf_do_periodo_e_do_fechamento(db: Session, admin: Usuario, dia_movimentado: None) -> None:
    rel = service.relatorio_periodo(db, admin, montar_periodo("hoje"))
    pdf = exportar_pdf.pdf_periodo(rel, "Papelaria Estrela")
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1500

    sessao_id = rel.fechamentos[0].id
    dados = caixa.obter_resumo(db, admin, sessao_id)
    pdf = exportar_pdf.pdf_fechamento(dados, "Papelaria Estrela")
    assert pdf.startswith(b"%PDF-")


def test_pdf_aceita_texto_fora_do_latin1() -> None:
    assert exportar_pdf.limpar("Saída ▲ — ok ✔ 😀") == "Saída + - ok  ?"
