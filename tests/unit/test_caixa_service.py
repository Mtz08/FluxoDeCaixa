"""Regras do caixa contra um banco real (com triggers)."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datas import agora_utc
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as repo
from app.modules.caixa import service
from app.modules.caixa.models import (
    MovimentoCaixa,
    SessaoCaixa,
    StatusSessao,
    TipoMovimento,
)
from app.modules.caixa.schemas import NovoLancamento, Parte

T = TipoMovimento


# ---------------------------------------------------------------- auxiliares


def forma(db: Session, nome: str) -> int:
    return next(f.id for f in repo.formas_ativas(db) if f.nome == nome)


def categoria(db: Session, nome: str = "Outros") -> int:
    return next(c.id for c in repo.categorias_ativas(db) if c.nome == nome)


def dados(
    db: Session,
    tipo: T,
    *partes: tuple[str, int],
    categoria_id: int | None = None,
    descricao: str | None = None,
    chave: str | None = None,
    confirmar_valor_alto: bool = False,
) -> NovoLancamento:
    return NovoLancamento(
        tipo=tipo,
        partes=[Parte(forma_id=forma(db, n), valor_centavos=v) for n, v in partes],
        categoria_id=categoria_id,
        descricao=descricao,
        chave=chave or uuid4(),  # type: ignore[arg-type]
        confirmar_valor_alto=confirmar_valor_alto,
    )


def lancar(
    db: Session, usuario: Usuario, tipo: T, *partes: tuple[str, int], **kw
) -> MovimentoCaixa:  # type: ignore[no-untyped-def]
    if tipo == T.SAIDA:
        kw.setdefault("categoria_id", categoria(db))
    if tipo == T.SANGRIA:
        kw.setdefault("descricao", "depósito")
    movimento, _ = service.lancar(db, usuario, dados(db, tipo, *partes, **kw), None)
    return movimento


def abrir(db: Session, usuario: Usuario, fundo: int = 10000) -> SessaoCaixa:
    return service.abrir_caixa(db, usuario, fundo, uuid4(), None)


def esperado(db: Session) -> int:
    sessao = service.sessao_aberta(db)
    assert sessao is not None
    return service.resumo_da_sessao(db, sessao).esperado_gaveta_centavos


def fechar(
    db: Session, usuario: Usuario, contado: int, justificativa: str | None = None
) -> SessaoCaixa:
    sessao = service.sessao_aberta(db)
    assert sessao is not None
    service.conferir_fechamento(db, usuario, sessao.id, contado, None)
    return service.fechar_caixa(db, usuario, sessao.id, contado, justificativa, None)


# ---------------------------------------------------------------- abertura


def test_abrir_caixa(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 15000)
    assert sessao.aberta and sessao.fundo_troco_centavos == 15000
    assert esperado(db) == 15000


def test_so_um_caixa_aberto(db: Session, operador: Usuario, admin: Usuario) -> None:
    abrir(db, operador)
    with pytest.raises(ErroNegocio, match="Já existe um caixa aberto"):
        abrir(db, admin)


def test_abrir_com_mesma_chave_e_idempotente(db: Session, operador: Usuario) -> None:
    chave = uuid4()
    s1 = service.abrir_caixa(db, operador, 5000, chave, None)
    s2 = service.abrir_caixa(db, operador, 5000, chave, None)
    assert s1.id == s2.id


def test_fundo_negativo_recusado(db: Session, operador: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="negativo"):
        abrir(db, operador, -1)


def test_banco_impede_dois_caixas_abertos(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO sessao_caixa (status, aberta_por, aberta_em, fundo_troco_centavos,"
                " chave_idempotencia) VALUES ('aberta', :u, '2026-01-01 00:00:00', 0, 'x')"
            ),
            {"u": operador.id},
        )
    db.rollback()


# ---------------------------------------------------------------- lançamentos


def test_sem_caixa_aberto_nao_lanca(db: Session, operador: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="caixa está fechado"):
        lancar(db, operador, T.ENTRADA, ("Dinheiro", 1000))


def test_caixa_de_ontem_bloqueia_lancamentos(db: Session, operador: Usuario) -> None:
    db.add(
        SessaoCaixa(
            status=StatusSessao.ABERTA,
            aberta_por=operador.id,
            aberta_em=agora_utc() - timedelta(days=1, hours=1),
            fundo_troco_centavos=0,
            chave_idempotencia=str(uuid4()),
        )
    )
    db.commit()
    assert service.sessao_de_dia_anterior(service.sessao_aberta(db))
    with pytest.raises(ErroNegocio, match="ainda está aberto"):
        lancar(db, operador, T.ENTRADA, ("Dinheiro", 1000))
    # Mas pode (e deve) fechar.
    fechar(db, operador, 0)
    assert service.sessao_aberta(db) is None


def test_fluxo_completo_do_dia_e_valor_esperado(db: Session, operador: Usuario) -> None:
    abrir(db, operador, 10000)  # R$ 100 de troco
    lancar(db, operador, T.ENTRADA, ("Dinheiro", 5000), ("PIX", 3000))  # venda dividida
    lancar(db, operador, T.ENTRADA, ("Cartão de débito", 2500))
    lancar(db, operador, T.SUPRIMENTO, ("Dinheiro", 2000))
    lancar(db, operador, T.SAIDA, ("Dinheiro", 1500))
    lancar(db, operador, T.SAIDA, ("PIX", 9000))  # despesa no PIX: não mexe na gaveta
    lancar(db, operador, T.SANGRIA, ("Dinheiro", 4000))
    # 100 + 50 + 20 − 15 − 40 = 115
    assert esperado(db) == 11500

    sessao = fechar(db, operador, 11500)
    assert sessao.status == StatusSessao.FECHADA
    assert (sessao.valor_esperado_centavos, sessao.diferenca_centavos) == (11500, 0)
    assert sessao.justificativa is None


def test_pagamento_dividido_grava_partes(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    mov = lancar(db, operador, T.ENTRADA, ("PIX", 3000), ("Dinheiro", 2000))
    assert mov.valor_centavos == 5000
    assert sorted(p.valor_centavos for p in mov.partes) == [2000, 3000]
    assert service.verificar_consistencia(db) == []


def test_saida_exige_categoria(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    with pytest.raises(ErroNegocio, match="categoria"):
        service.lancar(db, operador, dados(db, T.SAIDA, ("Dinheiro", 100)), None)


def test_sangria_exige_motivo_e_dinheiro(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    with pytest.raises(ErroNegocio, match="motivo"):
        service.lancar(db, operador, dados(db, T.SANGRIA, ("Dinheiro", 100)), None)
    with pytest.raises(ErroNegocio, match="sempre em dinheiro"):
        service.lancar(db, operador, dados(db, T.SUPRIMENTO, ("PIX", 100)), None)


def test_nao_tira_da_gaveta_mais_do_que_tem(db: Session, operador: Usuario) -> None:
    abrir(db, operador, 5000)
    with pytest.raises(ErroNegocio, match="maior do que o dinheiro"):
        lancar(db, operador, T.SANGRIA, ("Dinheiro", 5001))
    with pytest.raises(ErroNegocio, match="maior do que o dinheiro"):
        lancar(db, operador, T.SAIDA, ("Dinheiro", 6000))
    lancar(db, operador, T.SANGRIA, ("Dinheiro", 5000))
    assert esperado(db) == 0


def test_forma_repetida_ou_invalida(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    repetida = dados(db, T.ENTRADA, ("PIX", 100), ("PIX", 200))
    with pytest.raises(ErroNegocio, match="duas vezes"):
        service.lancar(db, operador, repetida, None)
    invalida = NovoLancamento(
        tipo=T.ENTRADA, partes=[Parte(forma_id=999, valor_centavos=1)], chave=uuid4()
    )
    with pytest.raises(ErroNegocio, match="Forma de pagamento inválida"):
        service.lancar(db, operador, invalida, None)


def test_recebimento_de_fiado_nao_e_lancado_pela_tela_do_caixa(
    db: Session, operador: Usuario
) -> None:
    abrir(db, operador)
    with pytest.raises(ErroNegocio, match="Tipo de lançamento inválido"):
        service.lancar(db, operador, dados(db, T.RECEBIMENTO_FIADO, ("PIX", 100)), None)


def test_valor_alto_exige_confirmacao(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    alto = 5_000_001
    previa = service.revisar_lancamento(db, dados(db, T.ENTRADA, ("PIX", alto)))
    assert previa.valor_alto
    with pytest.raises(ErroNegocio, match="confirmação de valor alto"):
        lancar(db, operador, T.ENTRADA, ("PIX", alto))
    assert (
        lancar(db, operador, T.ENTRADA, ("PIX", alto), confirmar_valor_alto=True).valor_centavos
        == alto
    )


def test_envio_repetido_nao_duplica(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    chave = str(uuid4())
    m1, rep1 = service.lancar(
        db, operador, dados(db, T.ENTRADA, ("Dinheiro", 1000), chave=chave), None
    )
    m2, rep2 = service.lancar(
        db, operador, dados(db, T.ENTRADA, ("Dinheiro", 1000), chave=chave), None
    )
    assert m1.id == m2.id and (rep1, rep2) == (False, True)
    total = db.scalar(
        select(MovimentoCaixa.id)
        .where(MovimentoCaixa.tipo == "entrada")
        .order_by(MovimentoCaixa.id.desc())
    )
    assert total == m1.id
    assert esperado(db) == 11000


# ---------------------------------------------------------------- estorno


def test_admin_estorna_entrada(db: Session, admin: Usuario) -> None:
    abrir(db, admin, 1000)
    mov = lancar(db, admin, T.ENTRADA, ("Dinheiro", 2000), ("PIX", 500))
    estorno, _ = service.estornar(db, admin, mov.id, "digitado errado", uuid4(), None, None)
    assert (estorno.tipo, estorno.sinal, estorno.valor_centavos) == ("estorno", -1, 2500)
    assert estorno.autorizado_por == admin.id and estorno.considera_gaveta
    assert esperado(db) == 1000
    assert sorted(p.valor_centavos for p in estorno.partes) == [500, 2000]


def test_nao_estorna_duas_vezes_nem_estorno(db: Session, admin: Usuario) -> None:
    abrir(db, admin)
    mov = lancar(db, admin, T.ENTRADA, ("PIX", 100))
    estorno, _ = service.estornar(db, admin, mov.id, "erro", uuid4(), None, None)
    with pytest.raises(ErroNegocio, match="já foi estornado"):
        service.estornar(db, admin, mov.id, "erro", uuid4(), None, None)
    with pytest.raises(ErroNegocio, match="estornar um estorno"):
        service.estornar(db, admin, estorno.id, "erro", uuid4(), None, None)


def test_estorno_exige_motivo(db: Session, admin: Usuario) -> None:
    abrir(db, admin)
    mov = lancar(db, admin, T.ENTRADA, ("PIX", 100))
    with pytest.raises(ErroNegocio, match="motivo"):
        service.estornar(db, admin, mov.id, "  ", uuid4(), None, None)


def test_operador_precisa_da_senha_do_admin(db: Session, admin: Usuario, operador: Usuario) -> None:
    abrir(db, operador)
    mov = lancar(db, operador, T.ENTRADA, ("PIX", 100))
    with pytest.raises(SemPermissao):
        service.estornar(db, operador, mov.id, "erro", uuid4(), None, None)
    with pytest.raises(SemPermissao):
        service.estornar(db, operador, mov.id, "erro", uuid4(), operador, None)
    estorno, _ = service.estornar(db, operador, mov.id, "erro", uuid4(), admin, None)
    assert (estorno.usuario_id, estorno.autorizado_por) == (operador.id, admin.id)


def test_estorno_de_entrada_em_dinheiro_exige_dinheiro_na_gaveta(
    db: Session, admin: Usuario
) -> None:
    abrir(db, admin, 0)
    mov = lancar(db, admin, T.ENTRADA, ("Dinheiro", 3000))
    lancar(db, admin, T.SANGRIA, ("Dinheiro", 3000))
    with pytest.raises(ErroNegocio, match="Não há dinheiro suficiente"):
        service.estornar(db, admin, mov.id, "erro", uuid4(), None, None)


def test_estorno_de_caixa_fechado_nao_mexe_na_gaveta_de_hoje(db: Session, admin: Usuario) -> None:
    abrir(db, admin, 0)
    ontem = lancar(db, admin, T.ENTRADA, ("Dinheiro", 3000))
    fechar(db, admin, 3000)

    abrir(db, admin, 5000)
    estorno, _ = service.estornar(
        db, admin, ontem.id, "venda lançada em dobro", uuid4(), None, None
    )
    assert not estorno.considera_gaveta
    assert esperado(db) == 5000
    sessao = service.sessao_aberta(db)
    assert sessao is not None
    resumo = service.resumo_da_sessao(db, sessao)
    assert resumo.estornos_fora_da_gaveta_centavos == -3000
    assert resumo.faturamento_centavos == -3000


def test_estorno_repetido_nao_duplica(db: Session, admin: Usuario) -> None:
    abrir(db, admin)
    mov = lancar(db, admin, T.ENTRADA, ("PIX", 100))
    chave = uuid4()
    e1, r1 = service.estornar(db, admin, mov.id, "erro", chave, None, None)
    e2, r2 = service.estornar(db, admin, mov.id, "erro", chave, None, None)
    assert e1.id == e2.id and (r1, r2) == (False, True)


# ---------------------------------------------------------------- fechamento cego


def test_diferenca_exige_justificativa(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 10000)
    conferencia = service.conferir_fechamento(db, operador, sessao.id, 9500, None)
    assert conferencia.diferenca_centavos == -500  # falta de R$ 5
    with pytest.raises(ErroNegocio, match="justificativa"):
        service.fechar_caixa(db, operador, sessao.id, 9500, "", None)
    fechado = service.fechar_caixa(db, operador, sessao.id, 9500, "troco errado", None)
    assert fechado.diferenca_centavos == -500 and fechado.justificativa == "troco errado"


def test_sobra_tambem_e_diferenca(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 10000)
    assert (
        service.conferir_fechamento(db, operador, sessao.id, 10200, None).diferenca_centavos == 200
    )


def test_fechamento_grava_a_ultima_contagem_conferida(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 10000)
    with pytest.raises(ErroNegocio, match="conferência"):
        service.fechar_caixa(db, operador, sessao.id, 10000, None, None)  # sem conferir
    service.conferir_fechamento(db, operador, sessao.id, 9000, None)
    with pytest.raises(ErroNegocio, match="conferência"):
        # Tentar fechar com outro valor (ex.: campo oculto alterado) é recusado.
        service.fechar_caixa(db, operador, sessao.id, 10000, None, None)


def test_caixa_fechado_nao_aceita_mais_nada(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 0)
    fechar(db, operador, 0)
    with pytest.raises(ErroNegocio, match="já foi fechado"):
        service.conferir_fechamento(db, operador, sessao.id, 0, None)
    with pytest.raises(ErroNegocio, match="caixa está fechado"):
        lancar(db, operador, T.ENTRADA, ("PIX", 100))


# ---------------------------------------------------------------- integridade no banco


def test_lancamento_e_imutavel_no_banco(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    lancar(db, operador, T.ENTRADA, ("PIX", 100))
    for sql in (
        "UPDATE movimento_caixa SET valor_centavos = 1",
        "DELETE FROM movimento_caixa",
        "UPDATE movimento_pagamento SET valor_centavos = 1",
        "DELETE FROM movimento_pagamento",
    ):
        with pytest.raises(IntegrityError, match="imutavel"):
            db.execute(text(sql))
        db.rollback()


def test_banco_recusa_lancamento_em_caixa_fechado(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 0)
    fechar(db, operador, 0)
    with pytest.raises(IntegrityError, match="caixa fechado"):
        db.execute(
            text(
                "INSERT INTO movimento_caixa (sessao_id, tipo, sinal, valor_centavos, usuario_id,"
                " considera_gaveta, origem, chave_idempotencia, criado_em)"
                " VALUES (:s, 'entrada', 1, 100, :u, TRUE, 'manual', 'k', '2026-01-01 00:00:00')"
            ),
            {"s": sessao.id, "u": operador.id},
        )
    db.rollback()


def test_banco_recusa_reabrir_ou_alterar_caixa_fechado(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador, 0)
    fechar(db, operador, 0)
    for sql in (
        "UPDATE sessao_caixa SET status = 'aberta'",
        "UPDATE sessao_caixa SET valor_contado_centavos = 999",
        "DELETE FROM sessao_caixa",
    ):
        with pytest.raises(IntegrityError):
            db.execute(text(sql))
        db.rollback()
    db.refresh(sessao)
    assert sessao.status == "fechada"


def test_banco_recusa_estorno_que_nao_e_inverso(db: Session, admin: Usuario) -> None:
    sessao = abrir(db, admin)
    mov = lancar(db, admin, T.ENTRADA, ("PIX", 100))
    with pytest.raises(IntegrityError, match="inverso exato"):
        db.execute(
            text(
                "INSERT INTO movimento_caixa (sessao_id, tipo, sinal, valor_centavos, usuario_id,"
                " estorno_de_id, considera_gaveta, origem, chave_idempotencia, criado_em)"
                " VALUES (:s, 'estorno', -1, 999, :u, :o, TRUE, 'manual', 'k',"
                " '2026-01-01 00:00:00')"
            ),
            {"s": sessao.id, "u": admin.id, "o": mov.id},
        )
    db.rollback()


def test_banco_recusa_valor_zero_ou_negativo(db: Session, operador: Usuario) -> None:
    sessao = abrir(db, operador)
    with pytest.raises(IntegrityError, match="(?i)check"):
        db.execute(
            text(
                "INSERT INTO movimento_caixa (sessao_id, tipo, sinal, valor_centavos, usuario_id,"
                " considera_gaveta, origem, chave_idempotencia, criado_em)"
                " VALUES (:s, 'entrada', 1, -5, :u, TRUE, 'manual', 'k', '2026-01-01 00:00:00')"
            ),
            {"s": sessao.id, "u": operador.id},
        )
    db.rollback()


# ---------------------------------------------------------------- consultas e alertas


def test_verificacao_de_consistencia_limpa(db: Session, operador: Usuario) -> None:
    abrir(db, operador)
    lancar(db, operador, T.ENTRADA, ("Dinheiro", 1000), ("PIX", 500))
    fechar(db, operador, 11000)
    assert service.verificar_consistencia(db) == []


def test_alerta_de_relogio_atrasado(db: Session, operador: Usuario) -> None:
    assert service.alerta_relogio(db) is None
    db.add(
        SessaoCaixa(
            status=StatusSessao.ABERTA,
            aberta_por=operador.id,
            aberta_em=agora_utc() + timedelta(days=2),
            fundo_troco_centavos=0,
            chave_idempotencia=str(uuid4()),
        )
    )
    db.commit()
    assert "relógio" in (service.alerta_relogio(db) or "")


def test_operador_so_ve_caixa_aberto_ou_o_que_fechou_hoje(
    db: Session, admin: Usuario, operador: Usuario
) -> None:
    sessao_admin = abrir(db, admin, 0)
    assert service.obter_resumo(db, operador, sessao_admin.id)  # aberto: pode ver
    fechar(db, admin, 0)
    with pytest.raises(SemPermissao):
        service.obter_resumo(db, operador, sessao_admin.id)
    assert service.obter_resumo(db, admin, sessao_admin.id)

    sessao_oper = abrir(db, operador, 0)
    fechar(db, operador, 0)
    assert service.obter_resumo(db, operador, sessao_oper.id)


def test_resumo_marca_estornados(db: Session, admin: Usuario) -> None:
    sessao = abrir(db, admin)
    mov = lancar(db, admin, T.ENTRADA, ("PIX", 100))
    service.estornar(db, admin, mov.id, "erro", uuid4(), None, None)
    resumo = service.obter_resumo(db, admin, sessao.id)
    assert resumo.estornados == {mov.id}
    assert resumo.resumo.estornos_qtd == 1
    assert resumo.movimentos[1].rotulo == "Estorno de entrada"


def test_objetos_inexistentes(db: Session, admin: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.obter_movimento(db, 999)
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.obter_resumo(db, admin, 999)
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.conferir_fechamento(db, admin, 999, 0, None)
