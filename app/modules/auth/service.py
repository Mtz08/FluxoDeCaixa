"""Regras de autenticação, sessões e gestão de usuários.

Cada função pública é uma operação completa: termina com commit() ou
levanta exceção (e nada é gravado).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core import auditoria
from app.core.config import Config
from app.core.datas import agora_utc
from app.core.erros import ErroNegocio, SemPermissao
from app.core.seguranca import conferir_senha, gerar_hash_senha, gerar_token, hash_token
from app.modules.auth import repository as repo
from app.modules.auth.models import Perfil, SessaoLogin, Usuario
from app.modules.auth.schemas import EdicaoUsuario, NovoUsuario

MSG_LOGIN_INVALIDO = "Usuário ou senha incorretos."
# Evita gravar no banco a cada clique: atualiza o "último acesso" no máximo 1x/min.
_INTERVALO_TOQUE = timedelta(seconds=60)


# ---------------------------------------------------------------- senhas


def validar_nova_senha(cfg: Config, senha: str, confirmacao: str, login: str) -> None:
    if senha != confirmacao:
        raise ErroNegocio("As duas senhas digitadas não são iguais.")
    if len(senha) < cfg.senha_tamanho_minimo:
        raise ErroNegocio(f"A senha precisa ter pelo menos {cfg.senha_tamanho_minimo} caracteres.")
    if senha.strip().lower() == login.lower():
        raise ErroNegocio("A senha não pode ser igual ao nome de usuário.")


# ---------------------------------------------------------------- primeiro acesso


def existe_usuario(db: Session) -> bool:
    return repo.contar_usuarios(db) > 0


def criar_primeiro_admin(
    db: Session, cfg: Config, dados: NovoUsuario, confirmacao: str, ip: str | None
) -> Usuario:
    if existe_usuario(db):
        raise ErroNegocio("O administrador já foi criado. Entre com usuário e senha.")
    validar_nova_senha(cfg, dados.senha, confirmacao, dados.login)
    usuario = _novo_usuario(dados.model_copy(update={"perfil": Perfil.ADMIN}), trocar_senha=False)
    db.add(usuario)
    db.flush()
    auditoria.registrar(
        db,
        acao="primeiro_admin",
        entidade="usuario",
        entidade_id=usuario.id,
        usuario_id=usuario.id,
        depois=usuario.resumo(),
        ip=ip,
    )
    db.commit()
    return usuario


# ---------------------------------------------------------------- login


def autenticar(db: Session, cfg: Config, login: str, senha: str, ip: str | None) -> Usuario:
    """Confere usuário e senha, aplicando o bloqueio após várias tentativas."""
    agora = agora_utc()
    usuario = repo.buscar_por_login(db, login.strip().lower())

    if usuario is None or not usuario.ativo:
        conferir_senha(senha, None)  # mesmo tempo de resposta
        raise ErroNegocio(MSG_LOGIN_INVALIDO)

    if usuario.bloqueado_ate and usuario.bloqueado_ate > agora:
        minutos = int((usuario.bloqueado_ate - agora).total_seconds() // 60) + 1
        raise ErroNegocio(
            f"Muitas tentativas erradas. Tente de novo em {minutos} minuto(s) "
            "ou peça ao administrador para redefinir a senha."
        )

    if not conferir_senha(senha, usuario.senha_hash):
        _registrar_falha(db, cfg, usuario, ip)
        raise ErroNegocio(MSG_LOGIN_INVALIDO)

    usuario.falhas_login = 0
    usuario.bloqueado_ate = None
    auditoria.registrar(
        db, acao="login", entidade="usuario", entidade_id=usuario.id, usuario_id=usuario.id, ip=ip
    )
    db.commit()
    return usuario


def _registrar_falha(db: Session, cfg: Config, usuario: Usuario, ip: str | None) -> None:
    usuario.falhas_login += 1
    acao = "login_falhou"
    if usuario.falhas_login >= cfg.max_tentativas_login:
        usuario.bloqueado_ate = agora_utc() + timedelta(minutes=cfg.bloqueio_minutos)
        usuario.falhas_login = 0
        acao = "login_bloqueado"
    auditoria.registrar(
        db, acao=acao, entidade="usuario", entidade_id=usuario.id, usuario_id=usuario.id, ip=ip
    )
    db.commit()


def autorizar_admin(db: Session, cfg: Config, login: str, senha: str, ip: str | None) -> Usuario:
    """Senha do administrador digitada na hora (estorno, limite de crédito...)."""
    try:
        usuario = autenticar(db, cfg, login, senha, ip)
    except ErroNegocio as erro:
        raise ErroNegocio(f"Autorização negada: {erro.mensagem}") from erro
    if not usuario.eh_admin:
        raise SemPermissao("Autorização negada: este usuário não é administrador.")
    return usuario


# ---------------------------------------------------------------- sessões


def iniciar_sessao(db: Session, usuario: Usuario, ip: str | None) -> str:
    """Cria a sessão e devolve o token (vai só no cookie; no banco fica o hash)."""
    token = gerar_token()
    agora = agora_utc()
    db.add(
        SessaoLogin(
            usuario_id=usuario.id,
            token_hash=hash_token(token),
            criado_em=agora,
            ultimo_acesso=agora,
            ip=ip,
        )
    )
    db.commit()
    return token


def usuario_da_sessao(db: Session, cfg: Config, token: str | None) -> Usuario | None:
    """Usuário dono do token, ou None se a sessão não existe/expirou/usuário inativo."""
    if not token:
        return None
    sessao = repo.buscar_sessao(db, hash_token(token))
    if sessao is None:
        return None
    agora = agora_utc()
    usuario = repo.buscar_por_id(db, sessao.usuario_id)
    expirou = agora - sessao.ultimo_acesso > timedelta(minutes=cfg.sessao_minutos)
    if expirou or usuario is None or not usuario.ativo:
        sessao.encerrada_em = agora
        db.commit()
        return None
    if agora - sessao.ultimo_acesso > _INTERVALO_TOQUE:
        sessao.ultimo_acesso = agora
        db.commit()
    return usuario


def encerrar_sessao(db: Session, token: str | None, ip: str | None) -> None:
    if not token:
        return
    sessao = repo.buscar_sessao(db, hash_token(token))
    if sessao is None:
        return
    sessao.encerrada_em = agora_utc()
    auditoria.registrar(
        db,
        acao="logout",
        entidade="usuario",
        entidade_id=sessao.usuario_id,
        usuario_id=sessao.usuario_id,
        ip=ip,
    )
    db.commit()


# ---------------------------------------------------------------- senha do próprio usuário


def trocar_propria_senha(
    db: Session,
    cfg: Config,
    usuario: Usuario,
    atual: str,
    nova: str,
    confirmacao: str,
    ip: str | None,
) -> None:
    if not conferir_senha(atual, usuario.senha_hash):
        raise ErroNegocio("A senha atual está incorreta.")
    if atual == nova:
        raise ErroNegocio("A nova senha precisa ser diferente da atual.")
    validar_nova_senha(cfg, nova, confirmacao, usuario.login)
    usuario.senha_hash = gerar_hash_senha(nova)
    usuario.trocar_senha = False
    auditoria.registrar(
        db,
        acao="senha_trocada",
        entidade="usuario",
        entidade_id=usuario.id,
        usuario_id=usuario.id,
        ip=ip,
    )
    db.commit()


# ---------------------------------------------------------------- gestão (admin)


def _exigir_admin(ator: Usuario) -> None:
    if not ator.eh_admin:
        raise SemPermissao()


def listar_usuarios(db: Session, ator: Usuario) -> list[Usuario]:
    _exigir_admin(ator)
    return repo.listar(db)


def obter_usuario(db: Session, ator: Usuario, usuario_id: int) -> Usuario:
    _exigir_admin(ator)
    usuario = repo.buscar_por_id(db, usuario_id)
    if usuario is None:
        raise ErroNegocio("Usuário não encontrado.")
    return usuario


def criar_usuario(
    db: Session, cfg: Config, ator: Usuario, dados: NovoUsuario, ip: str | None
) -> Usuario:
    """Cria com senha provisória: o funcionário troca no primeiro login."""
    _exigir_admin(ator)
    if repo.buscar_por_login(db, dados.login):
        raise ErroNegocio("Já existe um usuário com esse nome de login. Escolha outro.")
    validar_nova_senha(cfg, dados.senha, dados.senha, dados.login)
    usuario = _novo_usuario(dados, trocar_senha=True)
    db.add(usuario)
    db.flush()
    auditoria.registrar(
        db,
        acao="usuario_criado",
        entidade="usuario",
        entidade_id=usuario.id,
        usuario_id=ator.id,
        depois=usuario.resumo(),
        ip=ip,
    )
    db.commit()
    return usuario


def editar_usuario(
    db: Session, ator: Usuario, usuario_id: int, dados: EdicaoUsuario, ip: str | None
) -> Usuario:
    usuario = obter_usuario(db, ator, usuario_id)
    antes = usuario.resumo()
    perde_admin = (
        usuario.eh_admin and usuario.ativo and (dados.perfil != Perfil.ADMIN or not dados.ativo)
    )
    if perde_admin and repo.contar_admins_ativos(db) <= 1:
        raise ErroNegocio("Precisa existir pelo menos um administrador ativo.")
    if usuario.id == ator.id and not dados.ativo:
        raise ErroNegocio("Você não pode desativar o seu próprio usuário.")

    usuario.nome = dados.nome
    usuario.perfil = dados.perfil
    usuario.ativo = dados.ativo
    if not dados.ativo:
        repo.encerrar_sessoes_do_usuario(db, usuario.id, agora_utc())
    auditoria.registrar(
        db,
        acao="usuario_editado",
        entidade="usuario",
        entidade_id=usuario.id,
        usuario_id=ator.id,
        antes=antes,
        depois=usuario.resumo(),
        ip=ip,
    )
    db.commit()
    return usuario


def redefinir_senha(
    db: Session, cfg: Config, ator: Usuario, usuario_id: int, nova: str, ip: str | None
) -> Usuario:
    """Admin define senha provisória; desbloqueia e derruba as sessões abertas."""
    usuario = obter_usuario(db, ator, usuario_id)
    validar_nova_senha(cfg, nova, nova, usuario.login)
    usuario.senha_hash = gerar_hash_senha(nova)
    usuario.trocar_senha = True
    usuario.falhas_login = 0
    usuario.bloqueado_ate = None
    repo.encerrar_sessoes_do_usuario(db, usuario.id, agora_utc())
    auditoria.registrar(
        db,
        acao="senha_redefinida",
        entidade="usuario",
        entidade_id=usuario.id,
        usuario_id=ator.id,
        ip=ip,
    )
    db.commit()
    return usuario


def _novo_usuario(dados: NovoUsuario, *, trocar_senha: bool) -> Usuario:
    return Usuario(
        nome=dados.nome,
        login=dados.login,
        senha_hash=gerar_hash_senha(dados.senha),
        perfil=dados.perfil,
        ativo=True,
        trocar_senha=trocar_senha,
        falhas_login=0,
        criado_em=agora_utc(),
    )
