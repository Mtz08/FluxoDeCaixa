"""Dinheiro sempre em centavos inteiros. Nunca float.

`ler_valor` aceita o que se digita no balcão: "12", "12,5", "R$ 1.234,56",
"1234.56", " 3,00 ". Converte para centavos ou levanta ValorInvalido.
"""

from __future__ import annotations

import re

LIMITE_CONFIRMACAO_CENTAVOS = 50_000_00  # acima disso a tela pede confirmação extra

_SO_DIGITOS = re.compile(r"^\d+$")


class ValorInvalido(ValueError):
    """Valor digitado não pôde ser entendido como dinheiro."""


def ler_valor(texto: str | None) -> int:
    """Converte texto digitado em centavos (int > 0 não é exigido aqui)."""
    if texto is None:
        raise ValorInvalido("Informe o valor.")
    limpo = texto.strip().upper().replace("R$", "").replace(" ", "").replace(" ", "")
    if not limpo:
        raise ValorInvalido("Informe o valor.")
    if limpo.startswith("-"):
        raise ValorInvalido("O valor não pode ser negativo.")

    if "," in limpo:
        # Formato brasileiro: ponto é milhar, vírgula é decimal.
        inteiro, _, decimal = limpo.rpartition(",")
        inteiro = inteiro.replace(".", "")
    elif limpo.count(".") == 1 and len(limpo.rpartition(".")[2]) in (1, 2):
        # "12.5" / "1234.56": ponto usado como decimal (teclado numérico).
        inteiro, _, decimal = limpo.rpartition(".")
    else:
        # "1.234" ou "1.234.567": pontos de milhar.
        inteiro, decimal = limpo.replace(".", ""), ""

    inteiro = inteiro or "0"
    if not _SO_DIGITOS.match(inteiro) or (decimal and not _SO_DIGITOS.match(decimal)):
        raise ValorInvalido("Valor inválido. Use só números, ex.: 12,50.")
    if len(decimal) > 2:
        raise ValorInvalido("Use no máximo 2 casas depois da vírgula.")
    if len(inteiro) > 9:
        raise ValorInvalido("Valor alto demais.")

    return int(inteiro) * 100 + int(decimal.ljust(2, "0") or "0")


def formatar_reais(centavos: int | None) -> str:
    """123456 -> 'R$ 1.234,56'; negativos com sinal: 'R$ -1.234,56'."""
    if centavos is None:
        return ""
    sinal = "-" if centavos < 0 else ""
    reais, cent = divmod(abs(centavos), 100)
    reais_fmt = f"{reais:,}".replace(",", ".")
    return f"R$ {sinal}{reais_fmt},{cent:02d}"


def valor_para_campo(centavos: int) -> str:
    """123456 -> '1234,56' (valor para reenviar num campo de formulário)."""
    reais, cent = divmod(centavos, 100)
    return f"{reais},{cent:02d}"
