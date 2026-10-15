"""PDF (A4) do fechamento de caixa e do relatório do período, com fpdf2.

As fontes internas do PDF (Helvetica) só têm caracteres Latin-1: acentos do
português funcionam; símbolos (▲, ✔, emoji) são trocados por texto.
"""

from __future__ import annotations

from collections.abc import Sequence

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.core.datas import agora_utc, formatar_data, formatar_data_hora
from app.core.dinheiro import formatar_reais
from app.modules.caixa.models import ROTULO_TIPO
from app.modules.caixa.models import TipoMovimento as T
from app.modules.caixa.service import ResumoSessao
from app.modules.relatorios.service import RelatorioPeriodo

_TROCAS = {"−": "-", "—": "-", "–": "-", "▲": "+", "▼": "-", "✔": "", "…": "..."}
ALTURA = 6.5


def limpar(texto: object) -> str:
    """Deixa o texto compatível com a fonte do PDF (Latin-1)."""
    conteudo = "" if texto is None else str(texto)
    for de, para in _TROCAS.items():
        conteudo = conteudo.replace(de, para)
    return conteudo.encode("latin-1", "replace").decode("latin-1")


def _diferenca(centavos: int | None) -> str:
    if not centavos:
        return "Sem diferença"
    if centavos > 0:
        return f"Sobra de {formatar_reais(centavos)}"
    return f"Falta de {formatar_reais(-centavos)}"


class Documento(FPDF):
    def __init__(self, loja: str, titulo: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.loja = loja
        self.titulo = titulo
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(auto=True, margin=18)
        self.set_title(limpar(titulo))
        self.set_creator("Caixa da Papelaria")
        self.add_page()

    def header(self) -> None:
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 7, limpar(self.loja), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 11)
        self.cell(0, 6, limpar(self.titulo), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(120, 120, 120)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("Helvetica", "", 8)
        rodape = (
            f"Documento gerencial interno - não é documento fiscal. "
            f"Emitido em {formatar_data_hora(agora_utc())}. Página {self.page_no()}/{{nb}}"
        )
        self.cell(0, 5, limpar(rodape), align="C")

    # ------------------------------------------------------------ blocos

    def secao(self, titulo: str) -> None:
        if self.get_y() > self.h - 45:
            self.add_page()
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 7, limpar(titulo), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def pares(self, linhas: Sequence[tuple[str, str]], largura_rotulo: float = 70) -> None:
        for rotulo, valor in linhas:
            self.set_font("Helvetica", "", 10)
            self.cell(largura_rotulo, ALTURA, limpar(rotulo))
            self.set_font("Helvetica", "B", 10)
            self.cell(0, ALTURA, limpar(valor), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def tabela(
        self,
        cabecalho: Sequence[str],
        larguras: Sequence[float],
        linhas: Sequence[Sequence[str]],
        alinhamentos: Sequence[str] | None = None,
        total: Sequence[str] | None = None,
    ) -> None:
        alinhamentos = alinhamentos or ["L"] * len(cabecalho)
        self._linha(cabecalho, larguras, alinhamentos, negrito=True, fundo=True)
        if not linhas:
            self.set_font("Helvetica", "I", 9)
            self.cell(
                sum(larguras),
                ALTURA,
                "Nenhum registro.",
                border=1,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        for linha in linhas:
            if self.get_y() + ALTURA > self.page_break_trigger:
                self.add_page()
                self._linha(cabecalho, larguras, alinhamentos, negrito=True, fundo=True)
            self._linha(linha, larguras, alinhamentos)
        if total:
            self._linha(total, larguras, alinhamentos, negrito=True)

    def _linha(
        self,
        valores: Sequence[str],
        larguras: Sequence[float],
        alinhamentos: Sequence[str],
        negrito: bool = False,
        fundo: bool = False,
    ) -> None:
        self.set_font("Helvetica", "B" if negrito else "", 9)
        self.set_fill_color(230, 233, 238)
        for valor, largura, alinhamento in zip(valores, larguras, alinhamentos, strict=True):
            texto = limpar(valor)
            # Corta texto longo para caber na coluna (o CSV traz o texto completo).
            while texto and self.get_string_width(texto) > largura - 2:
                texto = texto[:-2] + "."
            self.cell(largura, ALTURA, texto, border=1, align=alinhamento, fill=fundo)
        self.ln(ALTURA)


# ---------------------------------------------------------------- fechamento de caixa


def pdf_fechamento(dados: ResumoSessao, loja: str) -> bytes:
    s, r = dados.sessao, dados.resumo
    situacao = "Fechado" if not s.aberta else "ABERTO (resumo parcial)"
    doc = Documento(loja, f"Resumo do caixa #{s.id} - {situacao}")

    identificacao = [
        ("Aberto por", f"{s.usuario_abertura.nome} em {formatar_data_hora(s.aberta_em)}")
    ]
    if not s.aberta and s.usuario_fechamento:
        identificacao.append(
            ("Fechado por", f"{s.usuario_fechamento.nome} em {formatar_data_hora(s.fechada_em)}")
        )
    doc.pares(identificacao)

    if not s.aberta:
        doc.secao("Conferência da gaveta (dinheiro)")
        g = r.gaveta
        doc.pares(
            [
                ("Fundo de troco", formatar_reais(r.fundo_troco_centavos)),
                ("+ Entradas em dinheiro", formatar_reais(g.get(T.ENTRADA, 0))),
                (
                    "+ Recebimentos de fiado em dinheiro",
                    formatar_reais(g.get(T.RECEBIMENTO_FIADO, 0)),
                ),
                ("+ Suprimentos", formatar_reais(g.get(T.SUPRIMENTO, 0))),
                ("- Saídas em dinheiro", formatar_reais(g.get(T.SAIDA, 0))),
                ("- Sangrias", formatar_reais(g.get(T.SANGRIA, 0))),
                ("= Esperado na gaveta", formatar_reais(s.valor_esperado_centavos)),
                ("Contado", formatar_reais(s.valor_contado_centavos)),
                ("Diferença", _diferenca(s.diferenca_centavos)),
            ],
            largura_rotulo=80,
        )
        if s.justificativa:
            doc.set_font("Helvetica", "", 10)
            doc.multi_cell(0, ALTURA, limpar(f"Justificativa: {s.justificativa}"))

    doc.secao("Por forma de pagamento")
    doc.tabela(
        ["Forma", "Recebido", "Pago (despesas)"],
        [80, 50, 50],
        [
            [f.forma, formatar_reais(f.recebido_centavos), formatar_reais(f.pago_centavos)]
            for f in r.por_forma
        ],
        ["L", "R", "R"],
        ["Total", formatar_reais(r.faturamento_centavos), formatar_reais(r.despesas_centavos)],
    )

    doc.secao("Por tipo (já descontados os estornos)")
    tipos = (T.ENTRADA, T.RECEBIMENTO_FIADO, T.SAIDA, T.SANGRIA, T.SUPRIMENTO)
    doc.tabela(
        ["Tipo", "Total"],
        [80, 50],
        [[ROTULO_TIPO[t], formatar_reais(r.por_tipo.get(t, 0))] for t in tipos]
        + [["Estornos (quantidade)", str(r.estornos_qtd)]],
        ["L", "R"],
    )

    doc.secao("Lançamentos")
    doc.tabela(
        ["#", "Hora", "Tipo", "Descrição", "Pagamento", "Por", "Valor"],
        [10, 13, 34, 45, 32, 24, 22],
        [
            [
                str(m.id) + (" (E)" if m.id in dados.estornados else ""),
                formatar_data_hora(m.criado_em)[-5:],
                m.rotulo,
                m.descricao or (m.categoria.nome if m.categoria else ""),
                " + ".join(p.forma.nome for p in m.partes),
                m.usuario.nome,
                formatar_reais(m.valor_com_sinal),
            ]
            for m in dados.movimentos
        ],
        ["R", "C", "L", "L", "L", "L", "R"],
    )
    if dados.estornados:
        doc.set_font("Helvetica", "I", 8)
        doc.cell(0, 5, "(E) = lançamento estornado.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(doc.output())


# ---------------------------------------------------------------- relatório do período


def pdf_periodo(rel: RelatorioPeriodo, loja: str) -> bytes:
    doc = Documento(loja, f"Relatório do período {rel.periodo.rotulo}")
    t = rel.total

    doc.secao("Resumo")
    doc.pares(
        [
            ("Faturamento (entradas + fiado recebido)", formatar_reais(t.faturamento_centavos)),
            ("Despesas (saídas)", formatar_reais(t.despesas_centavos)),
            ("Resultado (faturamento - despesas)", formatar_reais(rel.resultado_centavos)),
            ("Sangrias", formatar_reais(t.por_tipo.get(T.SANGRIA, 0))),
            ("Suprimentos", formatar_reais(t.por_tipo.get(T.SUPRIMENTO, 0))),
            ("Vendas fiado (compras anotadas)", formatar_reais(rel.fiado.compras_centavos)),
            ("Fiado recebido", formatar_reais(rel.fiado.recebido_centavos)),
            (
                "Total a receber hoje (fiado)",
                formatar_reais(rel.a_receber.total_a_receber_centavos),
            ),
        ],
        largura_rotulo=95,
    )

    doc.secao("Por forma de pagamento")
    doc.tabela(
        ["Forma", "Recebido", "Pago (despesas)"],
        [80, 50, 50],
        [
            [f.forma, formatar_reais(f.recebido_centavos), formatar_reais(f.pago_centavos)]
            for f in t.por_forma
        ],
        ["L", "R", "R"],
        ["Total", formatar_reais(t.faturamento_centavos), formatar_reais(t.despesas_centavos)],
    )

    doc.secao("Fluxo por dia")
    doc.tabela(
        ["Data", "Entradas", "Fiado receb.", "Saídas", "Sangrias", "Suprim.", "Resultado"],
        [24, 26, 26, 26, 26, 26, 26],
        [
            [
                formatar_data(d.dia),
                formatar_reais(d.resumo.por_tipo.get(T.ENTRADA, 0)),
                formatar_reais(d.resumo.por_tipo.get(T.RECEBIMENTO_FIADO, 0)),
                formatar_reais(d.resumo.por_tipo.get(T.SAIDA, 0)),
                formatar_reais(d.resumo.por_tipo.get(T.SANGRIA, 0)),
                formatar_reais(d.resumo.por_tipo.get(T.SUPRIMENTO, 0)),
                formatar_reais(d.resultado_centavos),
            ]
            for d in rel.dias
        ],
        ["L", "R", "R", "R", "R", "R", "R"],
    )

    doc.secao("Despesas por categoria")
    doc.tabela(
        ["Categoria", "Total"],
        [100, 50],
        [[c.categoria, formatar_reais(c.total_centavos)] for c in rel.categorias],
        ["L", "R"],
    )

    doc.secao("Fechamentos de caixa")
    doc.tabela(
        ["#", "Fechado em", "Por", "Esperado", "Contado", "Diferença"],
        [12, 32, 36, 32, 32, 36],
        [
            [
                str(s.id),
                formatar_data_hora(s.fechada_em),
                s.usuario_fechamento.nome if s.usuario_fechamento else "",
                formatar_reais(s.valor_esperado_centavos),
                formatar_reais(s.valor_contado_centavos),
                _diferenca(s.diferenca_centavos),
            ]
            for s in rel.fechamentos
        ],
        ["R", "L", "L", "R", "R", "R"],
    )

    doc.secao("Diferenças por operador")
    doc.tabela(
        ["Operador", "Fechamentos", "Sobras", "Faltas", "Saldo"],
        [60, 28, 30, 30, 32],
        [
            [
                o.nome,
                str(o.fechamentos),
                formatar_reais(o.sobras_centavos),
                formatar_reais(o.faltas_centavos),
                formatar_reais(o.saldo_centavos),
            ]
            for o in rel.operadores
        ],
        ["L", "R", "R", "R", "R"],
    )
    return bytes(doc.output())
