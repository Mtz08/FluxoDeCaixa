"""Cópia completa dos dados entre dois bancos do sistema (SQLite <-> PostgreSQL).

Usada para gerar o backup a partir do PostgreSQL (o arquivo .cxbk é sempre um SQLite)
e para restaurar um backup no PostgreSQL.

Os gatilhos de proteção continuam LIGADOS no destino: cada caixa é inserido aberto,
recebe seus lançamentos e só então é fechado — a mesma ordem em que aconteceu. Assim
nada precisa de permissão especial nem de "desligar" proteções.

Pré-requisito: o destino está com a estrutura atual (migrações) e sem dados, exceto os
cadastros iniciais criados pelas migrações (formas de pagamento e categorias).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, Table, func, select, text

# Importa todos os modelos para o metadata conhecer todas as tabelas.
from app.core import auditoria as _auditoria  # noqa: F401
from app.core.database import Base
from app.modules.auth import models as _auth  # noqa: F401
from app.modules.backup import models as _backup  # noqa: F401
from app.modules.caixa import models as _caixa  # noqa: F401
from app.modules.configuracao import models as _configuracao  # noqa: F401
from app.modules.fiado import models as _fiado  # noqa: F401

# Ordem respeita as chaves estrangeiras. sessao_caixa/movimentos têm tratamento próprio.
ANTES_DO_CAIXA = ("usuario", "sessao_login", "configuracao")
CADASTROS_INICIAIS = ("forma_pagamento", "categoria_despesa")  # já existem no destino
DEPOIS_DO_CAIXA = (
    "cliente",
    "fiado_lancamento",
    "fiado_parcela",
    "auditoria",
    "backup_registro",
)
CAMPOS_DE_FECHAMENTO = (
    "fechada_por",
    "fechada_em",
    "valor_contado_centavos",
    "valor_esperado_centavos",
    "diferenca_centavos",
    "justificativa",
)


def _tabela(nome: str) -> Table:
    return Base.metadata.tables[nome]


def _linhas(origem: Connection, tabela: Table) -> list[dict[str, Any]]:
    chave = tabela.c.id if "id" in tabela.c else next(iter(tabela.primary_key.columns))
    return [dict(linha._mapping) for linha in origem.execute(select(tabela).order_by(chave))]


def _inserir(destino: Connection, tabela: Table, linhas: list[dict[str, Any]]) -> None:
    if linhas:
        destino.execute(tabela.insert(), linhas)


def _cadastros_iniciais(origem: Connection, destino: Connection, nome: str) -> None:
    """Formas e categorias: atualiza as que as migrações já criaram, insere as demais."""
    tabela = _tabela(nome)
    existentes = set(destino.scalars(select(tabela.c.id)))
    for linha in _linhas(origem, tabela):
        if linha["id"] in existentes:
            valores = {k: v for k, v in linha.items() if k != "id"}
            destino.execute(tabela.update().where(tabela.c.id == linha["id"]).values(**valores))
        else:
            _inserir(destino, tabela, [linha])


def _caixas(origem: Connection, destino: Connection) -> None:
    """Refaz cada caixa na ordem: abre, lança os movimentos, fecha (se estava fechado)."""
    sessoes, movimentos, pagamentos = (
        _tabela("sessao_caixa"),
        _tabela("movimento_caixa"),
        _tabela("movimento_pagamento"),
    )
    pagamentos_por_movimento: dict[int, list[dict[str, Any]]] = {}
    for pagamento in _linhas(origem, pagamentos):
        pagamentos_por_movimento.setdefault(pagamento["movimento_id"], []).append(pagamento)
    movimentos_por_sessao: dict[int, list[dict[str, Any]]] = {}
    for movimento in _linhas(origem, movimentos):
        movimentos_por_sessao.setdefault(movimento["sessao_id"], []).append(movimento)

    for sessao in _linhas(origem, sessoes):
        aberta = dict(sessao, status="aberta", **dict.fromkeys(CAMPOS_DE_FECHAMENTO))
        _inserir(destino, sessoes, [aberta])
        for movimento in movimentos_por_sessao.get(sessao["id"], []):
            _inserir(destino, movimentos, [movimento])
            _inserir(destino, pagamentos, pagamentos_por_movimento.get(movimento["id"], []))
        if sessao["status"] != "aberta":
            fechamento = {campo: sessao[campo] for campo in (*CAMPOS_DE_FECHAMENTO, "status")}
            destino.execute(
                sessoes.update().where(sessoes.c.id == sessao["id"]).values(**fechamento)
            )


def _acertar_sequencias(destino: Connection) -> None:
    """PostgreSQL: o próximo id gerado continua depois do maior id copiado."""
    if destino.dialect.name != "postgresql":
        return
    for tabela in Base.metadata.sorted_tables:
        if "id" not in tabela.c:
            continue
        maior = destino.scalar(select(func.max(tabela.c.id)))
        if maior is not None:
            destino.execute(
                text("SELECT setval(pg_get_serial_sequence(:tabela, 'id'), :valor)"),
                {"tabela": tabela.name, "valor": maior},
            )


def transferir(origem: Connection, destino: Connection) -> dict[str, int]:
    """Copia todos os dados de `origem` para `destino` (na transação de `destino`).

    Devolve a quantidade de linhas por tabela (para conferência).
    """
    for nome in ANTES_DO_CAIXA:
        _inserir(destino, _tabela(nome), _linhas(origem, _tabela(nome)))
    for nome in CADASTROS_INICIAIS:
        _cadastros_iniciais(origem, destino, nome)
    _caixas(origem, destino)
    for nome in DEPOIS_DO_CAIXA:
        _inserir(destino, _tabela(nome), _linhas(origem, _tabela(nome)))
    _acertar_sequencias(destino)
    return contar(destino)


def contar(conexao: Connection) -> dict[str, int]:
    return {
        tabela.name: int(conexao.scalar(select(func.count()).select_from(tabela)) or 0)
        for tabela in Base.metadata.sorted_tables
        if tabela.name != "alembic_version"
    }
