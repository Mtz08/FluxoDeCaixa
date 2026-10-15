"""Telas e downloads de relatórios (administrador) e PDF do fechamento de caixa."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core import auditoria
from app.core.config import Config
from app.core.deps import exigir_admin, ip_cliente, obter_cfg, obter_db, usuario_atual
from app.core.erros import ErroNegocio
from app.core.templates import redirecionar, renderizar
from app.modules.auth.models import Usuario
from app.modules.caixa import service as caixa
from app.modules.caixa.models import ROTULO_TIPO
from app.modules.relatorios import exportar_csv, exportar_pdf, service
from app.modules.relatorios.service import Periodo

router = APIRouter()


def _periodo(preset: str, inicio: str, fim: str) -> Periodo:
    return service.montar_periodo(preset, inicio, fim)


def _registrar_exportacao(
    db: Session, usuario: Usuario, nome: str, periodo: Periodo | None, ip: str | None
) -> None:
    auditoria.registrar(
        db,
        acao="relatorio_exportado",
        entidade="relatorio",
        usuario_id=usuario.id,
        depois={"relatorio": nome, "periodo": periodo.rotulo if periodo else None},
        ip=ip,
    )
    db.commit()


def _download(conteudo: str | bytes, tipo: str, arquivo: str) -> Response:
    return Response(
        conteudo,
        media_type=tipo,
        headers={"Content-Disposition": f'attachment; filename="{arquivo}"'},
    )


@router.get("/relatorios")
def relatorios(
    request: Request,
    preset: str = "hoje",
    inicio: str = "",
    fim: str = "",
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        periodo = _periodo(preset, inicio, fim)
    except ErroNegocio as erro:
        return redirecionar(request, "/relatorios", erro.mensagem, erro=True)
    rel = service.relatorio_periodo(db, admin, periodo)
    contexto = {
        "rel": rel,
        "rotulos": ROTULO_TIPO,
        "consulta": f"preset={periodo.preset}&inicio={periodo.inicio}&fim={periodo.fim}",
    }
    return renderizar(request, "relatorios/periodo.html", contexto)


@router.get("/relatorios/csv/{nome}")
def baixar_csv(
    request: Request,
    nome: str,
    preset: str = "hoje",
    inicio: str = "",
    fim: str = "",
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    exportador = exportar_csv.EXPORTADORES.get(nome)
    if exportador is None:
        raise ErroNegocio("Relatório não encontrado.")
    periodo = _periodo(preset, inicio, fim)
    conteudo = exportador(service.relatorio_periodo(db, admin, periodo))
    _registrar_exportacao(db, admin, f"csv:{nome}", periodo, ip_cliente(request))
    arquivo = f"{nome}_{periodo.inicio}_{periodo.fim}.csv"
    return _download(conteudo, "text/csv; charset=utf-8", arquivo)


@router.get("/relatorios/pdf")
def baixar_pdf(
    request: Request,
    preset: str = "hoje",
    inicio: str = "",
    fim: str = "",
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    periodo = _periodo(preset, inicio, fim)
    conteudo = exportar_pdf.pdf_periodo(
        service.relatorio_periodo(db, admin, periodo), cfg.nome_loja
    )
    _registrar_exportacao(db, admin, "pdf:periodo", periodo, ip_cliente(request))
    return _download(conteudo, "application/pdf", f"relatorio_{periodo.inicio}_{periodo.fim}.pdf")


@router.get("/caixa/sessoes/{sessao_id}/pdf")
def fechamento_pdf(
    request: Request,
    sessao_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    """Mesma permissão do resumo na tela (operador: o caixa que ele fechou hoje)."""
    dados = caixa.obter_resumo(db, usuario, sessao_id)
    conteudo = exportar_pdf.pdf_fechamento(dados, cfg.nome_loja)
    _registrar_exportacao(db, usuario, f"pdf:caixa:{sessao_id}", None, ip_cliente(request))
    return _download(conteudo, "application/pdf", f"caixa_{sessao_id}.pdf")
