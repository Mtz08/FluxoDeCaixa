"""Etapa 3: fiado (clientes e lançamentos da caderneta).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIPOS = "('compra','pagamento','abatimento','saldo_inicial','devolucao_credito','estorno')"
CATEGORIA_DEVOLUCAO = "Devolução de crédito a cliente (fiado)"


def upgrade() -> None:
    op.create_table(
        "cliente",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(80), nullable=False),
        sa.Column("apelido", sa.String(40), nullable=True),
        sa.Column("telefone", sa.String(11), nullable=True),
        sa.Column("observacao", sa.String(300), nullable=True),
        sa.Column("limite_centavos", sa.Integer(), nullable=True),
        sa.Column("dia_pagamento", sa.Integer(), nullable=True),
        sa.Column("saldo_centavos", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("busca", sa.String(200), nullable=False),
        sa.Column("consentimento_em", sa.DateTime(), nullable=False),
        sa.Column("anonimizado_em", sa.DateTime(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "limite_centavos IS NULL OR limite_centavos >= 0", name="ck_cliente_limite"
        ),
        sa.CheckConstraint(
            "dia_pagamento IS NULL OR dia_pagamento BETWEEN 1 AND 31", name="ck_cliente_dia"
        ),
        sa.CheckConstraint(
            "telefone IS NULL OR length(telefone) IN (10, 11)", name="ck_cliente_telefone"
        ),
        # Anonimizado só com saldo zerado (LGPD sem perder o histórico contábil).
        sa.CheckConstraint(
            "anonimizado_em IS NULL OR saldo_centavos = 0", name="ck_cliente_anonimizado"
        ),
    )
    op.create_index("ix_cliente_busca", "cliente", ["busca"])

    op.create_table(
        "fiado_lancamento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("cliente.id"), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("sinal", sa.Integer(), nullable=False),
        sa.Column("valor_centavos", sa.Integer(), nullable=False),
        sa.Column("descricao", sa.String(200), nullable=True),
        sa.Column("data_referencia", sa.Date(), nullable=False),
        sa.Column("sessao_caixa_id", sa.Integer(), sa.ForeignKey("sessao_caixa.id"), nullable=True),
        sa.Column(
            "movimento_caixa_id", sa.Integer(), sa.ForeignKey("movimento_caixa.id"), nullable=True
        ),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=False),
        sa.Column("autorizado_por", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column(
            "estorno_de_id", sa.Integer(), sa.ForeignKey("fiado_lancamento.id"), nullable=True
        ),
        sa.Column("chave_idempotencia", sa.String(64), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("chave_idempotencia", name="uq_fiado_lancamento_chave_idempotencia"),
        sa.UniqueConstraint("estorno_de_id", name="uq_fiado_lancamento_estorno_de_id"),
        sa.UniqueConstraint("movimento_caixa_id", name="uq_fiado_lancamento_movimento_caixa_id"),
        sa.CheckConstraint("valor_centavos > 0", name="ck_fiado_lancamento_valor"),
        sa.CheckConstraint(f"tipo IN {TIPOS}", name="ck_fiado_lancamento_tipo"),
        sa.CheckConstraint(
            "(tipo IN ('compra','saldo_inicial','devolucao_credito') AND sinal = 1)"
            " OR (tipo IN ('pagamento','abatimento') AND sinal = -1)"
            " OR (tipo = 'estorno' AND sinal IN (1, -1))",
            name="ck_fiado_lancamento_sinal",
        ),
        sa.CheckConstraint(
            "(tipo = 'estorno') = (estorno_de_id IS NOT NULL)", name="ck_fiado_lancamento_estorno"
        ),
        # Dinheiro que passa pelo caixa precisa do movimento correspondente.
        sa.CheckConstraint(
            "tipo NOT IN ('pagamento','devolucao_credito') OR movimento_caixa_id IS NOT NULL",
            name="ck_fiado_lancamento_movimento",
        ),
        sa.CheckConstraint(
            "tipo NOT IN ('compra','pagamento','devolucao_credito') OR sessao_caixa_id IS NOT NULL",
            name="ck_fiado_lancamento_sessao",
        ),
    )
    op.create_index("ix_fiado_lancamento_cliente", "fiado_lancamento", ["cliente_id"])
    op.create_index("ix_fiado_lancamento_criado_em", "fiado_lancamento", ["criado_em"])
    op.create_index("ix_fiado_lancamento_data", "fiado_lancamento", ["data_referencia"])

    for momento, verbo in (("UPDATE", "alterado"), ("DELETE", "apagado")):
        op.execute(
            f"CREATE TRIGGER trg_fiado_lancamento_sem_{momento.lower()} BEFORE {momento}"
            f" ON fiado_lancamento BEGIN SELECT RAISE(ABORT,"
            f" 'lancamento de fiado nao pode ser {verbo}'); END"
        )
    op.execute(
        "CREATE TRIGGER trg_cliente_sem_delete BEFORE DELETE ON cliente BEGIN"
        " SELECT RAISE(ABORT, 'cliente nao pode ser apagado; desative ou anonimize'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_fiado_lancamento_estorno_inverso BEFORE INSERT ON fiado_lancamento"
        " WHEN NEW.tipo = 'estorno' AND ("
        "(SELECT sinal FROM fiado_lancamento WHERE id = NEW.estorno_de_id) IS NOT -NEW.sinal"
        " OR (SELECT valor_centavos FROM fiado_lancamento WHERE id = NEW.estorno_de_id)"
        " IS NOT NEW.valor_centavos"
        " OR (SELECT cliente_id FROM fiado_lancamento WHERE id = NEW.estorno_de_id)"
        " IS NOT NEW.cliente_id"
        " OR (SELECT tipo FROM fiado_lancamento WHERE id = NEW.estorno_de_id) = 'estorno')"
        " BEGIN SELECT RAISE(ABORT, 'estorno deve ser o inverso exato do original'); END"
    )

    # Categoria usada pela saída de caixa da devolução de crédito ao cliente.
    op.execute(
        sa.text("INSERT INTO categoria_despesa (nome, ativo) VALUES (:nome, 1)").bindparams(
            nome=CATEGORIA_DEVOLUCAO
        )
    )


def downgrade() -> None:
    for nome in (
        "trg_fiado_lancamento_estorno_inverso",
        "trg_cliente_sem_delete",
        "trg_fiado_lancamento_sem_delete",
        "trg_fiado_lancamento_sem_update",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {nome}")
    op.drop_table("fiado_lancamento")
    op.drop_table("cliente")
    # A categoria fica (pode haver saídas no caixa apontando para ela).
