"""Telas do caixa: abrir, lançar (com confirmação), estornar, fechar e resumo."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.config import Config
from app.core.deps import ip_cliente, obter_cfg, obter_db, usuario_atual
from app.core.dinheiro import ValorInvalido, formatar_reais, ler_valor
from app.core.erros import ErroNegocio
from app.core.templates import redirecionar, renderizar
from app.modules.auth import service as auth_service
from app.modules.auth.models import Usuario
from app.modules.auth.schemas import primeira_mensagem
from app.modules.backup import service as backup_service
from app.modules.backup.models import Gatilho
from app.modules.caixa import repository as repo
from app.modules.caixa import service
from app.modules.caixa.models import ROTULO_TIPO, TipoMovimento
from app.modules.caixa.schemas import TIPOS_MANUAIS, NovoLancamento, Parte

router = APIRouter(prefix="/caixa")

TIPOS_POR_URL = {t.value: t for t in TIPOS_MANUAIS}
CAMPOS_INTERNOS = {"csrf_token", "etapa"}


def _ler_chave(texto: str | None) -> UUID:
    try:
        return UUID(str(texto))
    except ValueError as erro:
        raise ErroNegocio("Formulário inválido. Recarregue a página (F5).") from erro


def _valor(texto: str | None, rotulo: str = "o valor") -> int:
    try:
        return ler_valor(texto)
    except ValorInvalido as erro:
        raise ErroNegocio(f"Confira {rotulo}: {erro}") from erro


# ---------------------------------------------------------------- abrir


@router.post("/abrir")
def abrir(
    request: Request,
    fundo: str = Form(""),
    chave: str = Form(""),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        fundo_centavos = _valor(fundo or "0", "o fundo de troco")
        sessao = service.abrir_caixa(
            db, usuario, fundo_centavos, _ler_chave(chave), ip_cliente(request)
        )
    except ErroNegocio as erro:
        return redirecionar(request, "/", erro.mensagem, erro=True)
    aviso = f"Caixa aberto com fundo de troco de {formatar_reais(sessao.fundo_troco_centavos)}."
    return redirecionar(request, "/", aviso)


# ---------------------------------------------------------------- lançamentos


def _contexto_form(db: Session, tipo: TipoMovimento, valores: dict[str, Any]) -> dict[str, Any]:
    return {
        "tipo": tipo,
        "rotulo": ROTULO_TIPO[tipo],
        "formas": repo.formas_ativas(db),
        "categorias": repo.categorias_ativas(db),
        "valores": valores,
        "chave": valores.get("chave") or str(uuid4()),
    }


def _ler_lancamento(db: Session, tipo: TipoMovimento, form: FormData) -> NovoLancamento:
    partes: list[Parte] = []
    if tipo == TipoMovimento.ENTRADA:
        for forma in repo.formas_ativas(db):
            texto = str(form.get(f"parte_{forma.id}") or "").strip()
            if texto:
                valor = _valor(texto, f"o valor em {forma.nome}")
                if valor > 0:
                    partes.append(Parte(forma_id=forma.id, valor_centavos=valor))
        if not partes:
            raise ErroNegocio("Informe o valor em pelo menos uma forma de pagamento.")
    else:
        if tipo == TipoMovimento.SAIDA:
            forma_id = int(str(form.get("forma_id") or 0) or 0)
        else:
            dinheiro = repo.forma_dinheiro(db)
            if dinheiro is None:
                raise ErroNegocio("Não há forma de pagamento em dinheiro ativa.")
            forma_id = dinheiro.id
        valor = _valor(str(form.get("valor") or ""))
        if valor <= 0:
            raise ErroNegocio("O valor precisa ser maior que zero.")
        partes.append(Parte(forma_id=forma_id, valor_centavos=valor))

    categoria = str(form.get("categoria_id") or "")
    try:
        return NovoLancamento(
            tipo=tipo,
            partes=partes,
            categoria_id=int(categoria) if categoria.isdigit() else None,
            descricao=str(form.get("descricao") or ""),
            chave=_ler_chave(str(form.get("chave") or "")),
            confirmar_valor_alto=form.get("confirmar_valor_alto") is not None,
        )
    except ValidationError as erro:
        raise ErroNegocio(primeira_mensagem(erro)) from erro


def _tipo_da_url(tipo: str) -> TipoMovimento:
    if tipo not in TIPOS_POR_URL:
        raise ErroNegocio("Página não encontrada.")
    return TIPOS_POR_URL[tipo]


@router.get("/lancar/{tipo}")
def lancamento_form(
    request: Request,
    tipo: str,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    tipo_mov = _tipo_da_url(tipo)
    try:
        service.exigir_sessao_para_lancar(db)
    except ErroNegocio as erro:
        return redirecionar(request, "/", erro.mensagem, erro=True)
    return renderizar(request, "caixa/lancamento.html", _contexto_form(db, tipo_mov, {}))


@router.post("/lancar/{tipo}")
async def lancamento(
    request: Request,
    tipo: str,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    tipo_mov = _tipo_da_url(tipo)
    form = await request.form()
    valores = {k: v for k, v in form.items() if k not in CAMPOS_INTERNOS}
    etapa = form.get("etapa")

    if etapa == "corrigir":
        return renderizar(request, "caixa/lancamento.html", _contexto_form(db, tipo_mov, valores))
    try:
        dados = _ler_lancamento(db, tipo_mov, form)
        if etapa == "gravar":
            movimento, repetido = service.lancar(db, usuario, dados, ip_cliente(request))
            valor = formatar_reais(movimento.valor_centavos)
            aviso = f"Lançamento gravado: {ROTULO_TIPO[tipo_mov]} de {valor}."
            if repetido:
                aviso = "Este lançamento já tinha sido registrado (envio repetido ignorado)."
            return redirecionar(request, "/", aviso)
        previa = service.revisar_lancamento(db, dados)
    except ErroNegocio as erro:
        db.rollback()
        contexto = _contexto_form(db, tipo_mov, valores) | {"erro": erro.mensagem}
        return renderizar(request, "caixa/lancamento.html", contexto, status=400)

    contexto = {
        "previa": previa,
        "rotulo": ROTULO_TIPO[tipo_mov],
        "tipo": tipo_mov,
        "valores": valores,
    }
    return renderizar(request, "caixa/confirmar.html", contexto)


# ---------------------------------------------------------------- estorno


def _contexto_estorno(db: Session, movimento_id: int) -> dict[str, Any]:
    original = service.obter_movimento(db, movimento_id)
    atual = service.sessao_aberta(db)
    return {
        "original": original,
        "de_caixa_anterior": atual is None or original.sessao_id != atual.id,
        "chave": str(uuid4()),
    }


@router.get("/movimentos/{movimento_id}/estornar")
def estorno_form(
    request: Request,
    movimento_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    contexto = _contexto_estorno(db, movimento_id)
    service.validar_estornavel(db, contexto["original"])
    return renderizar(request, "caixa/estorno.html", contexto)


@router.post("/movimentos/{movimento_id}/estornar")
def estorno(
    request: Request,
    movimento_id: int,
    motivo: str = Form(""),
    chave: str = Form(""),
    admin_login: str = Form(""),
    admin_senha: str = Form(""),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    ip = ip_cliente(request)
    try:
        autorizador = None
        if not usuario.eh_admin:
            autorizador = auth_service.autorizar_admin(db, cfg, admin_login, admin_senha, ip)
        movimento, repetido = service.estornar(
            db, usuario, movimento_id, motivo, _ler_chave(chave), autorizador, ip
        )
    except ErroNegocio as erro:
        db.rollback()
        contexto = _contexto_estorno(db, movimento_id) | {
            "erro": erro.mensagem,
            "motivo": motivo,
            "chave": chave,
        }
        return renderizar(request, "caixa/estorno.html", contexto, status=400)
    aviso = f"Estorno de {formatar_reais(movimento.valor_centavos)} registrado."
    if repetido:
        aviso = "Este estorno já tinha sido registrado (envio repetido ignorado)."
    return redirecionar(request, "/", aviso)


# ---------------------------------------------------------------- fechamento cego


@router.get("/fechar")
def fechar_form(
    request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(obter_db)
) -> Response:
    sessao = service.sessao_aberta(db)
    if sessao is None:
        return redirecionar(request, "/", "Não há caixa aberto.", erro=True)
    return renderizar(request, "caixa/fechar.html", {"sessao": sessao})


@router.post("/fechar")
def fechar(
    request: Request,
    sessao_id: int = Form(...),
    contado: str = Form(""),
    justificativa: str = Form(""),
    etapa: str = Form("conferir"),
    tarefas: BackgroundTasks = BackgroundTasks(),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    ip = ip_cliente(request)
    sessao = repo.buscar_sessao(db, sessao_id)
    if sessao is not None and not sessao.aberta:
        return redirecionar(request, f"/caixa/sessoes/{sessao_id}", "Este caixa já está fechado.")
    try:
        contado_centavos = _valor(contado, "o valor contado")
        if etapa == "gravar":
            service.fechar_caixa(db, usuario, sessao_id, contado_centavos, justificativa, ip)
            # Backup automático ao fechar o caixa (depois da resposta; falha não afeta o caixa).
            tarefas.add_task(
                backup_service.fazer_backup,
                request.app.state.config,
                request.app.state.fabrica_sessao,
                Gatilho.FECHAMENTO,
                usuario.id,
            )
            return redirecionar(request, f"/caixa/sessoes/{sessao_id}", "Caixa fechado.")
        conferencia = service.conferir_fechamento(db, usuario, sessao_id, contado_centavos, ip)
    except ErroNegocio as erro:
        db.rollback()
        sessao = repo.buscar_sessao(db, sessao_id)
        if etapa == "gravar" and contado:
            # Volta para a tela de diferença, mantendo a contagem já feita.
            conferencia = service.calcular_conferencia(db, sessao_id, _valor(contado))
            contexto = {
                "conferencia": conferencia,
                "erro": erro.mensagem,
                "justificativa": justificativa,
            }
            return renderizar(request, "caixa/fechar_conferir.html", contexto, status=400)
        return renderizar(
            request, "caixa/fechar.html", {"sessao": sessao, "erro": erro.mensagem}, status=400
        )
    return renderizar(request, "caixa/fechar_conferir.html", {"conferencia": conferencia})


# ---------------------------------------------------------------- resumo


@router.get("/sessoes/{sessao_id}")
def resumo(
    request: Request,
    sessao_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    dados = service.obter_resumo(db, usuario, sessao_id)
    return renderizar(request, "caixa/resumo.html", {"r": dados, "rotulos": ROTULO_TIPO})
