"""Tela inicial: estado do caixa e botões do balcão."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.deps import obter_db, usuario_atual
from app.core.templates import renderizar
from app.modules.auth.models import Usuario
from app.modules.backup import service as backup_service
from app.modules.caixa import service as caixa_service
from app.modules.relatorios import service as relatorios_service

router = APIRouter()


@router.get("/")
def inicio(
    request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(obter_db)
) -> Response:
    sessao = caixa_service.sessao_aberta(db)
    contexto: dict[str, Any] = {
        "sessao": sessao,
        "caixa_atrasado": caixa_service.sessao_de_dia_anterior(sessao),
        "alerta_relogio": caixa_service.alerta_relogio(db),
        "alerta_backup": backup_service.alerta(db, request.app.state.config),
        "chave": str(uuid4()),
        # Resumo do dia (todas as sessões de hoje + fiado): só administrador.
        "dia": relatorios_service.resumo_do_dia(db, usuario) if usuario.eh_admin else None,
    }
    if sessao is not None:
        dados = caixa_service.obter_resumo(db, usuario, sessao.id)
        contexto["movimentos"] = list(reversed(dados.movimentos))
        contexto["estornados"] = dados.estornados
    return renderizar(request, "inicio.html", contexto)
