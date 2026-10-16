"""Zerar o caixa depois de um TESTE (uso único, pelo script scripts/zerar_caixa.py).

Os lançamentos são imutáveis e o banco recusa apagar (gatilhos). Esta é a única exceção:
dentro de UMA transação, as proteções das tabelas do caixa e do fiado são desligadas,
os dados são apagados e as proteções voltam. Se algo falhar, nada muda.

O que sai: todos os caixas (abertura, entradas, saídas, fechamento) e os lançamentos de
fiado ligados a eles (toda compra, pagamento e devolução passa pelo caixa), com suas
parcelas, juros e estornos. Ficam: usuários, clientes, formas de pagamento, categorias,
configurações e a auditoria (que ganha um registro da limpeza).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import BindParameter, bindparam, text
from sqlalchemy.orm import Session

from app.core import auditoria

TABELAS_PROTEGIDAS = (
    "movimento_pagamento",
    "movimento_caixa",
    "sessao_caixa",
    "fiado_parcela",
    "fiado_lancamento",
)


@dataclass(frozen=True)
class SessaoResumo:
    id: int
    aberta_em: datetime
    status: str
    movimentos: int


@dataclass
class Levantamento:
    sessoes: list[SessaoResumo] = field(default_factory=list)
    movimentos: int = 0
    partes: int = 0
    fiado_ligado: set[int] = field(default_factory=set)
    parcelas: int = 0
    # Lançamentos de fiado sem caixa (saldo do caderno, abatimento): (cliente_id, id, tipo, valor ±)
    fiado_sem_caixa: list[tuple[int, int, str, int]] = field(default_factory=list)

    @property
    def vazio(self) -> bool:
        return not self.sessoes and not self.fiado_ligado


def _ids_fiado_do_caixa(db: Session) -> tuple[set[int], list[tuple[int, int, str, int]]]:
    linhas = db.execute(
        text(
            "SELECT id, cliente_id, tipo, sinal * valor_centavos AS valor, estorno_de_id,"
            " sessao_caixa_id, movimento_caixa_id FROM fiado_lancamento"
        )
    ).all()
    apagar = {
        r.id
        for r in linhas
        if r.sessao_caixa_id is not None or r.movimento_caixa_id is not None or r.tipo == "juros"
    }
    # Estorno e original saem juntos (senão um estorno solto mudaria o saldo).
    mudou = True
    while mudou:
        mudou = False
        for r in linhas:
            if r.estorno_de_id is None:
                continue
            if (r.id in apagar) != (r.estorno_de_id in apagar):
                apagar |= {r.id, r.estorno_de_id}
                mudou = True
    restantes = [(r.cliente_id, r.id, r.tipo, r.valor) for r in linhas if r.id not in apagar]
    return apagar, restantes


def levantar(db: Session) -> Levantamento:
    sessoes = db.execute(
        text(
            "SELECT s.id, s.aberta_em, s.status,"
            " (SELECT COUNT(*) FROM movimento_caixa m WHERE m.sessao_id = s.id) AS qtd"
            " FROM sessao_caixa s ORDER BY s.id"
        )
    ).all()
    ligado, restantes = _ids_fiado_do_caixa(db)
    parcelas = 0
    if ligado:
        parcelas = int(
            db.scalar(
                text("SELECT COUNT(*) FROM fiado_parcela WHERE compra_id IN :ids").bindparams(
                    bindparam("ids", list(ligado), expanding=True)
                )
            )
            or 0
        )
    return Levantamento(
        sessoes=[SessaoResumo(s.id, _data_hora(s.aberta_em), s.status, s.qtd) for s in sessoes],
        movimentos=int(db.scalar(text("SELECT COUNT(*) FROM movimento_caixa")) or 0),
        partes=int(db.scalar(text("SELECT COUNT(*) FROM movimento_pagamento")) or 0),
        fiado_ligado=ligado,
        parcelas=parcelas,
        fiado_sem_caixa=restantes,
    )


def _data_hora(valor: object) -> datetime:
    """SQL puro devolve texto no SQLite; o horário gravado é UTC."""
    momento = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor))
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def contar_gatilhos(db: Session) -> int:
    if db.get_bind().dialect.name == "sqlite":
        consulta = "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name IN :t"
    else:
        consulta = (
            "SELECT COUNT(*) FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid"
            " WHERE NOT g.tgisinternal AND g.tgenabled <> 'D' AND c.relname IN :t"
        )
    parametro: BindParameter[list[str]] = bindparam("t", list(TABELAS_PROTEGIDAS), expanding=True)
    return int(db.scalar(text(consulta).bindparams(parametro)) or 0)


@contextmanager
def _sem_protecoes(db: Session) -> Iterator[None]:
    """Desliga os gatilhos das tabelas do caixa/fiado DENTRO da transação corrente."""
    conexao = db.connection()
    if conexao.dialect.name == "sqlite":
        # SQLite não desliga gatilho: guarda o SQL original, apaga e recria (DDL é transacional).
        gatilhos = conexao.execute(
            text(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name IN :t"
            ).bindparams(bindparam("t", list(TABELAS_PROTEGIDAS), expanding=True))
        ).all()
        for nome, _ in gatilhos:
            conexao.exec_driver_sql(f'DROP TRIGGER "{nome}"')
        yield
        for _, sql in gatilhos:
            conexao.exec_driver_sql(sql)
        return
    for tabela in TABELAS_PROTEGIDAS:
        conexao.exec_driver_sql(f"ALTER TABLE {tabela} DISABLE TRIGGER USER")
    yield
    for tabela in TABELAS_PROTEGIDAS:
        conexao.exec_driver_sql(f"ALTER TABLE {tabela} ENABLE TRIGGER USER")


def zerar(db: Session, apagar_fiado_sem_caixa: bool = False) -> Levantamento:
    """Apaga o caixa (e o fiado ligado a ele) e confirma. Devolve o que foi apagado."""
    antes = contar_gatilhos(db)
    feito = levantar(db)
    ids = set(feito.fiado_ligado)
    if apagar_fiado_sem_caixa:
        ids |= {lanc_id for _, lanc_id, _, _ in feito.fiado_sem_caixa}
    try:
        with _sem_protecoes(db):
            if ids:
                lista: BindParameter[list[int]] = bindparam("ids", list(ids), expanding=True)
                db.execute(
                    text("DELETE FROM fiado_parcela WHERE compra_id IN :ids").bindparams(lista)
                )
                db.execute(text("DELETE FROM fiado_lancamento WHERE id IN :ids").bindparams(lista))
            db.execute(text("DELETE FROM movimento_pagamento"))
            db.execute(text("DELETE FROM movimento_caixa"))
            db.execute(text("DELETE FROM sessao_caixa"))
        # Saldo de cada cliente = soma do que sobrou (o cache tem de bater com os lançamentos).
        db.execute(
            text(
                "UPDATE cliente SET saldo_centavos = COALESCE(("
                "SELECT SUM(l.sinal * l.valor_centavos) FROM fiado_lancamento l"
                " WHERE l.cliente_id = cliente.id), 0)"
            )
        )
        if contar_gatilhos(db) != antes:
            raise RuntimeError("As proteções do banco não voltaram; nada foi apagado.")
        auditoria.registrar(
            db,
            acao="caixa_zerado",
            entidade="sessao_caixa",
            depois={
                "caixas": len(feito.sessoes),
                "movimentos": feito.movimentos,
                "lancamentos_fiado": len(ids),
                "parcelas": feito.parcelas,
                "motivo": "limpeza de dados de teste (scripts/zerar_caixa.py)",
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    feito.fiado_ligado = ids
    return feito
