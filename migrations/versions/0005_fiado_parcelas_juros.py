"""Fiado parcelado com juros por atraso: tabela de parcelas e lançamento do tipo "juros".

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core import gatilhos

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIPOS_ANTES = "('compra','pagamento','abatimento','saldo_inicial','devolucao_credito','estorno')"
TIPOS_DEPOIS = (
    "('compra','pagamento','abatimento','saldo_inicial','devolucao_credito','juros','estorno')"
)
SINAL_ANTES = (
    "(tipo IN ('compra','saldo_inicial','devolucao_credito') AND sinal = 1)"
    " OR (tipo IN ('pagamento','abatimento') AND sinal = -1)"
    " OR (tipo = 'estorno' AND sinal IN (1, -1))"
)
SINAL_DEPOIS = (
    "(tipo IN ('compra','saldo_inicial','devolucao_credito','juros') AND sinal = 1)"
    " OR (tipo IN ('pagamento','abatimento') AND sinal = -1)"
    " OR (tipo = 'estorno' AND sinal IN (1, -1))"
)

GATILHOS_LANCAMENTO = (
    "trg_fiado_lancamento_sem_update",
    "trg_fiado_lancamento_sem_delete",
    "trg_fiado_lancamento_estorno_inverso",
)


def _criar_gatilhos_lancamento() -> None:
    for momento, verbo in (("UPDATE", "alterado"), ("DELETE", "apagado")):
        gatilhos.bloquear(
            f"trg_fiado_lancamento_sem_{momento.lower()}",
            f"BEFORE {momento}",
            "fiado_lancamento",
            f"lancamento de fiado nao pode ser {verbo}",
        )
    gatilhos.bloquear(
        "trg_fiado_lancamento_estorno_inverso",
        "BEFORE INSERT",
        "fiado_lancamento",
        "estorno deve ser o inverso exato do original",
        "NEW.tipo = 'estorno' AND ("
        "(SELECT sinal FROM fiado_lancamento WHERE id = NEW.estorno_de_id)"
        " IS DISTINCT FROM -NEW.sinal"
        " OR (SELECT valor_centavos FROM fiado_lancamento WHERE id = NEW.estorno_de_id)"
        " IS DISTINCT FROM NEW.valor_centavos"
        " OR (SELECT cliente_id FROM fiado_lancamento WHERE id = NEW.estorno_de_id)"
        " IS DISTINCT FROM NEW.cliente_id"
        " OR (SELECT tipo FROM fiado_lancamento WHERE id = NEW.estorno_de_id) = 'estorno')",
    )


def _trocar_restricoes(tipos: str, sinal: str) -> None:
    # No SQLite a tabela é recriada (os gatilhos somem): remove antes e recria depois.
    for nome in GATILHOS_LANCAMENTO:
        gatilhos.remover(nome, "fiado_lancamento")
    # Nomes passam pela mesma convenção das migrações anteriores (iguais nos dois bancos).
    with op.batch_alter_table("fiado_lancamento", recreate="auto") as lote:
        lote.drop_constraint("ck_fiado_lancamento_tipo", type_="check")
        lote.drop_constraint("ck_fiado_lancamento_sinal", type_="check")
        lote.create_check_constraint("ck_fiado_lancamento_tipo", f"tipo IN {tipos}")
        lote.create_check_constraint("ck_fiado_lancamento_sinal", sinal)
    _criar_gatilhos_lancamento()


def upgrade() -> None:
    _trocar_restricoes(TIPOS_DEPOIS, SINAL_DEPOIS)

    op.create_table(
        "fiado_parcela",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("compra_id", sa.Integer(), sa.ForeignKey("fiado_lancamento.id"), nullable=False),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("cliente.id"), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("vencimento", sa.Date(), nullable=False),
        sa.Column("valor_centavos", sa.Integer(), nullable=False),
        sa.Column("taxa_juros_ppm", sa.Integer(), nullable=False),
        sa.Column("carencia_dias", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("compra_id", "numero", name="uq_fiado_parcela_compra_numero"),
        sa.CheckConstraint("valor_centavos > 0", name="ck_fiado_parcela_valor"),
        sa.CheckConstraint(
            "numero BETWEEN 1 AND total AND total <= 24", name="ck_fiado_parcela_numero"
        ),
        sa.CheckConstraint(
            "taxa_juros_ppm BETWEEN 0 AND 10000", name="ck_fiado_parcela_taxa"
        ),  # até 1% ao dia
        sa.CheckConstraint("carencia_dias BETWEEN 0 AND 60", name="ck_fiado_parcela_carencia"),
    )
    op.create_index("ix_fiado_parcela_cliente", "fiado_parcela", ["cliente_id"])
    op.create_index("ix_fiado_parcela_vencimento", "fiado_parcela", ["vencimento"])
    # Parcela é o "contrato" da compra: não se altera nem se apaga (estorne a compra).
    gatilhos.bloquear(
        "trg_fiado_parcela_sem_update",
        "BEFORE UPDATE",
        "fiado_parcela",
        "parcela nao pode ser alterada",
    )
    gatilhos.bloquear(
        "trg_fiado_parcela_sem_delete",
        "BEFORE DELETE",
        "fiado_parcela",
        "parcela nao pode ser apagada",
    )


def downgrade() -> None:
    gatilhos.remover("trg_fiado_parcela_sem_delete", "fiado_parcela")
    gatilhos.remover("trg_fiado_parcela_sem_update", "fiado_parcela")
    op.drop_table("fiado_parcela")
    _trocar_restricoes(TIPOS_ANTES, SINAL_ANTES)
