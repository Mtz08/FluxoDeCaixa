"""Etapa 5: configurações, registro de backups e campos opcionais do cliente.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core import gatilhos

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "configuracao",
        sa.Column("chave", sa.String(60), primary_key=True),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("atualizado_por", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "backup_registro",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gatilho", sa.String(20), nullable=False),
        sa.Column("arquivo", sa.String(200), nullable=True),
        sa.Column("destino", sa.String(500), nullable=True),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("erro", sa.String(500), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('ok','falha')", name="ck_backup_registro_status"),
        sa.CheckConstraint(
            "gatilho IN ('fechamento','diario','manual','pre_restauracao')",
            name="ck_backup_registro_gatilho",
        ),
    )
    op.create_index("ix_backup_registro_criado_em", "backup_registro", ["criado_em"])
    gatilhos.bloquear(
        "trg_backup_registro_sem_update",
        "BEFORE UPDATE",
        "backup_registro",
        "registro de backup nao pode ser alterado",
    )

    # Campos opcionais (desligados por padrão: minimização de dados, LGPD).
    op.add_column("cliente", sa.Column("cpf", sa.String(11), nullable=True))
    op.add_column("cliente", sa.Column("endereco", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cliente") as lote:
        lote.drop_column("endereco")
        lote.drop_column("cpf")
    gatilhos.remover("trg_backup_registro_sem_update", "backup_registro")
    op.drop_table("backup_registro")
    op.drop_table("configuracao")
