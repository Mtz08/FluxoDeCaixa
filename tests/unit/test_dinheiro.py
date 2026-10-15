import pytest

from app.core.dinheiro import ValorInvalido, formatar_reais, ler_valor


@pytest.mark.parametrize(
    ("texto", "centavos"),
    [
        ("12", 1200),
        ("12,5", 1250),
        ("12,50", 1250),
        ("0,01", 1),
        (",99", 99),
        ("R$ 1.234,56", 123456),
        ("r$1234,56", 123456),
        ("1.234", 123400),
        ("1.234.567", 123456700),
        ("12.5", 1250),
        ("1234.56", 123456),
        ("  3,00 ", 300),
        ("R$ 10,00", 1000),
        ("0", 0),
    ],
)
def test_ler_valor_aceita_formatos_do_balcao(texto: str, centavos: int) -> None:
    assert ler_valor(texto) == centavos


@pytest.mark.parametrize(
    "texto", ["", "   ", None, "abc", "-5", "1,234", "12,3,4", "1e3", "12,,0", "9999999999"]
)
def test_ler_valor_recusa_invalidos(texto: str | None) -> None:
    with pytest.raises(ValorInvalido):
        ler_valor(texto)


def test_nunca_usa_float_em_valores_que_quebram_ponto_flutuante() -> None:
    # 0,1 + 0,2 em float dá 0,30000000000000004; em centavos é exato.
    assert ler_valor("0,10") + ler_valor("0,20") == ler_valor("0,30")


@pytest.mark.parametrize(
    ("centavos", "texto"),
    [
        (0, "R$ 0,00"),
        (5, "R$ 0,05"),
        (123456, "R$ 1.234,56"),
        (100000000, "R$ 1.000.000,00"),
        (-2550, "R$ -25,50"),
        (None, ""),
    ],
)
def test_formatar_reais(centavos: int | None, texto: str) -> None:
    assert formatar_reais(centavos) == texto
