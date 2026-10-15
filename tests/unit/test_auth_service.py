from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auditoria import Auditoria
from app.core.config import Config
from app.core.datas import agora_utc
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth import service
from app.modules.auth.models import Perfil, SessaoLogin, Usuario
from app.modules.auth.schemas import EdicaoUsuario, NovoUsuario
from tests.conftest import SENHA_ADMIN, SENHA_OPERADOR


def _acoes(db: Session) -> list[str]:
    return list(db.scalars(select(Auditoria.acao).order_by(Auditoria.id)))


# ---------------------------------------------------------------- primeiro acesso


def test_primeiro_admin_so_pode_ser_criado_uma_vez(
    db: Session, cfg: Config, admin: Usuario
) -> None:
    assert admin.eh_admin and not admin.trocar_senha
    dados = NovoUsuario(nome="Outra", login="outra", perfil=Perfil.ADMIN, senha="qualquer1")
    with pytest.raises(ErroNegocio, match="já foi criado"):
        service.criar_primeiro_admin(db, cfg, dados, "qualquer1", None)


def test_primeiro_admin_sempre_e_admin_mesmo_se_pedir_operador(db: Session, cfg: Config) -> None:
    dados = NovoUsuario(nome="Dona", login="dona", perfil=Perfil.OPERADOR, senha="segredo1")
    usuario = service.criar_primeiro_admin(db, cfg, dados, "segredo1", None)
    assert usuario.perfil == Perfil.ADMIN


@pytest.mark.parametrize(
    ("senha", "confirmacao", "trecho"),
    [
        ("abc", "abc", "pelo menos"),
        ("segredo1", "segredo2", "não são iguais"),
        ("DONA12", "DONA12", "igual ao nome"),
    ],
)
def test_senha_fraca_e_recusada(
    db: Session, cfg: Config, senha: str, confirmacao: str, trecho: str
) -> None:
    dados = NovoUsuario(nome="Dona", login="dona12", perfil=Perfil.ADMIN, senha=senha)
    with pytest.raises(ErroNegocio, match=trecho):
        service.criar_primeiro_admin(db, cfg, dados, confirmacao, None)
    assert not service.existe_usuario(db)


# ---------------------------------------------------------------- login e bloqueio


def test_login_correto(db: Session, cfg: Config, admin: Usuario) -> None:
    assert service.autenticar(db, cfg, " MARIA ", SENHA_ADMIN, None).id == admin.id
    assert "login" in _acoes(db)


def test_login_com_senha_errada_ou_usuario_inexistente_tem_mesma_mensagem(
    db: Session, cfg: Config, admin: Usuario
) -> None:
    with pytest.raises(ErroNegocio) as e1:
        service.autenticar(db, cfg, "maria", "errada", None)
    with pytest.raises(ErroNegocio) as e2:
        service.autenticar(db, cfg, "ninguem", "errada", None)
    assert e1.value.mensagem == e2.value.mensagem == service.MSG_LOGIN_INVALIDO


def test_bloqueia_apos_5_tentativas_e_libera_depois(
    db: Session, cfg: Config, admin: Usuario
) -> None:
    for _ in range(cfg.max_tentativas_login):
        with pytest.raises(ErroNegocio):
            service.autenticar(db, cfg, "maria", "errada", None)
    # Mesmo com a senha certa, fica bloqueado.
    with pytest.raises(ErroNegocio, match="Muitas tentativas"):
        service.autenticar(db, cfg, "maria", SENHA_ADMIN, None)
    assert "login_bloqueado" in _acoes(db)

    # Passado o tempo de bloqueio, entra normalmente.
    admin.bloqueado_ate = agora_utc() - timedelta(seconds=1)
    db.commit()
    assert service.autenticar(db, cfg, "maria", SENHA_ADMIN, None).id == admin.id


def test_usuario_inativo_nao_entra(
    db: Session, cfg: Config, admin: Usuario, operador: Usuario
) -> None:
    service.editar_usuario(
        db,
        admin,
        operador.id,
        EdicaoUsuario(nome="João", perfil=Perfil.OPERADOR, ativo=False),
        None,
    )
    with pytest.raises(ErroNegocio, match="incorretos"):
        service.autenticar(db, cfg, "joao", SENHA_OPERADOR, None)


# ---------------------------------------------------------------- sessões


def test_sessao_valida_e_expiracao_por_inatividade(
    db: Session, cfg: Config, admin: Usuario
) -> None:
    token = service.iniciar_sessao(db, admin, None)
    assert service.usuario_da_sessao(db, cfg, token).id == admin.id  # type: ignore[union-attr]

    sessao = db.scalar(select(SessaoLogin))
    assert sessao is not None and sessao.token_hash != token  # banco guarda só o hash
    sessao.ultimo_acesso = agora_utc() - timedelta(minutes=cfg.sessao_minutos + 1)
    db.commit()
    assert service.usuario_da_sessao(db, cfg, token) is None
    assert service.usuario_da_sessao(db, cfg, token) is None  # continua encerrada


def test_sessao_ativa_renova_ultimo_acesso(db: Session, cfg: Config, admin: Usuario) -> None:
    token = service.iniciar_sessao(db, admin, None)
    sessao = db.scalar(select(SessaoLogin))
    assert sessao is not None
    antigo = agora_utc() - timedelta(minutes=5)
    sessao.ultimo_acesso = antigo
    db.commit()
    service.usuario_da_sessao(db, cfg, token)
    assert sessao.ultimo_acesso > antigo


def test_token_invalido_ou_vazio(db: Session, cfg: Config, admin: Usuario) -> None:
    assert service.usuario_da_sessao(db, cfg, None) is None
    assert service.usuario_da_sessao(db, cfg, "inventado") is None


def test_sair_encerra_sessao(db: Session, cfg: Config, admin: Usuario) -> None:
    token = service.iniciar_sessao(db, admin, None)
    service.encerrar_sessao(db, token, None)
    service.encerrar_sessao(db, token, None)  # segunda vez não quebra
    service.encerrar_sessao(db, None, None)
    assert service.usuario_da_sessao(db, cfg, token) is None


# ---------------------------------------------------------------- troca de senha


def test_usuario_criado_pelo_admin_precisa_trocar_senha(
    db: Session, cfg: Config, admin: Usuario
) -> None:
    dados = NovoUsuario(nome="Ana", login="ana", perfil=Perfil.OPERADOR, senha="provisoria1")
    ana = service.criar_usuario(db, cfg, admin, dados, None)
    assert ana.trocar_senha

    with pytest.raises(ErroNegocio, match="atual está incorreta"):
        service.trocar_propria_senha(db, cfg, ana, "errada", "nova-senha", "nova-senha", None)
    with pytest.raises(ErroNegocio, match="diferente da atual"):
        service.trocar_propria_senha(
            db, cfg, ana, "provisoria1", "provisoria1", "provisoria1", None
        )

    service.trocar_propria_senha(db, cfg, ana, "provisoria1", "nova-senha", "nova-senha", None)
    assert not ana.trocar_senha
    assert service.autenticar(db, cfg, "ana", "nova-senha", None).id == ana.id


def test_login_duplicado_e_recusado(db: Session, cfg: Config, admin: Usuario) -> None:
    dados = NovoUsuario(
        nome="Outra Maria", login="maria", perfil=Perfil.OPERADOR, senha="provisoria1"
    )
    with pytest.raises(ErroNegocio, match="Já existe"):
        service.criar_usuario(db, cfg, admin, dados, None)


# ---------------------------------------------------------------- permissões


def test_operador_nao_gerencia_usuarios(db: Session, cfg: Config, operador: Usuario) -> None:
    dados = NovoUsuario(nome="Xavier", login="xxx", perfil=Perfil.ADMIN, senha="provisoria1")
    with pytest.raises(SemPermissao):
        service.criar_usuario(db, cfg, operador, dados, None)
    with pytest.raises(SemPermissao):
        service.listar_usuarios(db, operador)
    with pytest.raises(SemPermissao):
        service.redefinir_senha(db, cfg, operador, operador.id, "outra-senha", None)


def test_sempre_sobra_um_admin_ativo(db: Session, admin: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="pelo menos um administrador"):
        service.editar_usuario(
            db,
            admin,
            admin.id,
            EdicaoUsuario(nome="Dona", perfil=Perfil.OPERADOR, ativo=True),
            None,
        )
    db.refresh(admin)
    assert admin.eh_admin


def test_admin_nao_desativa_a_si_mesmo(db: Session, cfg: Config, admin: Usuario) -> None:
    dados = NovoUsuario(nome="Sócia", login="socia", perfil=Perfil.ADMIN, senha="provisoria1")
    service.criar_usuario(db, cfg, admin, dados, None)
    with pytest.raises(ErroNegocio, match="próprio usuário"):
        service.editar_usuario(
            db, admin, admin.id, EdicaoUsuario(nome="Dona", perfil=Perfil.ADMIN, ativo=False), None
        )


def test_desativar_derruba_sessoes(
    db: Session, cfg: Config, admin: Usuario, operador: Usuario
) -> None:
    token = service.iniciar_sessao(db, operador, None)
    service.editar_usuario(
        db,
        admin,
        operador.id,
        EdicaoUsuario(nome="João", perfil=Perfil.OPERADOR, ativo=False),
        None,
    )
    assert service.usuario_da_sessao(db, cfg, token) is None


def test_editar_usuario_inexistente(db: Session, admin: Usuario) -> None:
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.obter_usuario(db, admin, 9999)


def test_redefinir_senha_desbloqueia_e_obriga_troca(
    db: Session, cfg: Config, admin: Usuario, operador: Usuario
) -> None:
    token = service.iniciar_sessao(db, operador, None)
    operador.bloqueado_ate = agora_utc() + timedelta(hours=1)
    db.commit()
    service.redefinir_senha(db, cfg, admin, operador.id, "temporaria1", None)
    assert operador.trocar_senha and operador.bloqueado_ate is None
    assert service.usuario_da_sessao(db, cfg, token) is None
    assert service.autenticar(db, cfg, "joao", "temporaria1", None).id == operador.id


# ---------------------------------------------------------------- autorização do admin na hora


def test_autorizar_admin(db: Session, cfg: Config, admin: Usuario, operador: Usuario) -> None:
    assert service.autorizar_admin(db, cfg, "maria", SENHA_ADMIN, None).id == admin.id
    with pytest.raises(SemPermissao, match="não é administrador"):
        service.autorizar_admin(db, cfg, "joao", SENHA_OPERADOR, None)
    with pytest.raises(ErroNegocio, match="Autorização negada"):
        service.autorizar_admin(db, cfg, "maria", "errada", None)


# ---------------------------------------------------------------- integridade no banco


def test_auditoria_nao_pode_ser_alterada_nem_apagada(db: Session, admin: Usuario) -> None:
    for sql in ("UPDATE auditoria SET acao = 'x'", "DELETE FROM auditoria"):
        with pytest.raises(IntegrityError, match="auditoria nao pode"):
            db.execute(text(sql))
        db.rollback()
    assert len(_acoes(db)) >= 1


def test_usuario_nao_pode_ser_apagado(db: Session, admin: Usuario) -> None:
    with pytest.raises(IntegrityError, match="desative"):
        db.execute(text("DELETE FROM usuario"))
    db.rollback()


def test_auditoria_nunca_grava_senha(db: Session, admin: Usuario) -> None:
    textos = " ".join(
        (a.depois_json or "") + (a.antes_json or "") for a in db.scalars(select(Auditoria))
    )
    assert SENHA_ADMIN not in textos and "senha_hash" not in textos
