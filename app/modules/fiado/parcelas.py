"""Parcelas e juros por atraso do fiado — cálculo puro (sem banco), testado à parte.

REGRAS CONFIRMADAS (16/10/2026):
- Compra parcelada: N parcelas mensais a partir da data da 1ª, mesmo dia do mês
  (se o mês não tiver o dia, usa o último dia do mês). Centavos que sobram da divisão
  vão para as primeiras parcelas (100,00 em 3x = 33,34 + 33,33 + 33,33).
- Carência: os `carencia` dias seguintes ao vencimento NÃO têm juros.
  Juros SÓ a partir do (carencia + 1)º dia, contando apenas os dias após a carência:
  vence dia 10, carência 10 → dias 11..20 sem juros; dia 21 = 1 dia de juros.
- Juros SIMPLES (fixos por dia), só sobre o que FALTA PAGAR da parcela (nunca sobre
  juros: não há juros sobre juros).
- Pagamento (e abatimento) abate PRIMEIRO os juros, DEPOIS o principal, começando pelo
  débito que venceu primeiro (parcela: vencimento; fiado comum: data da compra).
- Fiado comum (sem parcelar) NÃO tem juros.
- Taxa e carência ficam gravadas em cada parcela no dia da compra (o "contrato"
  informado ao cliente). Mudar a configuração só vale para compras novas.

Taxa em partes por milhão por dia (ppm): 0,033% ao dia = 0,00033 = 330 ppm.
Tudo em centavos inteiros; arredondamento meio-para-cima por parcela.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

PPM = 1_000_000
MAX_PARCELAS = 24


# ---------------------------------------------------------------- taxa


def percentual_para_ppm(texto: str) -> int:
    """'0,033' (% ao dia) -> 330 ppm. Aceita vírgula ou ponto; até 4 casas decimais."""
    try:
        valor = Decimal(texto.strip().replace("%", "").replace(",", "."))
    except (InvalidOperation, AttributeError) as erro:
        raise ValueError("Taxa inválida. Use, por exemplo, 0,033.") from erro
    if valor < 0 or valor > 1:
        raise ValueError("A taxa deve ficar entre 0 e 1% ao dia.")
    if valor != valor.quantize(Decimal("0.0001")):
        raise ValueError("Use no máximo 4 casas decimais na taxa.")
    return int(valor * 10_000)


def ppm_para_percentual(ppm: int) -> str:
    """330 -> '0,033' (sem zeros à direita desnecessários)."""
    texto = f"{Decimal(ppm) / Decimal(10_000):f}".rstrip("0").rstrip(".")
    return (texto or "0").replace(".", ",")


def ppm_ao_mes_aproximado(ppm: int) -> str:
    """Só para informar o cliente: taxa diária x 30 (juros simples)."""
    return ppm_para_percentual(ppm * 30)


# ---------------------------------------------------------------- geração das parcelas


def somar_meses(dia: date, meses: int, dia_desejado: int | None = None) -> date:
    """Mesmo dia do mês, `meses` depois; se não existir, o último dia do mês."""
    alvo = dia_desejado or dia.day
    ano, mes = divmod(dia.month - 1 + meses, 12)
    ano += dia.year
    mes += 1
    return date(ano, mes, min(alvo, calendar.monthrange(ano, mes)[1]))


@dataclass(frozen=True)
class NovaParcela:
    numero: int
    vencimento: date
    valor_centavos: int


def gerar_parcelas(total_centavos: int, quantidade: int, primeiro: date) -> list[NovaParcela]:
    if not 1 <= quantidade <= MAX_PARCELAS:
        raise ValueError(f"Parcelas: de 1 a {MAX_PARCELAS}.")
    if total_centavos < quantidade:
        raise ValueError("Valor pequeno demais para essa quantidade de parcelas.")
    base, sobra = divmod(total_centavos, quantidade)
    return [
        NovaParcela(
            numero=i + 1,
            vencimento=somar_meses(primeiro, i, primeiro.day),
            valor_centavos=base + (1 if i < sobra else 0),
        )
        for i in range(quantidade)
    ]


# ---------------------------------------------------------------- juros


def inicio_dos_juros(vencimento: date, carencia: int) -> date:
    """Último dia SEM juros. Juros contam a partir do dia seguinte."""
    return vencimento + timedelta(days=carencia)


def dias_com_juros(vencimento: date, carencia: int, ja_calculado_ate: date, ate: date) -> int:
    """Dias com juros no intervalo (ja_calculado_ate, ate], fora da carência."""
    desde = max(ja_calculado_ate, inicio_dos_juros(vencimento, carencia))
    return max(0, (ate - desde).days)


def valor_juros(principal_centavos: int, taxa_ppm: int, dias: int) -> int:
    """Juros simples em centavos, arredondamento meio-para-cima (inteiros, sem float)."""
    if principal_centavos <= 0 or taxa_ppm <= 0 or dias <= 0:
        return 0
    return (principal_centavos * taxa_ppm * dias + PPM // 2) // PPM


# ---------------------------------------------------------------- estado do cliente


@dataclass
class Parcela:
    id: int
    compra_id: int
    numero: int
    total: int
    vencimento: date
    valor_centavos: int
    taxa_ppm: int
    carencia: int
    aberto_centavos: int = 0
    juros_calculado_ate: date = date.min  # juros já lançados até este dia

    def __post_init__(self) -> None:
        if self.juros_calculado_ate == date.min:
            self.juros_calculado_ate = inicio_dos_juros(self.vencimento, self.carencia)

    @property
    def pago_centavos(self) -> int:
        return self.valor_centavos - self.aberto_centavos

    def juros_ate(self, dia: date) -> int:
        """Juros ainda não lançados, de `juros_calculado_ate` até `dia`."""
        dias = dias_com_juros(self.vencimento, self.carencia, self.juros_calculado_ate, dia)
        return valor_juros(self.aberto_centavos, self.taxa_ppm, dias)

    def dias_em_atraso(self, dia: date) -> int:
        return max(0, (dia - self.vencimento).days) if self.aberto_centavos else 0

    def situacao(self, dia: date) -> str:
        if self.aberto_centavos == 0:
            return "paga"
        if dia <= self.vencimento:
            return "a vencer"
        if dia <= inicio_dos_juros(self.vencimento, self.carencia):
            return "vencida (sem juros)"
        return "vencida (com juros)"


@dataclass
class DebitoComum:
    """Fiado comum / saldo do caderno / devolução de crédito: sem juros."""

    id: int
    data: date
    aberto_centavos: int


@dataclass(frozen=True)
class Evento:
    """Um lançamento do fiado, já sem os estornados (ver `montar_eventos`)."""

    id: int
    data: date
    tipo: str  # compra | saldo_inicial | devolucao_credito | pagamento | abatimento | juros
    valor_centavos: int
    dispensado: bool = False  # juros estornado (perdoado): avança a data, não cobra


@dataclass
class EstadoFiado:
    parcelas: list[Parcela] = field(default_factory=list)
    comuns: list[DebitoComum] = field(default_factory=list)
    juros_pendentes_centavos: int = 0  # juros já lançados e ainda não pagos
    credito_centavos: int = 0  # valor pago a mais (a favor do cliente)

    @property
    def principal_aberto_centavos(self) -> int:
        return sum(p.aberto_centavos for p in self.parcelas) + sum(
            d.aberto_centavos for d in self.comuns
        )

    @property
    def saldo_lancado_centavos(self) -> int:
        """Igual à soma dos lançamentos (o saldo "do caderno")."""
        return (
            self.juros_pendentes_centavos + self.principal_aberto_centavos - self.credito_centavos
        )

    def juros_a_lancar(self, dia: date) -> int:
        return sum(p.juros_ate(dia) for p in self.parcelas)

    def total_para_quitar(self, dia: date) -> int:
        return self.saldo_lancado_centavos + self.juros_a_lancar(dia)

    # ---- aplicação de pagamentos e créditos

    def _debitos_em_ordem(self) -> list[Parcela | DebitoComum]:
        def chave(item: Parcela | DebitoComum) -> tuple[date, int, int]:
            if isinstance(item, Parcela):
                return (item.vencimento, item.compra_id, item.numero)
            return (item.data, item.id, 0)

        return sorted([*self.parcelas, *self.comuns], key=chave)

    def _abater_principal(self, valor: int) -> int:
        """Abate do principal, do que venceu primeiro. Devolve o que sobrou."""
        for item in self._debitos_em_ordem():
            if valor <= 0:
                break
            usado = min(valor, item.aberto_centavos)
            item.aberto_centavos -= usado
            valor -= usado
        return valor

    def _usar_credito(self) -> None:
        if self.credito_centavos > 0:
            self.credito_centavos = self._abater_principal(self.credito_centavos)

    def receber(self, valor: int) -> None:
        """Pagamento/abatimento: primeiro os juros lançados, depois o principal."""
        dos_juros = min(valor, self.juros_pendentes_centavos)
        self.juros_pendentes_centavos -= dos_juros
        self.credito_centavos += self._abater_principal(valor - dos_juros)

    def lancar_juros(self, dia: date, valor: int, dispensado: bool) -> None:
        if not dispensado:
            self.juros_pendentes_centavos += valor
        for parcela in self.parcelas:
            if dia > parcela.juros_calculado_ate:
                parcela.juros_calculado_ate = dia
        if not dispensado:
            # Juros entram como dívida; um crédito existente os quita primeiro.
            quitado = min(self.credito_centavos, self.juros_pendentes_centavos)
            self.credito_centavos -= quitado
            self.juros_pendentes_centavos -= quitado


DEBITOS_COMUNS = {"compra", "saldo_inicial", "devolucao_credito"}
CREDITOS = {"pagamento", "abatimento"}


def calcular_estado(eventos: list[Evento], parcelas: dict[int, list[Parcela]]) -> EstadoFiado:
    """Refaz a história do cliente em ordem (data, id).

    `parcelas`: para cada compra parcelada (id do lançamento), suas parcelas.
    Compras com parcelas viram parcelas; as demais, débitos comuns (sem juros).
    """
    estado = EstadoFiado()
    for evento in sorted(eventos, key=lambda e: (e.data, e.id)):
        if evento.tipo == "juros":
            estado.lancar_juros(evento.data, evento.valor_centavos, evento.dispensado)
        elif evento.tipo in CREDITOS:
            estado.receber(evento.valor_centavos)
        elif evento.tipo == "compra" and evento.id in parcelas:
            for parcela in parcelas[evento.id]:
                parcela.aberto_centavos = parcela.valor_centavos
                estado.parcelas.append(parcela)
            estado._usar_credito()
        elif evento.tipo in DEBITOS_COMUNS:
            estado.comuns.append(DebitoComum(evento.id, evento.data, evento.valor_centavos))
            estado._usar_credito()
        else:
            raise ValueError(f"Tipo de lançamento desconhecido: {evento.tipo}")
    return estado
