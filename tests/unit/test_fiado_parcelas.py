"""Cálculo das parcelas e dos juros por atraso (sem banco).

Regras em app/modules/fiado/parcelas.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.modules.fiado.parcelas import (
    Evento,
    Parcela,
    calcular_estado,
    dias_com_juros,
    gerar_parcelas,
    percentual_para_ppm,
    ppm_para_percentual,
    somar_meses,
    valor_juros,
)

TAXA = 330  # 0,033% ao dia
CARENCIA = 10
VENCE = date(2026, 11, 10)


def dia(n: int) -> date:
    """Dia `n` depois do vencimento (dia(0) = vencimento)."""
    return VENCE + timedelta(days=n)


# ---------------------------------------------------------------- taxa


@pytest.mark.parametrize(
    ("texto", "ppm"), [("0,033", 330), ("0.033", 330), ("1", 10_000), ("0", 0), ("0,0333", 333)]
)
def test_taxa_em_ppm(texto: str, ppm: int) -> None:
    assert percentual_para_ppm(texto) == ppm


@pytest.mark.parametrize("texto", ["abc", "-0,1", "2", "0,00001"])
def test_taxa_invalida(texto: str) -> None:
    with pytest.raises(ValueError):
        percentual_para_ppm(texto)


def test_ppm_para_texto() -> None:
    assert ppm_para_percentual(330) == "0,033"
    assert ppm_para_percentual(10_000) == "1"
    assert ppm_para_percentual(0) == "0"


# ---------------------------------------------------------------- parcelas


def test_divisao_em_parcelas_sem_perder_centavo() -> None:
    parcelas = gerar_parcelas(10_000, 3, date(2026, 11, 10))
    assert [p.valor_centavos for p in parcelas] == [3334, 3333, 3333]
    assert sum(p.valor_centavos for p in parcelas) == 10_000
    assert [p.vencimento for p in parcelas] == [
        date(2026, 11, 10),
        date(2026, 12, 10),
        date(2027, 1, 10),
    ]
    assert [p.numero for p in parcelas] == [1, 2, 3]


def test_vencimento_no_fim_do_mes() -> None:
    parcelas = gerar_parcelas(9_000, 3, date(2027, 1, 31))
    # fevereiro não tem 31: último dia; depois volta para 31
    assert [p.vencimento for p in parcelas] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
    ]
    assert somar_meses(date(2028, 1, 31), 1) == date(2028, 2, 29)  # ano bissexto


@pytest.mark.parametrize(("total", "n"), [(100, 0), (100, 25), (2, 3)])
def test_parcelamento_invalido(total: int, n: int) -> None:
    with pytest.raises(ValueError):
        gerar_parcelas(total, n, VENCE)


# ---------------------------------------------------------------- carência e juros


@pytest.mark.parametrize(
    ("depois", "dias"),
    [(-5, 0), (0, 0), (1, 0), (9, 0), (10, 0), (11, 1), (12, 2), (40, 30)],
)
def test_carencia_de_10_dias_e_juros_a_partir_do_11(depois: int, dias: int) -> None:
    """Vence dia 10: dias 11..20 sem juros; dia 21 (11º dia) = 1 dia de juros."""
    inicio = VENCE + timedelta(days=CARENCIA)
    assert dias_com_juros(VENCE, CARENCIA, inicio, dia(depois)) == dias


def test_exemplo_do_enunciado_vence_dia_10() -> None:
    venc = date(2026, 11, 10)
    assert dias_com_juros(venc, 10, date(2026, 11, 20), date(2026, 11, 20)) == 0
    assert dias_com_juros(venc, 10, date(2026, 11, 20), date(2026, 11, 21)) == 1
    assert dias_com_juros(venc, 10, date(2026, 11, 20), date(2026, 11, 22)) == 2


def test_valor_dos_juros_simples_em_centavos() -> None:
    # R$ 100,00 x 0,033% x 30 dias = R$ 0,99
    assert valor_juros(10_000, TAXA, 30) == 99
    # R$ 33,34 x 0,033% x 1 dia = 1,10022 centavo -> 1 centavo
    assert valor_juros(3334, TAXA, 1) == 1
    # meio centavo exato arredonda para cima: R$ 500,00 x 0,033% x 1 dia = 16,5 centavos -> 17
    assert valor_juros(50_000, TAXA, 1) == 17
    # abaixo de meio centavo arredonda para baixo: R$ 499,99 -> 16,49967 -> 16
    assert valor_juros(49_999, TAXA, 1) == 16
    assert valor_juros(0, TAXA, 10) == 0 and valor_juros(1000, 0, 10) == 0


def test_juros_sao_simples_nao_compostos() -> None:
    # 60 dias = exatamente 2x 30 dias (não há juros sobre juros)
    assert valor_juros(10_000, TAXA, 60) == 2 * valor_juros(10_000, TAXA, 30)


# ---------------------------------------------------------------- estado do cliente


def parcelas_da_compra(compra_id: int, valores: list[int], primeiro: date = VENCE) -> list[Parcela]:
    return [
        Parcela(
            id=compra_id * 100 + i,
            compra_id=compra_id,
            numero=i + 1,
            total=len(valores),
            vencimento=somar_meses(primeiro, i, primeiro.day),
            valor_centavos=v,
            taxa_ppm=TAXA,
            carencia=CARENCIA,
        )
        for i, v in enumerate(valores)
    ]


def test_mochila_em_3x_juros_so_na_parcela_atrasada() -> None:
    compra = Evento(1, date(2026, 10, 10), "compra", 15_000)
    estado = calcular_estado([compra], {1: parcelas_da_compra(1, [5000, 5000, 5000])})
    hoje = dia(25)  # 1ª parcela 25 dias atrasada -> 15 dias de juros; a 2ª ainda não venceu
    assert estado.juros_a_lancar(hoje) == valor_juros(5000, TAXA, 15)
    assert estado.parcelas[0].situacao(hoje) == "vencida (com juros)"
    assert estado.parcelas[1].situacao(hoje) == "a vencer"
    assert estado.parcelas[0].situacao(dia(10)) == "vencida (sem juros)"
    assert estado.total_para_quitar(hoje) == 15_000 + valor_juros(5000, TAXA, 15)


def test_pagamento_abate_juros_primeiro_e_parcela_mais_antiga() -> None:
    parcelas = parcelas_da_compra(1, [5000, 5000, 5000])
    juros = valor_juros(5000, TAXA, 15)
    eventos = [
        Evento(1, date(2026, 10, 10), "compra", 15_000),
        Evento(2, dia(25), "juros", juros),  # lançado no dia do pagamento
        Evento(3, dia(25), "pagamento", 3000),
    ]
    estado = calcular_estado(eventos, {1: parcelas})
    assert estado.juros_pendentes_centavos == 0
    assert [p.aberto_centavos for p in estado.parcelas] == [5000 - (3000 - juros), 5000, 5000]
    assert estado.saldo_lancado_centavos == 15_000 + juros - 3000


def test_juros_continuam_so_sobre_o_que_falta() -> None:
    """Pagou metade da parcela atrasada: os juros seguem só sobre a outra metade."""
    parcelas = parcelas_da_compra(1, [10_000])
    juros_ate_d20 = valor_juros(10_000, TAXA, 10)  # dias 11..20 após a carência
    eventos = [
        Evento(1, date(2026, 10, 1), "compra", 10_000),
        Evento(2, dia(20), "juros", juros_ate_d20),
        Evento(3, dia(20), "pagamento", juros_ate_d20 + 5000),
    ]
    estado = calcular_estado(eventos, {1: parcelas})
    assert estado.parcelas[0].aberto_centavos == 5000
    # dos dias 21 a 30: 10 dias sobre R$ 50,00
    assert estado.juros_a_lancar(dia(30)) == valor_juros(5000, TAXA, 10)


def test_pagamento_dentro_da_carencia_nao_tem_juros() -> None:
    eventos = [
        Evento(1, date(2026, 10, 1), "compra", 10_000),
        Evento(2, dia(10), "pagamento", 10_000),
    ]
    estado = calcular_estado(eventos, {1: parcelas_da_compra(1, [10_000])})
    assert estado.saldo_lancado_centavos == 0 and estado.juros_a_lancar(dia(100)) == 0


def test_parcela_quitada_para_de_render_juros() -> None:
    juros = valor_juros(10_000, TAXA, 5)
    eventos = [
        Evento(1, date(2026, 10, 1), "compra", 10_000),
        Evento(2, dia(15), "juros", juros),
        Evento(3, dia(15), "pagamento", 10_000 + juros),
    ]
    estado = calcular_estado(eventos, {1: parcelas_da_compra(1, [10_000])})
    assert estado.parcelas[0].situacao(dia(200)) == "paga"
    assert estado.juros_a_lancar(dia(200)) == 0 and estado.saldo_lancado_centavos == 0


def test_pagamento_menor_que_os_juros_nao_reduz_o_principal() -> None:
    juros = valor_juros(10_000, TAXA, 30)  # 99
    eventos = [
        Evento(1, date(2026, 10, 1), "compra", 10_000),
        Evento(2, dia(40), "juros", juros),
        Evento(3, dia(40), "pagamento", 50),
    ]
    estado = calcular_estado(eventos, {1: parcelas_da_compra(1, [10_000])})
    assert estado.juros_pendentes_centavos == juros - 50
    assert estado.parcelas[0].aberto_centavos == 10_000
    # juros pendentes NÃO rendem juros: o próximo cálculo é só sobre o principal
    assert estado.juros_a_lancar(dia(50)) == valor_juros(10_000, TAXA, 10)


def test_fiado_comum_nao_tem_juros_e_e_pago_por_ordem_de_data() -> None:
    eventos = [
        Evento(1, date(2026, 9, 1), "compra", 2000),  # comum (sem parcelas)
        Evento(2, date(2026, 10, 1), "compra", 6000),  # parcelada, vence em 10/11
        Evento(3, date(2026, 10, 5), "pagamento", 2500),
    ]
    estado = calcular_estado(eventos, {2: parcelas_da_compra(2, [3000, 3000])})
    assert estado.comuns[0].aberto_centavos == 0  # o comum (01/09) foi quitado primeiro
    assert estado.parcelas[0].aberto_centavos == 2500
    assert estado.juros_a_lancar(dia(400)) > 0
    compra_comum = calcular_estado([Evento(1, date(2026, 1, 1), "compra", 5000)], {})
    assert compra_comum.juros_a_lancar(date(2027, 12, 31)) == 0


def test_juros_perdoado_nao_e_cobrado_de_novo() -> None:
    juros = valor_juros(10_000, TAXA, 20)
    eventos = [
        Evento(1, date(2026, 10, 1), "compra", 10_000),
        Evento(2, dia(30), "juros", juros, dispensado=True),  # admin estornou (perdoou)
    ]
    estado = calcular_estado(eventos, {1: parcelas_da_compra(1, [10_000])})
    assert estado.juros_pendentes_centavos == 0
    assert estado.juros_a_lancar(dia(30)) == 0  # o período perdoado não volta
    assert estado.juros_a_lancar(dia(31)) == valor_juros(10_000, TAXA, 1)


def test_credito_anterior_abate_a_compra_parcelada() -> None:
    eventos = [
        Evento(1, date(2026, 9, 1), "pagamento", 1000),  # adiantamento (crédito)
        Evento(2, date(2026, 10, 1), "compra", 6000),
    ]
    estado = calcular_estado(eventos, {2: parcelas_da_compra(2, [3000, 3000])})
    assert estado.credito_centavos == 0
    assert [p.aberto_centavos for p in estado.parcelas] == [2000, 3000]


def test_saldo_sempre_igual_a_soma_dos_lancamentos() -> None:
    """Conferência: o estado nunca "cria" nem "some" com dinheiro."""
    eventos = [
        Evento(1, date(2026, 9, 1), "compra", 1234),
        Evento(2, date(2026, 10, 1), "compra", 9999),
        Evento(3, dia(30), "juros", 77),
        Evento(4, dia(30), "pagamento", 5000),
        Evento(5, dia(31), "abatimento", 100),
        Evento(6, dia(40), "pagamento", 20_000),  # paga a mais: vira crédito
    ]
    estado = calcular_estado(eventos, {2: parcelas_da_compra(2, [3333, 3333, 3333])})
    soma = 1234 + 9999 + 77 - 5000 - 100 - 20_000
    assert estado.saldo_lancado_centavos == soma
    assert estado.credito_centavos == -soma


def test_tipo_desconhecido() -> None:
    with pytest.raises(ValueError):
        calcular_estado([Evento(1, VENCE, "outro", 1)], {})
