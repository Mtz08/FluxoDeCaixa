"""Cálculo do resumo e do valor esperado na gaveta (sem banco)."""

from app.modules.caixa.calculo import ItemResumo, calcular_resumo
from app.modules.caixa.models import TipoMovimento as T


def item(
    tipo: T,
    valor: int,
    forma: str = "Dinheiro",
    *,
    sinal: int | None = None,
    estorno: bool = False,
    considera_gaveta: bool = True,
) -> ItemResumo:
    naturais = {T.ENTRADA: 1, T.RECEBIMENTO_FIADO: 1, T.SUPRIMENTO: 1, T.SAIDA: -1, T.SANGRIA: -1}
    s = sinal if sinal is not None else naturais[tipo] * (-1 if estorno else 1)
    return ItemResumo(
        tipo_efetivo=tipo,
        eh_estorno=estorno,
        sinal=s,
        valor_centavos=valor,
        forma=forma,
        forma_ordem={"Dinheiro": 1, "PIX": 2, "Cartão de débito": 3}[forma],
        afeta_gaveta=forma == "Dinheiro",
        considera_gaveta=considera_gaveta,
    )


def test_caixa_vazio_espera_so_o_fundo() -> None:
    resumo = calcular_resumo(10000, [])
    assert resumo.esperado_gaveta_centavos == 10000
    assert resumo.faturamento_centavos == 0
    assert resumo.por_forma == []


def test_formula_do_esperado_na_gaveta() -> None:
    # fundo 100 + entrada 50 + fiado 12 + suprimento 20 − saída 15 − sangria 40 = 127
    itens = [
        item(T.ENTRADA, 5000),
        item(T.RECEBIMENTO_FIADO, 1200),
        item(T.SUPRIMENTO, 2000),
        item(T.SAIDA, 1500),
        item(T.SANGRIA, 4000),
    ]
    resumo = calcular_resumo(10000, itens)
    assert resumo.esperado_gaveta_centavos == 12700
    assert resumo.gaveta == {
        T.ENTRADA: 5000,
        T.RECEBIMENTO_FIADO: 1200,
        T.SUPRIMENTO: 2000,
        T.SAIDA: 1500,
        T.SANGRIA: 4000,
    }


def test_pagamento_dividido_so_o_dinheiro_vai_para_a_gaveta() -> None:
    # Venda de R$ 50: R$ 30 no PIX + R$ 20 em dinheiro.
    itens = [item(T.ENTRADA, 3000, "PIX"), item(T.ENTRADA, 2000, "Dinheiro")]
    resumo = calcular_resumo(0, itens)
    assert resumo.esperado_gaveta_centavos == 2000
    assert resumo.faturamento_centavos == 5000
    assert [(f.forma, f.recebido_centavos) for f in resumo.por_forma] == [
        ("Dinheiro", 2000),
        ("PIX", 3000),
    ]


def test_despesa_no_pix_nao_mexe_na_gaveta() -> None:
    resumo = calcular_resumo(5000, [item(T.SAIDA, 1000, "PIX")])
    assert resumo.esperado_gaveta_centavos == 5000
    assert resumo.despesas_centavos == 1000


def test_estorno_na_mesma_sessao_anula_o_original() -> None:
    itens = [item(T.ENTRADA, 5000), item(T.ENTRADA, 5000, estorno=True)]
    resumo = calcular_resumo(1000, itens)
    assert resumo.esperado_gaveta_centavos == 1000
    assert resumo.faturamento_centavos == 0
    assert resumo.por_tipo[T.ENTRADA] == 0


def test_estorno_de_saida_devolve_dinheiro_a_gaveta() -> None:
    itens = [item(T.SAIDA, 3000), item(T.SAIDA, 3000, estorno=True)]
    resumo = calcular_resumo(10000, itens)
    assert resumo.esperado_gaveta_centavos == 10000
    assert resumo.despesas_centavos == 0


def test_estorno_de_caixa_anterior_nao_mexe_na_gaveta_de_hoje() -> None:
    itens = [item(T.ENTRADA, 2000), item(T.ENTRADA, 700, estorno=True, considera_gaveta=False)]
    resumo = calcular_resumo(0, itens)
    assert resumo.esperado_gaveta_centavos == 2000
    assert resumo.estornos_fora_da_gaveta_centavos == -700
    # Mas aparece no faturamento do dia (lançamento de correção na data do estorno).
    assert resumo.faturamento_centavos == 1300


def test_resultado_e_exato_em_centavos() -> None:
    # 0,10 + 0,20 com float daria 0,30000000000000004.
    resumo = calcular_resumo(0, [item(T.ENTRADA, 10), item(T.ENTRADA, 20)])
    assert resumo.esperado_gaveta_centavos == 30
