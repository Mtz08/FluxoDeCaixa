"""Etapa 2: caixa (sessões, movimentações, formas de pagamento, categorias).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core import gatilhos

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIPOS = "('entrada','saida','sangria','suprimento','recebimento_fiado','estorno')"


def upgrade() -> None:
    forma = op.create_table(
        "forma_pagamento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(40), nullable=False),
        sa.Column("afeta_gaveta", sa.Boolean(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.UniqueConstraint("nome", name="uq_forma_pagamento_nome"),
    )
    categoria = op.create_table(
        "categoria_despesa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(60), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("nome", name="uq_categoria_despesa_nome"),
    )

    op.create_table(
        "sessao_caixa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("aberta_por", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=False),
        sa.Column("aberta_em", sa.DateTime(), nullable=False),
        sa.Column("fundo_troco_centavos", sa.Integer(), nullable=False),
        sa.Column("fechada_por", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column("fechada_em", sa.DateTime(), nullable=True),
        sa.Column("valor_contado_centavos", sa.Integer(), nullable=True),
        sa.Column("valor_esperado_centavos", sa.Integer(), nullable=True),
        sa.Column("diferenca_centavos", sa.Integer(), nullable=True),
        sa.Column("justificativa", sa.String(500), nullable=True),
        sa.Column("chave_idempotencia", sa.String(36), nullable=False),
        sa.UniqueConstraint("chave_idempotencia", name="uq_sessao_caixa_chave_idempotencia"),
        sa.CheckConstraint("status IN ('aberta','fechada')", name="ck_sessao_caixa_status"),
        sa.CheckConstraint("fundo_troco_centavos >= 0", name="ck_sessao_caixa_fundo"),
        sa.CheckConstraint(
            "valor_contado_centavos IS NULL OR valor_contado_centavos >= 0",
            name="ck_sessao_caixa_contado",
        ),
        sa.CheckConstraint(
            "status = 'aberta' OR (fechada_por IS NOT NULL AND fechada_em IS NOT NULL"
            " AND valor_contado_centavos IS NOT NULL AND valor_esperado_centavos IS NOT NULL"
            " AND diferenca_centavos = valor_contado_centavos - valor_esperado_centavos"
            " AND (diferenca_centavos = 0 OR length(trim(justificativa)) > 0))",
            name="ck_sessao_caixa_fechamento",
        ),
    )
    op.create_index("ix_sessao_caixa_aberta_em", "sessao_caixa", ["aberta_em"])
    # Só pode existir UM caixa aberto.
    op.execute(
        "CREATE UNIQUE INDEX ux_sessao_caixa_uma_aberta ON sessao_caixa(status) "
        "WHERE status = 'aberta'"
    )

    op.create_table(
        "movimento_caixa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sessao_id", sa.Integer(), sa.ForeignKey("sessao_caixa.id"), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("sinal", sa.Integer(), nullable=False),
        sa.Column("valor_centavos", sa.Integer(), nullable=False),
        sa.Column(
            "categoria_id", sa.Integer(), sa.ForeignKey("categoria_despesa.id"), nullable=True
        ),
        sa.Column("descricao", sa.String(200), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=False),
        sa.Column("autorizado_por", sa.Integer(), sa.ForeignKey("usuario.id"), nullable=True),
        sa.Column(
            "estorno_de_id", sa.Integer(), sa.ForeignKey("movimento_caixa.id"), nullable=True
        ),
        sa.Column("considera_gaveta", sa.Boolean(), nullable=False),
        sa.Column("origem", sa.String(10), nullable=False),
        sa.Column("origem_id", sa.Integer(), nullable=True),
        sa.Column("chave_idempotencia", sa.String(64), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("chave_idempotencia", name="uq_movimento_caixa_chave_idempotencia"),
        # Um lançamento só pode ser estornado uma vez.
        sa.UniqueConstraint("estorno_de_id", name="uq_movimento_caixa_estorno_de_id"),
        sa.CheckConstraint("valor_centavos > 0", name="ck_movimento_caixa_valor"),
        sa.CheckConstraint(f"tipo IN {TIPOS}", name="ck_movimento_caixa_tipo"),
        sa.CheckConstraint("origem IN ('manual','fiado','pdv')", name="ck_movimento_caixa_origem"),
        sa.CheckConstraint(
            "(tipo IN ('entrada','suprimento','recebimento_fiado') AND sinal = 1)"
            " OR (tipo IN ('saida','sangria') AND sinal = -1)"
            " OR (tipo = 'estorno' AND sinal IN (1, -1))",
            name="ck_movimento_caixa_sinal",
        ),
        sa.CheckConstraint(
            "(tipo = 'estorno') = (estorno_de_id IS NOT NULL)", name="ck_movimento_caixa_estorno"
        ),
        sa.CheckConstraint(
            "tipo <> 'saida' OR categoria_id IS NOT NULL", name="ck_movimento_caixa_categoria"
        ),
        sa.CheckConstraint(
            "considera_gaveta OR tipo = 'estorno'", name="ck_movimento_caixa_gaveta"
        ),
    )
    op.create_index("ix_movimento_caixa_sessao", "movimento_caixa", ["sessao_id"])
    op.create_index("ix_movimento_caixa_criado_em", "movimento_caixa", ["criado_em"])

    op.create_table(
        "movimento_pagamento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "movimento_id", sa.Integer(), sa.ForeignKey("movimento_caixa.id"), nullable=False
        ),
        sa.Column(
            "forma_pagamento_id",
            sa.Integer(),
            sa.ForeignKey("forma_pagamento.id"),
            nullable=False,
        ),
        sa.Column("valor_centavos", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "movimento_id", "forma_pagamento_id", name="uq_movimento_pagamento_forma"
        ),
        sa.CheckConstraint("valor_centavos > 0", name="ck_movimento_pagamento_valor"),
    )
    op.create_index("ix_movimento_pagamento_movimento", "movimento_pagamento", ["movimento_id"])

    _criar_triggers()

    op.bulk_insert(
        forma,
        [
            {"nome": "Dinheiro", "afeta_gaveta": True, "ativo": True, "ordem": 1},
            {"nome": "PIX", "afeta_gaveta": False, "ativo": True, "ordem": 2},
            {"nome": "Cartão de débito", "afeta_gaveta": False, "ativo": True, "ordem": 3},
            {"nome": "Cartão de crédito", "afeta_gaveta": False, "ativo": True, "ordem": 4},
        ],
    )
    op.bulk_insert(
        categoria,
        [{"nome": nome, "ativo": True} for nome in ("Fornecedor", "Limpeza", "Lanche", "Outros")],
    )


_trigger = gatilhos.bloquear  # gera a versão SQLite ou PostgreSQL

# (nome, tabela) de todos os gatilhos desta migração, para o downgrade.
GATILHOS = [
    ("trg_movimento_caixa_sem_update", "movimento_caixa"),
    ("trg_movimento_caixa_sem_delete", "movimento_caixa"),
    ("trg_movimento_pagamento_sem_update", "movimento_pagamento"),
    ("trg_movimento_pagamento_sem_delete", "movimento_pagamento"),
    ("trg_forma_pagamento_sem_delete", "forma_pagamento"),
    ("trg_categoria_despesa_sem_delete", "categoria_despesa"),
    ("trg_sessao_caixa_sem_delete", "sessao_caixa"),
    ("trg_sessao_caixa_so_fecha", "sessao_caixa"),
    ("trg_movimento_caixa_sessao_aberta", "movimento_caixa"),
    ("trg_movimento_pagamento_sessao_aberta", "movimento_pagamento"),
    ("trg_movimento_caixa_estorno_inverso", "movimento_caixa"),
]


def _criar_triggers() -> None:
    # Lançamentos financeiros são imutáveis: correção só por estorno.
    for tabela in ("movimento_caixa", "movimento_pagamento"):
        _trigger(f"trg_{tabela}_sem_update", "BEFORE UPDATE", tabela, f"{tabela} e imutavel")
        _trigger(f"trg_{tabela}_sem_delete", "BEFORE DELETE", tabela, f"{tabela} e imutavel")

    # Cadastros com histórico: só desativar.
    for tabela in ("forma_pagamento", "categoria_despesa", "sessao_caixa"):
        _trigger(
            f"trg_{tabela}_sem_delete", "BEFORE DELETE", tabela, f"{tabela} nao pode ser apagada"
        )

    # Sessão: única alteração permitida é aberta -> fechada, uma vez, sem mexer na abertura.
    _trigger(
        "trg_sessao_caixa_so_fecha",
        "BEFORE UPDATE",
        "sessao_caixa",
        "caixa so pode ser fechado uma vez e a abertura nao muda",
        "OLD.status <> 'aberta' OR NEW.status <> 'fechada'"
        " OR NEW.aberta_por IS DISTINCT FROM OLD.aberta_por OR NEW.aberta_em IS DISTINCT FROM OLD.aberta_em"
        " OR NEW.fundo_troco_centavos IS DISTINCT FROM OLD.fundo_troco_centavos"
        " OR NEW.chave_idempotencia IS DISTINCT FROM OLD.chave_idempotencia",
    )

    # Caixa fechado não aceita lançamentos.
    _trigger(
        "trg_movimento_caixa_sessao_aberta",
        "BEFORE INSERT",
        "movimento_caixa",
        "caixa fechado nao aceita lancamentos",
        "(SELECT status FROM sessao_caixa WHERE id = NEW.sessao_id) IS DISTINCT FROM 'aberta'",
    )
    _trigger(
        "trg_movimento_pagamento_sessao_aberta",
        "BEFORE INSERT",
        "movimento_pagamento",
        "caixa fechado nao aceita lancamentos",
        "(SELECT s.status FROM movimento_caixa m JOIN sessao_caixa s ON s.id = m.sessao_id"
        " WHERE m.id = NEW.movimento_id) IS DISTINCT FROM 'aberta'",
    )

    # Estorno é o inverso exato do original, e não se estorna um estorno.
    _trigger(
        "trg_movimento_caixa_estorno_inverso",
        "BEFORE INSERT",
        "movimento_caixa",
        "estorno deve ser o inverso exato do original",
        "NEW.tipo = 'estorno' AND ("
        "(SELECT sinal FROM movimento_caixa WHERE id = NEW.estorno_de_id) IS DISTINCT FROM -NEW.sinal"
        " OR (SELECT valor_centavos FROM movimento_caixa WHERE id = NEW.estorno_de_id)"
        " IS DISTINCT FROM NEW.valor_centavos"
        " OR (SELECT tipo FROM movimento_caixa WHERE id = NEW.estorno_de_id) = 'estorno')",
    )


def downgrade() -> None:
    for nome, tabela in GATILHOS:
        gatilhos.remover(nome, tabela)
    op.drop_table("movimento_pagamento")
    op.drop_table("movimento_caixa")
    op.execute("DROP INDEX IF EXISTS ux_sessao_caixa_uma_aberta")
    op.drop_table("sessao_caixa")
    op.drop_table("categoria_despesa")
    op.drop_table("forma_pagamento")
