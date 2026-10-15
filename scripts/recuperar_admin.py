"""Recuperação de acesso quando o ADMINISTRADOR esqueceu a senha.

Roda direto no computador da loja (exige acesso físico ao PC e à conta do Windows):
- lista os administradores;
- define uma senha provisória aleatória para o escolhido, desbloqueia e reativa;
- a troca de senha é obrigatória no próximo acesso;
- tudo fica registrado na auditoria.
Se não existir administrador ativo, permite promover um usuário existente.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core import auditoria
from app.core.config import obter_config
from app.core.database import criar_engine, criar_fabrica_sessao
from app.core.datas import agora_utc
from app.core.migracoes import migrar
from app.core.seguranca import gerar_hash_senha
from app.modules.auth import repository as auth_repo
from app.modules.auth.models import Perfil, Usuario

ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"  # sem letras/números que se confundem


def senha_provisoria() -> str:
    return "".join(secrets.choice(ALFABETO) for _ in range(10))


def escolher(usuarios: list[Usuario], entrada: str) -> Usuario | None:
    entrada = entrada.strip().lower()
    for usuario in usuarios:
        if entrada in (usuario.login, str(usuario.id)):
            return usuario
    return None


def redefinir(usuario: Usuario, promover: bool) -> str:
    senha = senha_provisoria()
    usuario.senha_hash = gerar_hash_senha(senha)
    usuario.trocar_senha = True
    usuario.falhas_login = 0
    usuario.bloqueado_ate = None
    usuario.ativo = True
    if promover:
        usuario.perfil = Perfil.ADMIN
    return senha


def principal(entrada: str | None = None) -> int:
    cfg = obter_config()
    engine = criar_engine(cfg)
    migrar(engine)
    db = criar_fabrica_sessao(engine)()
    try:
        admins = list(db.scalars(select(Usuario).where(Usuario.perfil == Perfil.ADMIN)))
        promover = not any(u.ativo for u in admins)
        candidatos = admins if not promover else auth_repo.listar(db)
        if not candidatos:
            print("Não há usuários. Abra o sistema: o primeiro acesso cria o administrador.")
            return 1
        print(
            "Nenhum administrador ativo. Escolha quem vai virar administrador:"
            if promover
            else "Administradores:"
        )
        for u in candidatos:
            situacao = "ativo" if u.ativo else "inativo"
            print(f"  [{u.id}] {u.login} - {u.nome} ({situacao})")
        resposta = entrada if entrada is not None else input("Digite o usuário (ou número): ")
        alvo = escolher(candidatos, resposta)
        if alvo is None:
            print("Usuário não encontrado. Nada foi alterado.")
            return 1
        senha = redefinir(alvo, promover)
        auth_repo.encerrar_sessoes_do_usuario(db, alvo.id, agora_utc())
        auditoria.registrar(
            db,
            acao="senha_recuperada_local",
            entidade="usuario",
            entidade_id=alvo.id,
            depois={"promovido_a_admin": promover},
        )
        db.commit()
    finally:
        db.close()
        engine.dispose()
    print()
    print(f"Senha provisória de '{alvo.login}':  {senha}")
    print("Entre com ela; o sistema vai pedir para criar uma senha nova.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
