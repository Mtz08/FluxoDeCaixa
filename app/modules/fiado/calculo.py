"""Cálculos do fiado sem banco: saldo, compra mais antiga em aberto, atraso, WhatsApp."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from app.core.datas import formatar_data
from app.core.dinheiro import formatar_reais


@dataclass(frozen=True)
class ItemFiado:
    id: int
    sinal: int
    valor_centavos: int
    data: date
    eh_estorno: bool
    estorno_de_id: int | None


def saldo(itens: list[ItemFiado]) -> int:
    """+ = cliente deve; − = cliente tem crédito."""
    return sum(i.sinal * i.valor_centavos for i in itens)


def compra_mais_antiga_em_aberto(itens: list[ItemFiado]) -> date | None:
    """Data do débito mais antigo ainda não quitado.

    Regra (confirmada): pagamentos e abatimentos quitam primeiro as compras mais
    antigas. Lançamentos estornados (e seus estornos) são ignorados — um anula o outro.
    """
    estornados = {i.estorno_de_id for i in itens if i.eh_estorno}
    validos = [i for i in itens if not i.eh_estorno and i.id not in estornados]
    debitos = sorted((i for i in validos if i.sinal > 0), key=lambda i: (i.data, i.id))
    creditos = sum(i.valor_centavos for i in validos if i.sinal < 0)
    for debito in debitos:
        if creditos >= debito.valor_centavos:
            creditos -= debito.valor_centavos
        else:
            return debito.data
    return None


def faixa_atraso(dias: int | None) -> str:
    if dias is None:
        return ""
    if dias >= 90:
        return "90+ dias"
    if dias >= 60:
        return "60+ dias"
    if dias >= 30:
        return "30+ dias"
    return "até 30 dias"


# ---------------------------------------------------------------- busca e telefone


def normalizar_busca(*partes: str | None) -> str:
    """Minúsculo e sem acento: 'João' e 'joao' acham o mesmo cliente."""
    texto = " ".join(p for p in partes if p)
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def normalizar_telefone(texto: str | None) -> str | None:
    """Guarda só DDD + número (10 ou 11 dígitos). Aceita (11) 98888-7777, +55..., etc."""
    if not texto or not texto.strip():
        return None
    digitos = re.sub(r"\D", "", texto)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    if digitos.startswith("0"):
        digitos = digitos.lstrip("0")
    if len(digitos) not in (10, 11):
        raise ValueError("Telefone inválido. Use DDD + número, ex.: (11) 98888-7777.")
    return digitos


def formatar_telefone(digitos: str | None) -> str:
    if not digitos:
        return ""
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"


def normalizar_cpf(texto: str | None) -> str | None:
    """Só dígitos, com os dígitos verificadores conferidos."""
    if not texto or not texto.strip():
        return None
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        raise ValueError("CPF inválido.")
    for posicao in (9, 10):
        soma = sum(int(d) * (posicao + 1 - i) for i, d in enumerate(digitos[:posicao]))
        verificador = (soma * 10) % 11 % 10
        if verificador != int(digitos[posicao]):
            raise ValueError("CPF inválido.")
    return digitos


def formatar_cpf(digitos: str | None) -> str:
    if not digitos:
        return ""
    return f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"


# ---------------------------------------------------------------- WhatsApp


def mensagem_extrato(
    tratamento: str, loja: str, saldo_centavos: int, desde: date | None, juros_centavos: int = 0
) -> str:
    """Mensagem educada com o saldo (já com os juros por atraso, se houver)."""
    inicio = f"Olá, {tratamento}! Tudo bem? Aqui é da {loja}."
    if saldo_centavos > 0:
        quando = f", referente a compras desde {formatar_data(desde)}" if desde else ""
        juros = (
            f" (inclui {formatar_reais(juros_centavos)} de juros por atraso de parcela)"
            if juros_centavos > 0
            else ""
        )
        corpo = (
            f" Passando para lembrar que o seu saldo em aberto conosco é de "
            f"{formatar_reais(saldo_centavos)}{juros}{quando}. "
            "Quando puder, é só passar aqui na loja."
        )
    elif saldo_centavos < 0:
        corpo = f" Você tem um crédito de {formatar_reais(-saldo_centavos)} conosco."
    else:
        corpo = " A sua conta conosco está em dia. Obrigado pela confiança!"
    return inicio + corpo + " Qualquer dúvida, estamos à disposição."


def link_whatsapp(telefone: str | None, mensagem: str) -> str | None:
    """Link wa.me: abre o WhatsApp com a mensagem pronta; a pessoa confirma o envio."""
    if not telefone:
        return None
    return f"https://wa.me/55{telefone}?text={quote(mensagem)}"
