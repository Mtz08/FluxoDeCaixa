"""Backup manual e restauração (administrador)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import Config
from app.core.deps import exigir_admin, ip_cliente, obter_cfg, obter_db
from app.core.erros import ErroNegocio
from app.core.seguranca import COOKIE_SESSAO
from app.core.templates import opcoes_cookie, redirecionar, renderizar
from app.modules.auth import service as auth_service
from app.modules.auth.models import Usuario
from app.modules.backup import service
from app.modules.backup.models import Gatilho

router = APIRouter(prefix="/backup")
TAMANHO_MAXIMO = 200 * 1024 * 1024  # 200 MB


@router.post("/agora")
def backup_agora(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    db.close()  # não segurar o banco enquanto a cópia é feita
    app = request.app
    registros = service.fazer_backup(
        app.state.config, app.state.fabrica_sessao, Gatilho.MANUAL, admin.id
    )
    falhas = [r for r in registros if not r.ok]
    if not registros or falhas:
        motivo = falhas[0].erro if falhas else "erro inesperado (veja o log)"
        return redirecionar(
            request, "/configuracoes#backup", f"Backup com problema: {motivo}", erro=True
        )
    return redirecionar(
        request, "/configuracoes#backup", f"Backup feito em {len(registros)} pasta(s)."
    )


@router.get("/baixar")
def baixar(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        nome, conteudo = service.gerar_para_download(
            request.app.state.config, db, admin, ip_cliente(request)
        )
    except ErroNegocio as e:
        return redirecionar(request, "/configuracoes#backup", e.mensagem, erro=True)
    return Response(
        conteudo,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.get("/restaurar")
def restaurar_form(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    arquivos = service.listar_arquivos(db, request.app.state.config)
    return renderizar(request, "backup/restaurar.html", {"arquivos": arquivos})


@router.post("/restaurar")
async def restaurar(
    request: Request,
    origem: str = Form(""),
    arquivo: UploadFile | None = None,
    senha_backup: str = Form(""),
    senha_admin: str = Form(""),
    confirmacao: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    ip = ip_cliente(request)
    base: Config = request.app.state.config
    try:
        # Confirmação dupla: digitar RESTAURAR e a senha do administrador.
        if confirmacao.strip().upper() != "RESTAURAR":
            raise ErroNegocio("Para restaurar, digite RESTAURAR no campo de confirmação.")
        auth_service.autorizar_admin(db, cfg, admin.login, senha_admin, ip)
        if origem:
            conteudo = service.arquivo_da_lista(db, base, origem).read_bytes()
        elif arquivo is not None and arquivo.filename:
            conteudo = await arquivo.read(TAMANHO_MAXIMO + 1)
            if len(conteudo) > TAMANHO_MAXIMO:
                raise ErroNegocio("Arquivo grande demais para ser um backup do sistema.")
        else:
            raise ErroNegocio("Escolha um backup da lista ou envie um arquivo .cxbk.")
        db.close()  # libera o banco antes de substituí-lo
        app = request.app
        copia = service.restaurar(
            base,
            app.state.engine,
            app.state.fabrica_sessao,
            conteudo,
            senha_backup or None,
            admin,
            ip,
        )
    except ErroNegocio as e:
        db.rollback()
        arquivos = service.listar_arquivos(db, base)
        contexto = {"arquivos": arquivos, "erro": e.mensagem}
        return renderizar(request, "backup/restaurar.html", contexto, status=400)
    resposta = redirecionar(
        request,
        "/login",
        f"Backup restaurado. Entre novamente. Cópia do estado anterior: {copia.name}",
    )
    resposta.delete_cookie(COOKIE_SESSAO, **opcoes_cookie(request))
    return resposta
