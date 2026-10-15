"""Tela de configurações (administrador) e verificação de integridade dos dados."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session

from app.core import auditoria
from app.core.config import Config
from app.core.database import verificar_integridade
from app.core.deps import exigir_admin, ip_cliente, obter_cfg, obter_db
from app.core.erros import ErroNegocio
from app.core.templates import redirecionar, renderizar
from app.modules.auth import service as auth_service
from app.modules.auth.models import Usuario
from app.modules.backup import cripto
from app.modules.backup import service as backup
from app.modules.caixa import service as caixa
from app.modules.configuracao import service
from app.modules.fiado import service as fiado

router = APIRouter(prefix="/configuracoes")


def _contexto(db: Session, cfg: Config, request: Request) -> dict[str, Any]:
    base: Config = request.app.state.config
    urls = service.enderecos_locais(base.porta)
    remotos = service.enderecos_remotos(base.porta)
    return {
        "prefs": service.carregar(db, base),
        "formas": service.listar_formas(db),
        "categorias": service.listar_categorias(db),
        "backup": backup.situacao(db, base),
        "urls": urls,
        "qr": service.qr_code(urls[0]) if urls else None,
        "remotos": remotos,
        "qr_remoto": service.qr_code(remotos[0]) if remotos else None,
        "pasta_dados": str(base.dir_dados),
    }


@router.get("")
def pagina(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    return renderizar(request, "configuracao/pagina.html", _contexto(db, cfg, request))


def _voltar(request: Request, aviso: str, ancora: str, erro: bool = False) -> Response:
    return redirecionar(request, f"/configuracoes#{ancora}", aviso, erro=erro)


@router.post("/gerais")
def gerais(
    request: Request,
    nome_loja: str = Form(""),
    sessao_minutos: str = Form("30"),
    campos_extras: str | None = Form(None),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        minutos = int(sessao_minutos) if sessao_minutos.strip().isdigit() else 0
        service.salvar_gerais(
            db, admin, nome_loja, minutos, campos_extras is not None, ip_cliente(request)
        )
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "gerais", erro=True)
    return _voltar(request, "Configurações salvas.", "gerais")


@router.post("/formas")
def nova_forma(
    request: Request,
    nome: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        forma = service.criar_forma(db, admin, nome, ip_cliente(request))
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "formas", erro=True)
    return _voltar(request, f"Forma de pagamento '{forma.nome}' criada.", "formas")


@router.post("/formas/{forma_id}/alternar")
def alternar_forma(
    request: Request,
    forma_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        forma = service.alternar_forma(db, admin, forma_id, ip_cliente(request))
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "formas", erro=True)
    estado = "ativada" if forma.ativo else "desativada"
    return _voltar(request, f"Forma '{forma.nome}' {estado}.", "formas")


@router.post("/categorias")
def nova_categoria(
    request: Request,
    nome: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        categoria = service.criar_categoria(db, admin, nome, ip_cliente(request))
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "categorias", erro=True)
    return _voltar(request, f"Categoria '{categoria.nome}' criada.", "categorias")


@router.post("/categorias/{categoria_id}/alternar")
def alternar_categoria(
    request: Request,
    categoria_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        categoria = service.alternar_categoria(db, admin, categoria_id, ip_cliente(request))
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "categorias", erro=True)
    estado = "ativada" if categoria.ativo else "desativada"
    return _voltar(request, f"Categoria '{categoria.nome}' {estado}.", "categorias")


@router.post("/backup")
def salvar_backup(
    request: Request,
    pastas: str = Form(""),
    hora: str = Form("21:00"),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    try:
        service.salvar_backup(db, admin, pastas, hora, ip_cliente(request))
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "backup", erro=True)
    return _voltar(request, "Configuração de backup salva.", "backup")


@router.post("/backup/senha")
def senha_backup(
    request: Request,
    nova: str = Form(""),
    confirmacao: str = Form(""),
    senha_admin: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    try:
        # Trocar a senha do backup exige confirmar a senha do próprio administrador.
        auth_service.autorizar_admin(db, cfg, admin.login, senha_admin, ip_cliente(request))
        if nova != confirmacao:
            raise ErroNegocio("As duas senhas do backup não são iguais.")
        cripto.guardar_senha(request.app.state.config.dir_dados, nova)
    except ValueError as e:
        return _voltar(request, str(e), "backup", erro=True)
    except ErroNegocio as e:
        return _voltar(request, e.mensagem, "backup", erro=True)
    auditoria.registrar(
        db,
        acao="senha_backup_definida",
        entidade="backup",
        usuario_id=admin.id,
        ip=ip_cliente(request),
    )
    db.commit()
    aviso = "Senha do backup definida. ANOTE-A EM PAPEL: sem ela os backups não abrem."
    return _voltar(request, aviso, "backup")


@router.post("/verificar")
def verificar(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    """Recalcula tudo a partir dos lançamentos e confere a integridade do banco."""
    problemas = caixa.verificar_consistencia(db) + fiado.verificar_saldos(db)
    # Libera a transação desta requisição antes de abrir outra conexão (senão uma espera a outra).
    db.rollback()
    problemas = verificar_integridade(request.app.state.engine) + problemas
    contexto = _contexto(db, cfg, request) | {"verificacao": problemas, "verificado": True}
    return renderizar(request, "configuracao/pagina.html", contexto)
