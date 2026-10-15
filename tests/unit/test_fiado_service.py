"""Regras do fiado contra banco real (com triggers)."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auditoria import Auditoria
from app.core.datas import hoje_local
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa import service as caixa
from app.modules.caixa.models import MovimentoCaixa
from app.modules.caixa.schemas import Parte
from app.modules.fiado import service
from app.modules.fiado.models import Cliente
from app.modules.fiado.schemas import DadosCliente, Excedente, NovaCompra, NovoPagamento

# ---------------------------------------------------------------- auxiliares


def forma(db: Session, nome: str) -> int:
    return next(f.id for f in caixa_repo.formas_ativas(db) if f.nome == nome)


def abrir_caixa(db: Session, usuario: Usuario, fundo: int = 10000) -> None:
    caixa.abrir_caixa(db, usuario, fundo, uuid4(), None)


def novo_cliente(
    db: Session, usuario: Usuario, nome: str = "Sebastião Lima", limite: int | None = None, **kw
) -> Cliente:  # type: ignore[no-untyped-def]
    dados = DadosCliente(nome=nome, consentimento=True, limite_centavos=limite, **kw)
    return service.criar_cliente(db, usuario, dados, None)


def compra(
    db: Session,
    usuario: Usuario,
    cliente: Cliente,
    total: int,
    pago: list[tuple[str, int]] | None = None,
    autorizador: Usuario | None = None,
    confirmar_limite: bool = False,
    dias_atras: int = 0,
):  # type: ignore[no-untyped-def]
    dados = NovaCompra(
        cliente_id=cliente.id,
        valor_total_centavos=total,
        partes_pagas=[Parte(forma_id=forma(db, n), valor_centavos=v) for n, v in (pago or [])],
        descricao="caderno + caneta",
        data_referencia=hoje_local() - timedelta(days=dias_atras),
        chave=uuid4(),
    )
    lanc, _ = service.anotar_compra(db, usuario, dados, autorizador, confirmar_limite, None)
    return lanc


def pagar(
    db: Session,
    usuario: Usuario,
    cliente: Cliente,
    partes: list[tuple[str, int]],
    excedente: Excedente | None = None,
    chave=None,  # type: ignore[no-untyped-def]
):  # type: ignore[no-untyped-def]
    dados = NovoPagamento(
        cliente_id=cliente.id,
        partes=[Parte(forma_id=forma(db, n), valor_centavos=v) for n, v in partes],
        excedente=excedente,
        chave=chave or uuid4(),
    )
    return service.receber_pagamento(db, usuario, dados, None)


def gaveta(db: Session) -> int:
    sessao = caixa.sessao_aberta(db)
    assert sessao is not None
    return caixa.resumo_da_sessao(db, sessao).esperado_gaveta_centavos


# ---------------------------------------------------------------- cadastro


def test_cadastro_exige_consentimento(db: Session, operador: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="autorizou"):
        service.criar_cliente(db, operador, DadosCliente(nome="Ana Souza"), None)


def test_operador_nao_define_limite(db: Session, operador: Usuario, admin: Usuario) -> None:
    cliente = novo_cliente(db, operador, limite=5000)
    assert cliente.limite_centavos is None
    dados = DadosCliente(nome="Sebastião Lima", consentimento=True, limite_centavos=9999)
    service.editar_cliente(db, operador, cliente.id, dados, True, None)
    assert cliente.limite_centavos is None
    service.editar_cliente(db, admin, cliente.id, dados, True, None)
    assert cliente.limite_centavos == 9999


def test_busca_por_nome_apelido_telefone_sem_acento(db: Session, admin: Usuario) -> None:
    novo_cliente(db, admin, "Sebastião Lima", apelido="Tião", telefone="(11) 98888-7777")
    novo_cliente(db, admin, "Maria Souza")
    assert [c.nome for c in service.buscar_clientes(db, "tiao")] == ["Sebastião Lima"]
    assert [c.nome for c in service.buscar_clientes(db, "SEBASTIAO")] == ["Sebastião Lima"]
    assert [c.nome for c in service.buscar_clientes(db, "98888")] == ["Sebastião Lima"]
    assert len(service.buscar_clientes(db, "")) == 2
    assert service.buscar_clientes(db, "%") == []  # curinga do SQL é tratado como texto


def test_nao_desativa_cliente_com_saldo(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin)
    cliente = novo_cliente(db, admin)
    compra(db, admin, cliente, 1000)
    dados = DadosCliente(nome=cliente.nome, consentimento=True)
    with pytest.raises(ErroNegocio, match="não pode ser desativado"):
        service.editar_cliente(db, admin, cliente.id, dados, False, None)


# ---------------------------------------------------------------- compra


def test_compra_fiado_nao_entra_no_caixa(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador)
    cliente = novo_cliente(db, operador)
    lanc = compra(db, operador, cliente, 2550)
    assert (lanc.tipo, lanc.valor_centavos, lanc.movimento_caixa_id) == ("compra", 2550, None)
    assert cliente.saldo_centavos == 2550
    assert gaveta(db) == 10000
    assert db.scalar(select(MovimentoCaixa)) is None


def test_compra_exige_caixa_aberto(db: Session, operador: Usuario) -> None:
    cliente = novo_cliente(db, operador)
    with pytest.raises(ErroNegocio, match="caixa está fechado"):
        compra(db, operador, cliente, 1000)


def test_venda_mista_parte_paga_entra_no_caixa(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    lanc = compra(db, operador, cliente, 5000, pago=[("Dinheiro", 2000)])
    assert lanc.valor_centavos == 3000 and cliente.saldo_centavos == 3000
    assert gaveta(db) == 2000
    mov = caixa.obter_movimento(db, lanc.movimento_caixa_id)
    assert (mov.tipo, mov.origem, mov.valor_centavos) == ("entrada", "fiado", 2000)


def test_venda_mista_invalida(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador)
    cliente = novo_cliente(db, operador)
    with pytest.raises(ErroNegocio, match="tudo pago"):
        compra(db, operador, cliente, 1000, pago=[("PIX", 1000)])
    with pytest.raises(ErroNegocio, match="maior que o total"):
        compra(db, operador, cliente, 1000, pago=[("PIX", 1500)])


def test_compra_com_data_futura_e_recusada(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador)
    cliente = novo_cliente(db, operador)
    with pytest.raises(ErroNegocio, match="futuro"):
        compra(db, operador, cliente, 1000, dias_atras=-1)


def test_limite_de_credito(db: Session, operador: Usuario, admin: Usuario) -> None:
    abrir_caixa(db, operador)
    cliente = novo_cliente(db, admin, limite=5000)
    compra(db, operador, cliente, 4000)
    assert cliente.pode_comprar_centavos() == 1000

    with pytest.raises(SemPermissao, match="senha do administrador"):
        compra(db, operador, cliente, 1001)
    with pytest.raises(SemPermissao):
        compra(db, operador, cliente, 1001, autorizador=operador)
    lanc = compra(db, operador, cliente, 1001, autorizador=admin)
    assert lanc.autorizado_por == admin.id

    with pytest.raises(ErroNegocio, match="Marque a autorização"):
        compra(db, admin, cliente, 100)
    assert compra(db, admin, cliente, 100, confirmar_limite=True).autorizado_por == admin.id


def test_compra_dentro_do_limite_nao_pede_autorizacao(
    db: Session, operador: Usuario, admin: Usuario
) -> None:
    abrir_caixa(db, operador)
    cliente = novo_cliente(db, admin, limite=5000)
    assert compra(db, operador, cliente, 5000).autorizado_por is None


def test_cliente_desativado_nao_compra(db: Session, operador: Usuario, admin: Usuario) -> None:
    abrir_caixa(db, operador)
    cliente = novo_cliente(db, admin)
    service.editar_cliente(db, admin, cliente.id, DadosCliente(nome=cliente.nome), False, None)
    with pytest.raises(ErroNegocio, match="desativado"):
        compra(db, operador, cliente, 100)


# ---------------------------------------------------------------- pagamento


def test_pagamento_parcial_entra_no_caixa_na_mesma_transacao(
    db: Session, operador: Usuario
) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 5000)
    lanc, repetido = pagar(db, operador, cliente, [("Dinheiro", 2000)])
    assert not repetido
    assert cliente.saldo_centavos == 3000
    mov = caixa.obter_movimento(db, lanc.movimento_caixa_id)
    assert (mov.tipo, mov.valor_centavos, mov.origem_id) == ("recebimento_fiado", 2000, cliente.id)
    assert gaveta(db) == 2000


def test_pagamento_dividido(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 5000)
    pagar(db, operador, cliente, [("PIX", 3000), ("Dinheiro", 2000)])
    assert cliente.saldo_centavos == 0
    assert gaveta(db) == 2000


def test_pagamento_maior_que_a_divida_pergunta(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 3000)
    with pytest.raises(ErroNegocio, match="troco ou deixar crédito"):
        pagar(db, operador, cliente, [("Dinheiro", 5000)])


def test_excedente_como_troco(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 3000)
    lanc, _ = pagar(db, operador, cliente, [("Dinheiro", 5000)], Excedente.TROCO)
    assert lanc.valor_centavos == 3000 and cliente.saldo_centavos == 0
    assert gaveta(db) == 3000  # entrou 50, saiu 20 de troco


def test_excedente_como_credito(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 3000)
    lanc, _ = pagar(db, operador, cliente, [("PIX", 5000)], Excedente.CREDITO)
    assert lanc.valor_centavos == 5000
    assert cliente.saldo_centavos == -2000  # crédito a favor do cliente


def test_troco_so_com_dinheiro(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 3000)
    with pytest.raises(ErroNegocio, match="Só dá para devolver troco"):
        pagar(db, operador, cliente, [("PIX", 5000)], Excedente.TROCO)


def test_credito_abate_compra_seguinte(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    pagar(db, operador, cliente, [("PIX", 1000)], Excedente.CREDITO)  # adiantamento
    compra(db, operador, cliente, 1500)
    assert cliente.saldo_centavos == 500


def test_pagamento_repetido_nao_duplica(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 5000)
    chave = uuid4()
    l1, r1 = pagar(db, operador, cliente, [("Dinheiro", 1000)], chave=chave)
    l2, r2 = pagar(db, operador, cliente, [("Dinheiro", 1000)], chave=chave)
    assert l1.id == l2.id and (r1, r2) == (False, True)
    assert cliente.saldo_centavos == 4000 and gaveta(db) == 1000


def test_falha_no_meio_nao_grava_nada(db: Session, operador: Usuario, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Se o lançamento do cliente falhar, a entrada no caixa também é desfeita."""
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 5000)

    def falhar(*_args, **_kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("queda de energia simulada")

    monkeypatch.setattr(service, "_gravar", falhar)
    with pytest.raises(RuntimeError):
        pagar(db, operador, cliente, [("Dinheiro", 1000)])
    db.rollback()
    assert db.scalar(select(MovimentoCaixa)) is None
    db.refresh(cliente)
    assert cliente.saldo_centavos == 5000


# -------------------------------------------------- abatimento, saldo inicial, devolução


def test_abatimento_so_admin_e_sem_caixa(db: Session, operador: Usuario, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin)
    compra(db, admin, cliente, 5000)
    with pytest.raises(SemPermissao):
        service.abater(db, operador, cliente.id, 1000, "desconto", uuid4(), None)
    with pytest.raises(ErroNegocio, match="maior que a dívida"):
        service.abater(db, admin, cliente.id, 6000, "desconto", uuid4(), None)
    with pytest.raises(ErroNegocio, match="motivo"):
        service.abater(db, admin, cliente.id, 1000, "", uuid4(), None)
    service.abater(db, admin, cliente.id, 1000, "devolveu um caderno", uuid4(), None)
    assert cliente.saldo_centavos == 4000
    assert gaveta(db) == 0


def test_saldo_inicial_do_caderno(db: Session, operador: Usuario, admin: Usuario) -> None:
    cliente = novo_cliente(db, admin)
    data = hoje_local() - timedelta(days=75)
    with pytest.raises(SemPermissao):
        service.lancar_saldo_inicial(db, operador, cliente.id, 5000, data, None, uuid4(), None)
    lanc, _ = service.lancar_saldo_inicial(db, admin, cliente.id, 5000, data, None, uuid4(), None)
    assert lanc.descricao == "Saldo trazido do caderno" and cliente.saldo_centavos == 5000
    ext = service.extrato(db, cliente.id, "Loja")
    assert ext.compra_mais_antiga == data and ext.dias_em_aberto == 75


def test_devolucao_de_credito(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 5000)
    cliente = novo_cliente(db, operador)
    with pytest.raises(ErroNegocio, match="não tem crédito"):
        service.devolver_credito(db, operador, cliente.id, 100, uuid4(), None)
    pagar(db, operador, cliente, [("PIX", 2000)], Excedente.CREDITO)
    with pytest.raises(ErroNegocio, match="entre"):
        service.devolver_credito(db, operador, cliente.id, 2001, uuid4(), None)
    lanc, _ = service.devolver_credito(db, operador, cliente.id, 2000, uuid4(), None)
    assert cliente.saldo_centavos == 0
    mov = caixa.obter_movimento(db, lanc.movimento_caixa_id)
    assert (mov.tipo, mov.valor_centavos) == ("saida", 2000)
    assert gaveta(db) == 3000


def test_devolucao_exige_dinheiro_na_gaveta(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    pagar(db, operador, cliente, [("PIX", 2000)], Excedente.CREDITO)
    with pytest.raises(ErroNegocio, match="maior do que o dinheiro"):
        service.devolver_credito(db, operador, cliente.id, 2000, uuid4(), None)


# ---------------------------------------------------------------- estorno


def test_estorno_de_compra(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin)
    lanc = compra(db, admin, cliente, 3000)
    estorno, _ = service.estornar(
        db, admin, lanc.id, "anotado no cliente errado", uuid4(), None, None
    )
    assert (estorno.sinal, estorno.valor_centavos, estorno.estorno_de_id) == (-1, 3000, lanc.id)
    assert cliente.saldo_centavos == 0
    with pytest.raises(ErroNegocio, match="já foi estornado"):
        service.estornar(db, admin, lanc.id, "de novo", uuid4(), None, None)
    with pytest.raises(ErroNegocio, match="estornar um estorno"):
        service.estornar(db, admin, estorno.id, "de novo", uuid4(), None, None)


def test_estorno_de_pagamento_estorna_o_caixa_junto(
    db: Session, operador: Usuario, admin: Usuario
) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador)
    compra(db, operador, cliente, 5000)
    pag, _ = pagar(db, operador, cliente, [("Dinheiro", 2000)])
    with pytest.raises(SemPermissao):
        service.estornar(db, operador, pag.id, "erro", uuid4(), None, None)
    est, _ = service.estornar(db, operador, pag.id, "valor errado", uuid4(), admin, None)
    assert cliente.saldo_centavos == 5000
    assert gaveta(db) == 0
    mov = caixa.obter_movimento(db, est.movimento_caixa_id)
    assert (mov.tipo, mov.estorno_de_id, mov.autorizado_por) == (
        "estorno",
        pag.movimento_caixa_id,
        admin.id,
    )


def test_estorno_de_venda_mista_desfaz_a_entrada(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin)
    lanc = compra(db, admin, cliente, 5000, pago=[("Dinheiro", 2000)])
    service.estornar(db, admin, lanc.id, "venda cancelada", uuid4(), None, None)
    assert cliente.saldo_centavos == 0 and gaveta(db) == 0


def test_caixa_nao_estorna_lancamento_do_fiado(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin)
    pag, _ = pagar(db, admin, cliente, [("PIX", 100)], Excedente.CREDITO)
    with pytest.raises(ErroNegocio, match="ficha do cliente"):
        caixa.estornar(db, admin, pag.movimento_caixa_id, "erro", uuid4(), None, None)


# ---------------------------------------------------------------- extrato e painel


def test_extrato_com_saldo_corrente(db: Session, operador: Usuario) -> None:
    abrir_caixa(db, operador, 0)
    cliente = novo_cliente(db, operador, telefone="11988887777")
    compra(db, operador, cliente, 5000, dias_atras=40)
    compra(db, operador, cliente, 3000, dias_atras=10)
    pagar(db, operador, cliente, [("Dinheiro", 5000)])
    ext = service.extrato(db, cliente.id, "Papelaria Estrela")
    assert [linha.saldo_apos_centavos for linha in ext.linhas] == [5000, 8000, 3000]
    assert ext.saldo_centavos == 3000
    assert ext.dias_em_aberto == 10
    assert ext.link_whatsapp and "R%24%2030%2C00" in ext.link_whatsapp


def test_painel_de_fiado(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    a = novo_cliente(db, admin, "Ana Souza")
    b = novo_cliente(db, admin, "Bia Lima")
    c = novo_cliente(db, admin, "Caio Reis")
    novo_cliente(db, admin, "Dora Sem Saldo")
    compra(db, admin, a, 1000, dias_atras=95)
    compra(db, admin, b, 5000, dias_atras=5)
    pagar(db, admin, c, [("PIX", 700)], Excedente.CREDITO)

    p = service.painel(db, "valor")
    assert (p.total_a_receber_centavos, p.clientes_devendo, p.total_creditos_centavos) == (
        6000,
        2,
        700,
    )
    assert [linha.cliente.nome for linha in p.linhas] == ["Bia Lima", "Ana Souza"]
    assert p.por_faixa == {"90+ dias": 1000, "até 30 dias": 5000}
    assert [linha.cliente.nome for linha in service.painel(db, "atraso").linhas] == [
        "Ana Souza",
        "Bia Lima",
    ]


# ---------------------------------------------------------------- LGPD


def test_anonimizar_so_com_saldo_zero(db: Session, admin: Usuario, operador: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin, "Sebastião Lima", telefone="11988887777")
    lanc = compra(db, admin, cliente, 1000)
    with pytest.raises(ErroNegocio, match="saldo zerado"):
        service.anonimizar(db, admin, cliente.id, None)
    with pytest.raises(SemPermissao):
        service.anonimizar(db, operador, cliente.id, None)
    pagar(db, admin, cliente, [("PIX", 1000)])
    service.anonimizar(db, admin, cliente.id, None)

    assert cliente.nome == f"Cliente anonimizado #{cliente.id}"
    assert cliente.telefone is None and not cliente.ativo
    assert service.extrato(db, cliente.id, "Loja").linhas[0].lancamento.id == lanc.id
    assert service.buscar_clientes(db, "sebastiao", incluir_inativos=True) == []
    registro = db.scalar(select(Auditoria).where(Auditoria.acao == "cliente_anonimizado"))
    assert registro is not None
    assert (registro.antes_json, registro.depois_json) == (None, None)  # sem dados pessoais
    with pytest.raises(ErroNegocio, match="já foi anonimizado"):
        service.anonimizar(db, admin, cliente.id, None)


def test_exportar_dados_do_cliente(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin, telefone="11988887777")
    compra(db, admin, cliente, 1000)
    dados = service.exportar_dados(db, admin, cliente.id, None)
    assert dados["cliente"]["telefone"] == "11988887777"
    assert dados["lancamentos"][0]["valor_centavos"] == 1000


# ---------------------------------------------------------------- integridade


def test_saldos_conferem_com_lancamentos(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin)
    compra(db, admin, cliente, 5000)
    pagar(db, admin, cliente, [("PIX", 1000)])
    assert service.verificar_saldos(db) == []
    db.execute(text("UPDATE cliente SET saldo_centavos = 1"))
    db.commit()
    assert "difere do recalculado" in service.verificar_saldos(db)[0]


def test_lancamento_de_fiado_e_imutavel(db: Session, admin: Usuario) -> None:
    abrir_caixa(db, admin, 0)
    cliente = novo_cliente(db, admin)
    compra(db, admin, cliente, 5000)
    for sql in (
        "UPDATE fiado_lancamento SET valor_centavos = 1",
        "DELETE FROM fiado_lancamento",
        "DELETE FROM cliente",
    ):
        with pytest.raises(IntegrityError):
            db.execute(text(sql))
        db.rollback()


def test_cliente_inexistente(db: Session, admin: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.obter_cliente(db, 999)
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.obter_lancamento(db, 999)
