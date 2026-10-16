"""Relatórios gerenciais: resumo do dia, fluxo de caixa por período, despesas por
categoria, fechamentos por operador e fiado.

Critérios de data:
- Caixa: data/hora em que o lançamento foi feito (horário da loja).
- Fiado: data de referência (dia da compra/pagamento informado na tela).
- Estornos entram no dia em que foram feitos, descontando do tipo original.
Relatórios são do administrador (o operador só vê o caixa aberto).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.datas import dia_local, formatar_data, hoje_local, intervalo_utc
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import service as caixa
from app.modules.caixa.calculo import ResumoCaixa, calcular_resumo
from app.modules.caixa.models import MovimentoCaixa, SessaoCaixa, TipoMovimento
from app.modules.fiado import service as fiado
from app.modules.fiado.models import FiadoLancamento, TipoFiado
from app.modules.fiado.service import Painel
from app.modules.relatorios import repository as repo

MAX_DIAS = 366


# ---------------------------------------------------------------- período


@dataclass(frozen=True)
class Periodo:
    inicio: date
    fim: date
    preset: str

    @property
    def rotulo(self) -> str:
        if self.inicio == self.fim:
            return formatar_data(self.inicio)
        return f"{formatar_data(self.inicio)} a {formatar_data(self.fim)}"

    def dias(self) -> list[date]:
        return [self.inicio + timedelta(days=i) for i in range((self.fim - self.inicio).days + 1)]


def montar_periodo(preset: str, inicio: str | None = None, fim: str | None = None) -> Periodo:
    """Hoje, semana (seg→hoje), mês (dia 1→hoje) ou intervalo personalizado."""
    hoje = hoje_local()
    if preset == "semana":
        return Periodo(hoje - timedelta(days=hoje.weekday()), hoje, preset)
    if preset == "mes":
        return Periodo(hoje.replace(day=1), hoje, preset)
    if preset == "personalizado":
        try:
            ini, fi = date.fromisoformat(inicio or ""), date.fromisoformat(fim or "")
        except ValueError as erro:
            raise ErroNegocio("Informe as datas de início e fim.") from erro
        if fi < ini:
            raise ErroNegocio("A data final é anterior à inicial.")
        if (fi - ini).days >= MAX_DIAS:
            raise ErroNegocio("Escolha um período de até 1 ano.")
        return Periodo(ini, fi, preset)
    return Periodo(hoje, hoje, "hoje")


def _exigir_admin(usuario: Usuario) -> None:
    if not usuario.eh_admin:
        raise SemPermissao("Relatórios são do administrador.")


# ---------------------------------------------------------------- estruturas


@dataclass(frozen=True)
class LinhaDia:
    dia: date
    resumo: ResumoCaixa

    @property
    def resultado_centavos(self) -> int:
        """Receitas − despesas do dia (sangria/suprimento são só transferência de dinheiro)."""
        return self.resumo.faturamento_centavos - self.resumo.despesas_centavos


@dataclass(frozen=True)
class LinhaCategoria:
    categoria: str
    total_centavos: int


@dataclass
class LinhaOperador:
    nome: str
    fechamentos: int = 0
    sobras_centavos: int = 0
    faltas_centavos: int = 0  # valor positivo

    @property
    def saldo_centavos(self) -> int:
        return self.sobras_centavos - self.faltas_centavos


@dataclass
class ResumoFiadoPeriodo:
    compras_centavos: int = 0  # vendas a prazo (líquido de estornos)
    recebido_centavos: int = 0
    abatimentos_centavos: int = 0
    saldo_inicial_centavos: int = 0  # dívidas trazidas do caderno (não são vendas do período)
    juros_centavos: int = 0  # juros por atraso de parcelas lançados no período (líquido)


@dataclass
class RelatorioPeriodo:
    periodo: Periodo
    total: ResumoCaixa
    dias: list[LinhaDia]
    categorias: list[LinhaCategoria]
    fechamentos: list[SessaoCaixa]
    operadores: list[LinhaOperador]
    fiado: ResumoFiadoPeriodo
    a_receber: Painel
    movimentos: list[MovimentoCaixa] = field(default_factory=list)

    @property
    def resultado_centavos(self) -> int:
        return self.total.faturamento_centavos - self.total.despesas_centavos


# ---------------------------------------------------------------- cálculos


def _resumo(movimentos: list[MovimentoCaixa]) -> ResumoCaixa:
    resumo = calcular_resumo(0, caixa.itens_resumo(movimentos))
    resumo.estornos_qtd = sum(1 for m in movimentos if m.tipo == TipoMovimento.ESTORNO)
    return resumo


def _por_dia(periodo: Periodo, movimentos: list[MovimentoCaixa]) -> list[LinhaDia]:
    por_dia: dict[date, list[MovimentoCaixa]] = defaultdict(list)
    for mov in movimentos:
        por_dia[dia_local(mov.criado_em)].append(mov)
    return [LinhaDia(dia, _resumo(por_dia[dia])) for dia in periodo.dias()]


def _por_categoria(movimentos: list[MovimentoCaixa]) -> list[LinhaCategoria]:
    totais: dict[str, int] = defaultdict(int)
    for mov in movimentos:
        if mov.tipo_efetivo == TipoMovimento.SAIDA:
            nome = mov.categoria.nome if mov.categoria else "Sem categoria"
            totais[nome] += -mov.valor_com_sinal  # saída = positivo; estorno desconta
    linhas = [LinhaCategoria(nome, total) for nome, total in totais.items() if total]
    return sorted(linhas, key=lambda linha: -linha.total_centavos)


def _por_operador(fechamentos: list[SessaoCaixa]) -> list[LinhaOperador]:
    operadores: dict[str, LinhaOperador] = {}
    for sessao in fechamentos:
        nome = sessao.usuario_fechamento.nome if sessao.usuario_fechamento else "?"
        linha = operadores.setdefault(nome, LinhaOperador(nome))
        linha.fechamentos += 1
        diferenca = sessao.diferenca_centavos or 0
        if diferenca > 0:
            linha.sobras_centavos += diferenca
        else:
            linha.faltas_centavos += -diferenca
    return sorted(operadores.values(), key=lambda linha: linha.nome)


def _fiado(lancamentos: list[FiadoLancamento]) -> ResumoFiadoPeriodo:
    resumo = ResumoFiadoPeriodo()
    for lanc in lancamentos:
        valor = lanc.valor_com_sinal
        tipo = lanc.tipo_efetivo
        if tipo == TipoFiado.COMPRA:
            resumo.compras_centavos += valor
        elif tipo == TipoFiado.PAGAMENTO:
            resumo.recebido_centavos += -valor
        elif tipo == TipoFiado.ABATIMENTO:
            resumo.abatimentos_centavos += -valor
        elif tipo == TipoFiado.SALDO_INICIAL:
            resumo.saldo_inicial_centavos += valor
        elif tipo == TipoFiado.JUROS:
            resumo.juros_centavos += valor
    return resumo


# ---------------------------------------------------------------- relatórios


def relatorio_periodo(db: Session, usuario: Usuario, periodo: Periodo) -> RelatorioPeriodo:
    _exigir_admin(usuario)
    inicio, fim = intervalo_utc(periodo.inicio, periodo.fim)
    movimentos = repo.movimentos_no_intervalo(db, inicio, fim)
    fechamentos = repo.fechamentos_no_intervalo(db, inicio, fim)
    return RelatorioPeriodo(
        periodo=periodo,
        total=_resumo(movimentos),
        dias=_por_dia(periodo, movimentos),
        categorias=_por_categoria(movimentos),
        fechamentos=fechamentos,
        operadores=_por_operador(fechamentos),
        fiado=_fiado(repo.fiado_no_periodo(db, periodo.inicio, periodo.fim)),
        a_receber=fiado.painel(db, "atraso"),
        movimentos=movimentos,
    )


@dataclass(frozen=True)
class ResumoDia:
    """Tela inicial do administrador."""

    caixa: ResumoCaixa
    gaveta_centavos: int | None  # esperado no caixa aberto (None = caixa fechado)
    fiado: ResumoFiadoPeriodo


def resumo_do_dia(db: Session, usuario: Usuario) -> ResumoDia:
    _exigir_admin(usuario)
    hoje = hoje_local()
    inicio, fim = intervalo_utc(hoje, hoje)
    sessao = caixa.sessao_aberta(db)
    gaveta = caixa.resumo_da_sessao(db, sessao).esperado_gaveta_centavos if sessao else None
    return ResumoDia(
        caixa=_resumo(repo.movimentos_no_intervalo(db, inicio, fim)),
        gaveta_centavos=gaveta,
        fiado=_fiado(repo.fiado_no_periodo(db, hoje, hoje)),
    )
