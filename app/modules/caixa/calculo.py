"""Cálculo do resumo de uma sessão de caixa (função pura, sem banco).

Valor esperado na gaveta:
    fundo de troco
  + entradas em dinheiro + recebimentos de fiado em dinheiro + suprimentos
  − saídas em dinheiro − sangrias
  ± estornos (só os que mexem na gaveta de hoje)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.modules.caixa.models import SINAL_DO_TIPO, TipoMovimento

TIPOS_RECEITA = (TipoMovimento.ENTRADA, TipoMovimento.RECEBIMENTO_FIADO)


@dataclass(frozen=True)
class ItemResumo:
    """Uma parte de pagamento de um movimento, já com o contexto necessário."""

    tipo_efetivo: TipoMovimento  # para estorno, o tipo do original
    eh_estorno: bool
    sinal: int  # sinal gravado no movimento
    valor_centavos: int  # valor desta parte (sempre > 0)
    forma: str
    forma_ordem: int
    afeta_gaveta: bool  # a forma de pagamento é dinheiro físico
    considera_gaveta: bool  # False: estorno de caixa anterior


@dataclass
class LinhaForma:
    forma: str
    recebido_centavos: int = 0  # entradas + recebimentos de fiado (líquido de estornos)
    pago_centavos: int = 0  # saídas/despesas (líquido de estornos)


@dataclass
class ResumoCaixa:
    fundo_troco_centavos: int
    # Total líquido por tipo, sempre na "direção natural" do tipo (saída positiva = saiu).
    por_tipo: dict[TipoMovimento, int] = field(default_factory=dict)
    por_forma: list[LinhaForma] = field(default_factory=list)
    # Composição da gaveta (só dinheiro), na direção natural de cada tipo.
    gaveta: dict[TipoMovimento, int] = field(default_factory=dict)
    esperado_gaveta_centavos: int = 0
    estornos_qtd: int = 0
    estornos_fora_da_gaveta_centavos: int = 0  # estornos de caixas anteriores (com sinal)

    @property
    def faturamento_centavos(self) -> int:
        return sum(linha.recebido_centavos for linha in self.por_forma)

    @property
    def despesas_centavos(self) -> int:
        return sum(linha.pago_centavos for linha in self.por_forma)


def calcular_resumo(fundo_troco_centavos: int, itens: list[ItemResumo]) -> ResumoCaixa:
    por_tipo: dict[TipoMovimento, int] = defaultdict(int)
    gaveta: dict[TipoMovimento, int] = defaultdict(int)
    formas: dict[str, LinhaForma] = {}
    ordem_formas: dict[str, int] = {}
    esperado = fundo_troco_centavos
    estornos_fora = 0

    for item in itens:
        com_sinal = item.sinal * item.valor_centavos
        natural = com_sinal * SINAL_DO_TIPO[item.tipo_efetivo]
        por_tipo[item.tipo_efetivo] += natural

        linha = formas.setdefault(item.forma, LinhaForma(item.forma))
        ordem_formas[item.forma] = item.forma_ordem
        if item.tipo_efetivo in TIPOS_RECEITA:
            linha.recebido_centavos += com_sinal
        elif item.tipo_efetivo == TipoMovimento.SAIDA:
            linha.pago_centavos -= com_sinal

        if item.afeta_gaveta and item.considera_gaveta:
            gaveta[item.tipo_efetivo] += natural
            esperado += com_sinal
        if item.eh_estorno and not item.considera_gaveta:
            estornos_fora += com_sinal

    linhas = sorted(formas.values(), key=lambda linha: (ordem_formas[linha.forma], linha.forma))
    return ResumoCaixa(
        fundo_troco_centavos=fundo_troco_centavos,
        por_tipo=dict(por_tipo),
        por_forma=[linha for linha in linhas if linha.recebido_centavos or linha.pago_centavos],
        gaveta=dict(gaveta),
        esperado_gaveta_centavos=esperado,
        estornos_fora_da_gaveta_centavos=estornos_fora,
    )
