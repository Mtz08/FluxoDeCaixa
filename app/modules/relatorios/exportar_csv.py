"""Exportação CSV para planilha e para o escritório de contabilidade.

Formato do Excel em português: separador ";", decimal com vírgula, UTF-8 com BOM.
Valores em reais SEM "R$" (a planilha reconhece como número).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from app.core.datas import formatar_data, formatar_data_hora
from app.modules.caixa.models import TipoMovimento as T
from app.modules.relatorios.service import RelatorioPeriodo

BOM = "﻿"
_INICIO_PERIGOSO = ("=", "+", "-", "@", "\t", "\r")


def numero(centavos: int | None) -> str:
    """123456 -> '1234,56'; -500 -> '-5,00'. Sem separador de milhar (planilha entende)."""
    if centavos is None:
        return ""
    sinal = "-" if centavos < 0 else ""
    reais, cent = divmod(abs(centavos), 100)
    return f"{sinal}{reais},{cent:02d}"


def texto(valor: object) -> str:
    """Evita "injeção de fórmula": texto começando com = + - @ vira texto literal."""
    conteudo = "" if valor is None else str(valor)
    if conteudo.startswith(_INICIO_PERIGOSO):
        return "'" + conteudo
    return conteudo


def _gerar(cabecalho: Sequence[str], linhas: Iterable[Sequence[str]]) -> str:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    return BOM + saida.getvalue()


def movimentos(rel: RelatorioPeriodo) -> str:
    """Uma linha por forma de pagamento de cada lançamento (pagamento dividido = várias linhas)."""
    cabecalho = [
        "Data/hora",
        "Lançamento",
        "Caixa",
        "Tipo",
        "Descrição",
        "Categoria",
        "Forma de pagamento",
        "Valor (R$)",
        "Usuário",
        "Estorno do lançamento",
        "Origem",
    ]
    linhas = [
        [
            formatar_data_hora(m.criado_em),
            str(m.id),
            str(m.sessao_id),
            texto(m.rotulo),
            texto(m.descricao),
            texto(m.categoria.nome if m.categoria else ""),
            texto(p.forma.nome),
            numero(m.sinal * p.valor_centavos),
            texto(m.usuario.nome),
            str(m.estorno_de_id or ""),
            m.origem,
        ]
        for m in rel.movimentos
        for p in m.partes
    ]
    return _gerar(cabecalho, linhas)


def fluxo_diario(rel: RelatorioPeriodo) -> str:
    cabecalho = [
        "Data",
        "Entradas",
        "Recebimentos de fiado",
        "Saídas (despesas)",
        "Sangrias",
        "Suprimentos",
        "Resultado do dia",
    ]
    linhas = [
        [
            formatar_data(d.dia),
            numero(d.resumo.por_tipo.get(T.ENTRADA, 0)),
            numero(d.resumo.por_tipo.get(T.RECEBIMENTO_FIADO, 0)),
            numero(d.resumo.por_tipo.get(T.SAIDA, 0)),
            numero(d.resumo.por_tipo.get(T.SANGRIA, 0)),
            numero(d.resumo.por_tipo.get(T.SUPRIMENTO, 0)),
            numero(d.resultado_centavos),
        ]
        for d in rel.dias
    ]
    return _gerar(cabecalho, linhas)


def formas(rel: RelatorioPeriodo) -> str:
    linhas = [
        [texto(f.forma), numero(f.recebido_centavos), numero(f.pago_centavos)]
        for f in rel.total.por_forma
    ]
    return _gerar(["Forma de pagamento", "Recebido", "Pago (despesas)"], linhas)


def despesas(rel: RelatorioPeriodo) -> str:
    linhas = [[texto(c.categoria), numero(c.total_centavos)] for c in rel.categorias]
    return _gerar(["Categoria", "Total"], linhas)


def fechamentos(rel: RelatorioPeriodo) -> str:
    cabecalho = [
        "Caixa",
        "Aberto em",
        "Aberto por",
        "Fechado em",
        "Fechado por",
        "Fundo de troco",
        "Esperado",
        "Contado",
        "Diferença",
        "Justificativa",
    ]
    linhas = [
        [
            str(s.id),
            formatar_data_hora(s.aberta_em),
            texto(s.usuario_abertura.nome),
            formatar_data_hora(s.fechada_em),
            texto(s.usuario_fechamento.nome if s.usuario_fechamento else ""),
            numero(s.fundo_troco_centavos),
            numero(s.valor_esperado_centavos),
            numero(s.valor_contado_centavos),
            numero(s.diferenca_centavos),
            texto(s.justificativa),
        ]
        for s in rel.fechamentos
    ]
    return _gerar(cabecalho, linhas)


def fiado_a_receber(rel: RelatorioPeriodo) -> str:
    """Contém nomes de clientes (dado pessoal): exportação só do administrador."""
    linhas = [
        [
            texto(linha.cliente.nome),
            numero(linha.saldo_centavos),
            formatar_data(linha.desde),
            str(linha.dias if linha.dias is not None else ""),
            linha.faixa,
        ]
        for linha in rel.a_receber.linhas
    ]
    cabecalho = ["Cliente", "Saldo devedor", "Em aberto desde", "Dias", "Faixa"]
    return _gerar(cabecalho, linhas)


EXPORTADORES = {
    "lancamentos": movimentos,
    "fluxo-diario": fluxo_diario,
    "formas-pagamento": formas,
    "despesas": despesas,
    "fechamentos": fechamentos,
    "fiado-a-receber": fiado_a_receber,
}
