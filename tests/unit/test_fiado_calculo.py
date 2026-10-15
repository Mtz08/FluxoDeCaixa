"""Cálculos do fiado sem banco: FIFO, atraso, telefone, WhatsApp."""

from datetime import date
from urllib.parse import unquote

import pytest

from app.modules.fiado.calculo import (
    ItemFiado,
    compra_mais_antiga_em_aberto,
    faixa_atraso,
    formatar_telefone,
    link_whatsapp,
    mensagem_extrato,
    normalizar_busca,
    normalizar_telefone,
    saldo,
)

_seq = iter(range(1, 1000))


def compra(valor: int, dia: int) -> ItemFiado:
    return ItemFiado(next(_seq), 1, valor, date(2026, 10, dia), False, None)


def pagamento(valor: int, dia: int) -> ItemFiado:
    return ItemFiado(next(_seq), -1, valor, date(2026, 10, dia), False, None)


def estorno(de: ItemFiado, dia: int) -> ItemFiado:
    return ItemFiado(next(_seq), -de.sinal, de.valor_centavos, date(2026, 10, dia), True, de.id)


def test_saldo_com_compras_pagamentos_parciais_e_estorno() -> None:
    c1, c2 = compra(5000, 1), compra(3000, 5)
    p1 = pagamento(2000, 6)
    itens = [c1, c2, p1, estorno(c2, 7)]
    assert saldo(itens) == 3000  # 50 + 30 − 20 − 30


def test_pagamento_quita_primeiro_a_compra_mais_antiga() -> None:
    itens = [compra(5000, 1), compra(3000, 5), pagamento(5000, 6)]
    assert compra_mais_antiga_em_aberto(itens) == date(2026, 10, 5)


def test_pagamento_parcial_mantem_a_mais_antiga_em_aberto() -> None:
    itens = [compra(5000, 1), compra(3000, 5), pagamento(4999, 6)]
    assert compra_mais_antiga_em_aberto(itens) == date(2026, 10, 1)


def test_tudo_pago_nao_tem_compra_em_aberto() -> None:
    assert compra_mais_antiga_em_aberto([compra(5000, 1), pagamento(5000, 2)]) is None
    assert compra_mais_antiga_em_aberto([]) is None


def test_compra_estornada_nao_conta_para_atraso() -> None:
    antiga = compra(5000, 1)
    itens = [antiga, compra(3000, 5), estorno(antiga, 6)]
    assert compra_mais_antiga_em_aberto(itens) == date(2026, 10, 5)


def test_pagamento_estornado_volta_a_deixar_compra_em_aberto() -> None:
    p = pagamento(5000, 2)
    itens = [compra(5000, 1), p, estorno(p, 3)]
    assert compra_mais_antiga_em_aberto(itens) == date(2026, 10, 1)


def test_ordem_por_data_e_nao_por_digitacao() -> None:
    # Compra antiga digitada depois (ex.: saldo do caderno) é a mais antiga.
    itens = [compra(1000, 10), compra(1000, 2), pagamento(1000, 11)]
    assert compra_mais_antiga_em_aberto(itens) == date(2026, 10, 10)


@pytest.mark.parametrize(
    ("dias", "faixa"),
    [
        (None, ""),
        (0, "até 30 dias"),
        (29, "até 30 dias"),
        (30, "30+ dias"),
        (60, "60+ dias"),
        (95, "90+ dias"),
    ],
)
def test_faixa_de_atraso(dias: int | None, faixa: str) -> None:
    assert faixa_atraso(dias) == faixa


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [
        ("(11) 98888-7777", "11988887777"),
        ("+55 11 98888-7777", "11988887777"),
        ("011 3333-4444", "1133334444"),
        ("", None),
        (None, None),
    ],
)
def test_normalizar_telefone(entrada: str | None, saida: str | None) -> None:
    assert normalizar_telefone(entrada) == saida


def test_telefone_invalido() -> None:
    with pytest.raises(ValueError, match="Telefone inválido"):
        normalizar_telefone("12345")


def test_formatar_telefone() -> None:
    assert formatar_telefone("11988887777") == "(11) 98888-7777"
    assert formatar_telefone("1133334444") == "(11) 3333-4444"
    assert formatar_telefone(None) == ""


def test_busca_sem_acento() -> None:
    assert normalizar_busca("João", "Tião", None) == "joao tiao"


def test_mensagem_e_link_do_whatsapp() -> None:
    msg = mensagem_extrato("Tião", "Papelaria Estrela", 12345, date(2026, 9, 1))
    assert "Olá, Tião!" in msg and "R$ 123,45" in msg and "01/09/2026" in msg
    link = link_whatsapp("11988887777", msg)
    assert link is not None and link.startswith("https://wa.me/5511988887777?text=")
    assert unquote(link.split("text=")[1]) == msg
    assert link_whatsapp(None, msg) is None


def test_mensagem_de_credito_e_conta_em_dia() -> None:
    assert "crédito de R$ 10,00" in mensagem_extrato("Ana", "Loja", -1000, None)
    assert "em dia" in mensagem_extrato("Ana", "Loja", 0, None)
