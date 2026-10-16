"""Fiado parcelado com juros por atraso, contra banco real (SQLite e PostgreSQL)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datas import hoje_local
from app.core.erros import ErroNegocio
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa import service as caixa
from app.modules.caixa.schemas import Parte
from app.modules.configuracao import service as configuracao
from app.modules.fiado import service
from app.modules.fiado.models import Cliente, FiadoLancamento, FiadoParcela
from app.modules.fiado.parcelas import valor_juros
from app.modules.fiado.schemas import DadosCliente, Excedente, NovaCompra, NovoPagamento

TAXA = 330
COND = service.CondicoesJuros(taxa_ppm=TAXA, carencia_dias=10)
HOJE = hoje_local()


def forma(db: Session, nome: str) -> int:
    return next(f.id for f in caixa_repo.formas_ativas(db) if f.nome == nome)


@pytest.fixture
def cliente(db: Session, admin: Usuario) -> Cliente:
    caixa.abrir_caixa(db, admin, 10_000, uuid4(), None)
    return service.criar_cliente(
        db, admin, DadosCliente(nome="Ana Souza", consentimento=True), None
    )


def comprar(
    db: Session,
    usuario: Usuario,
    cliente: Cliente,
    total: int,
    parcelas: int,
    primeiro_vencimento: date | None,
    data_compra: date = HOJE,
    condicoes: service.CondicoesJuros = COND,
) -> FiadoLancamento:
    dados = NovaCompra(
        cliente_id=cliente.id,
        valor_total_centavos=total,
        descricao="mochila",
        data_referencia=data_compra,
        chave=uuid4(),
        parcelas=parcelas,
        primeiro_vencimento=primeiro_vencimento,
    )
    lanc, _ = service.anotar_compra(db, usuario, dados, None, False, None, condicoes)
    return lanc


def pagar(db: Session, usuario: Usuario, cliente: Cliente, valor: int, excedente=None):  # type: ignore[no-untyped-def]
    dados = NovoPagamento(
        cliente_id=cliente.id,
        partes=[Parte(forma_id=forma(db, "PIX"), valor_centavos=valor)],
        excedente=excedente,
        chave=uuid4(),
    )
    return service.receber_pagamento(db, usuario, dados, None)[0]


def lancamentos(db: Session, cliente: Cliente) -> list[tuple[str, int]]:
    consulta = select(FiadoLancamento).where(FiadoLancamento.cliente_id == cliente.id)
    return [(x.tipo, x.valor_com_sinal) for x in db.scalars(consulta.order_by(FiadoLancamento.id))]


# ---------------------------------------------------------------- compra parcelada


def test_mochila_em_3x_grava_as_parcelas(db: Session, admin: Usuario, cliente: Cliente) -> None:
    venc = HOJE + timedelta(days=30)
    compra = comprar(db, admin, cliente, 15_000, 3, venc)
    parcelas = list(db.scalars(select(FiadoParcela).order_by(FiadoParcela.numero)))
    assert [(p.numero, p.total, p.valor_centavos) for p in parcelas] == [
        (1, 3, 5000),
        (2, 3, 5000),
        (3, 3, 5000),
    ]
    assert parcelas[0].vencimento == venc and parcelas[0].compra_id == compra.id
    assert {(p.taxa_juros_ppm, p.carencia_dias) for p in parcelas} == {(TAXA, 10)}
    assert cliente.saldo_centavos == 15_000
    assert service.juros_a_lancar(db, cliente) == 0  # nada venceu ainda


def test_parcela_nao_pode_ser_alterada_nem_apagada(
    db: Session, admin: Usuario, cliente: Cliente
) -> None:
    comprar(db, admin, cliente, 9000, 3, HOJE + timedelta(days=5))
    for sql in ("UPDATE fiado_parcela SET valor_centavos = 1", "DELETE FROM fiado_parcela"):
        with pytest.raises(IntegrityError, match="parcela nao pode"):
            db.execute(text(sql))
        db.rollback()


@pytest.mark.parametrize(
    ("parcelas", "vencimento", "erro"),
    [
        (3, None, "vencimento da 1ª parcela"),
        (3, HOJE - timedelta(days=1), "antes da data da compra"),
    ],
)
def test_parcelamento_invalido(
    db: Session, admin: Usuario, cliente: Cliente, parcelas: int, vencimento: date | None, erro: str
) -> None:
    with pytest.raises(ErroNegocio, match=erro):
        comprar(db, admin, cliente, 9000, parcelas, vencimento)


def test_fiado_comum_continua_sem_juros(db: Session, admin: Usuario, cliente: Cliente) -> None:
    comprar(db, admin, cliente, 5000, 0, None, data_compra=HOJE - timedelta(days=400))
    assert db.scalar(select(FiadoParcela)) is None
    assert service.juros_a_lancar(db, cliente) == 0


# ---------------------------------------------------------------- juros por atraso


def compra_atrasada(
    db: Session, admin: Usuario, cliente: Cliente, dias_atraso: int, valor: int = 10_000
):  # type: ignore[no-untyped-def]
    """Compra em 1x que venceu há `dias_atraso` dias."""
    vencimento = HOJE - timedelta(days=dias_atraso)
    return comprar(
        db, admin, cliente, valor, 1, vencimento, data_compra=vencimento - timedelta(days=30)
    )


@pytest.mark.parametrize(("atraso", "dias_juros"), [(5, 0), (10, 0), (11, 1), (30, 20)])
def test_juros_so_depois_de_10_dias(
    db: Session, admin: Usuario, cliente: Cliente, atraso: int, dias_juros: int
) -> None:
    compra_atrasada(db, admin, cliente, atraso)
    assert service.juros_a_lancar(db, cliente) == valor_juros(10_000, TAXA, dias_juros)
    assert service.total_devido(db, cliente) == 10_000 + valor_juros(10_000, TAXA, dias_juros)


def test_pagamento_lanca_os_juros_e_abate_primeiro(
    db: Session, admin: Usuario, cliente: Cliente
) -> None:
    compra_atrasada(db, admin, cliente, 40)  # 30 dias de juros
    juros = valor_juros(10_000, TAXA, 30)
    previa = service.revisar_pagamento(
        db,
        NovoPagamento(
            cliente_id=cliente.id,
            partes=[Parte(forma_id=forma(db, "PIX"), valor_centavos=3000)],
            chave=uuid4(),
        ),
    )
    assert previa.juros_centavos == juros and previa.divida_centavos == 10_000 + juros
    pagar(db, admin, cliente, 3000)
    assert lancamentos(db, cliente) == [("compra", 10_000), ("juros", juros), ("pagamento", -3000)]
    estado = service.estado_do_cliente(db, cliente.id)
    assert estado.juros_pendentes_centavos == 0
    assert estado.parcelas[0].aberto_centavos == 10_000 - (3000 - juros)
    assert service.juros_a_lancar(db, cliente) == 0  # já lançados até hoje
    assert cliente.saldo_centavos == estado.saldo_lancado_centavos  # cache confere


def test_quitar_tudo_com_juros(db: Session, admin: Usuario, cliente: Cliente) -> None:
    compra_atrasada(db, admin, cliente, 25)
    total = service.total_devido(db, cliente)
    pagar(db, admin, cliente, total)
    assert cliente.saldo_centavos == 0 and service.total_devido(db, cliente) == 0
    assert service.estado_do_cliente(db, cliente.id).parcelas[0].situacao(HOJE) == "paga"


def test_pagar_a_mais_com_juros_pergunta_troco_ou_credito(
    db: Session, admin: Usuario, cliente: Cliente
) -> None:
    compra_atrasada(db, admin, cliente, 25)
    total = service.total_devido(db, cliente)
    with pytest.raises(ErroNegocio, match="troco ou deixar crédito"):
        pagar(db, admin, cliente, total + 500)
    pagar(db, admin, cliente, total + 500, Excedente.CREDITO)
    assert cliente.saldo_centavos == -500


def test_abatimento_tambem_lanca_os_juros_antes(
    db: Session, admin: Usuario, cliente: Cliente
) -> None:
    compra_atrasada(db, admin, cliente, 40)
    juros = valor_juros(10_000, TAXA, 30)
    service.abater(db, admin, cliente.id, juros, "perdão dos juros", uuid4(), None)
    assert lancamentos(db, cliente) == [
        ("compra", 10_000),
        ("juros", juros),
        ("abatimento", -juros),
    ]
    assert cliente.saldo_centavos == 10_000


def test_perdoar_juros_por_estorno_nao_cobra_de_novo(
    db: Session, admin: Usuario, cliente: Cliente
) -> None:
    compra_atrasada(db, admin, cliente, 40)
    pagar(db, admin, cliente, 1)  # força o lançamento dos juros até hoje
    juros_lanc = db.scalar(select(FiadoLancamento).where(FiadoLancamento.tipo == "juros"))
    assert juros_lanc is not None
    service.estornar(db, admin, juros_lanc.id, "perdoado pela dona", uuid4(), None, None)
    assert service.juros_a_lancar(db, cliente) == 0  # o período perdoado não volta
    assert cliente.saldo_centavos == 10_000 - 1


def test_estornar_compra_parcelada_cancela_as_parcelas(
    db: Session, admin: Usuario, cliente: Cliente
) -> None:
    compra = compra_atrasada(db, admin, cliente, 40)
    service.estornar(db, admin, compra.id, "venda cancelada", uuid4(), None, None)
    assert service.estado_do_cliente(db, cliente.id).parcelas == []
    assert service.total_devido(db, cliente) == 0


def test_nova_taxa_vale_so_para_compras_novas(
    db: Session,
    admin: Usuario,
    cliente: Cliente,
    cfg,  # type: ignore[no-untyped-def]
) -> None:
    compra_atrasada(db, admin, cliente, 30, valor=10_000)  # taxa 330 gravada
    configuracao.salvar_juros(db, admin, "0,1", "5", None)
    prefs = configuracao.carregar(db, cfg)
    assert (prefs.juros_taxa_ppm, prefs.juros_carencia_dias) == (1000, 5)
    nova = service.CondicoesJuros(prefs.juros_taxa_ppm, prefs.juros_carencia_dias)
    venc = HOJE - timedelta(days=30)
    comprar(db, admin, cliente, 10_000, 1, venc, data_compra=venc, condicoes=nova)
    esperado = valor_juros(10_000, 330, 20) + valor_juros(10_000, 1000, 25)
    assert service.juros_a_lancar(db, cliente) == esperado


def test_configuracao_de_juros_validada(db: Session, admin: Usuario) -> None:
    for taxa, carencia, erro in (
        ("abc", "10", "Taxa inválida"),
        ("2", "10", "entre 0 e 1"),
        ("0,03", "x", "carência"),
    ):
        with pytest.raises(ErroNegocio, match=erro):
            configuracao.salvar_juros(db, admin, taxa, carencia, None)


def test_extrato_e_painel_mostram_os_juros(db: Session, admin: Usuario, cliente: Cliente) -> None:
    compra_atrasada(db, admin, cliente, 40)
    juros = valor_juros(10_000, TAXA, 30)
    ext = service.extrato(db, cliente.id, "Loja")
    assert (ext.juros_hoje_centavos, ext.total_para_quitar_centavos) == (juros, 10_000 + juros)
    assert len(ext.parcelas) == 1 and ext.parcelas[0].situacao(HOJE) == "vencida (com juros)"
    painel = service.painel(db)
    assert painel.total_a_receber_centavos == 10_000 + juros


def test_backup_e_restauracao_levam_as_parcelas(
    db: Session,
    admin: Usuario,
    cliente: Cliente,
    cfg,
    fabrica,  # type: ignore[no-untyped-def]
) -> None:
    from app.modules.backup import cripto
    from app.modules.backup import service as backup

    compra_atrasada(db, admin, cliente, 40)
    juros_antes = service.juros_a_lancar(db, cliente)
    cripto.guardar_senha(cfg.dir_dados, "senha-backup-1")
    db.rollback()
    nome, conteudo = backup.gerar_para_download(cfg, db, admin, None)
    comprar(db, admin, cliente, 5000, 2, HOJE + timedelta(days=10))  # depois do backup
    db.close()
    backup.restaurar(cfg, fabrica.kw["bind"], fabrica, conteudo, None, admin, None)
    novo = fabrica()
    try:
        cli = novo.get(Cliente, cliente.id)
        assert cli is not None
        assert len(list(novo.scalars(select(FiadoParcela)))) == 1  # a compra posterior sumiu
        assert service.juros_a_lancar(novo, cli) == juros_antes
    finally:
        novo.close()
