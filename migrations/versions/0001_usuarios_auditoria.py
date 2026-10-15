"""Etapa 1: usuários, sessões de login e auditoria (só inserção).

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usuario",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(80), nullable=False),
        sa.Column("login", sa.String(40), nullable=False),
        sa.Column("senha_hash", sa.String(255), nullable=False),
        sa.Column("perfil", sa.String(20), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("trocar_senha", sa.Boolean(), nullable=False),
        sa.Column("falhas_login", sa.Integer(), nullable=False),
        sa.Column("bloqueado_ate", sa.DateTime(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint("perfil IN ('admin', 'operador')", name="ck_usuario_perfil"),
        sa.UniqueConstraint("login", name="uq_usuario_login"),
    )

    op.create_table(
        "sessao_login",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("ultimo_acesso", sa.DateTime(), nullable=False),
        sa.Column("encerrada_em", sa.DateTime(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_sessao_login_token_hash"),
    )
    op.create_index("ix_sessao_login_usuario", "sessao_login", ["usuario_id"])

    op.create_table(
        "auditoria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column("acao", sa.String(60), nullable=False),
        sa.Column("entidade", sa.String(60), nullable=False),
        sa.Column("entidade_id", sa.Integer(), nullable=True),
        sa.Column("antes_json", sa.Text(), nullable=True),
        sa.Column("depois_json", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_auditoria_criado_em", "auditoria", ["criado_em"])
    op.create_index("ix_auditoria_entidade", "auditoria", ["entidade", "entidade_id"])

    # Auditoria é só inserção: o próprio banco recusa alteração e exclusão.
    op.execute(
        """
        CREATE TRIGGER trg_auditoria_sem_update BEFORE UPDATE ON auditoria
        BEGIN SELECT RAISE(ABORT, 'auditoria nao pode ser alterada'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_auditoria_sem_delete BEFORE DELETE ON auditoria
        BEGIN SELECT RAISE(ABORT, 'auditoria nao pode ser apagada'); END
        """
    )
    # Usuário com histórico nunca é apagado: desativar (soft delete).
    op.execute(
        """
        CREATE TRIGGER trg_usuario_sem_delete BEFORE DELETE ON usuario
        BEGIN SELECT RAISE(ABORT, 'usuario nao pode ser apagado; desative'); END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_usuario_sem_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_auditoria_sem_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_auditoria_sem_update")
    op.drop_table("auditoria")
    op.drop_table("sessao_login")
    op.drop_table("usuario")
