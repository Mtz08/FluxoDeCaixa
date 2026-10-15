"""Gatilhos (triggers) de proteção, escritos uma vez e gerados para SQLite ou PostgreSQL.

Usados pelas migrações. Os dois bancos recusam a operação com a MESMA mensagem e
como erro de integridade (o sistema e os testes tratam igual).

Condições: use sintaxe comum aos dois (NEW/OLD, subconsultas, `IS DISTINCT FROM`).
"""

from __future__ import annotations

from alembic import op


def _dialeto() -> str:
    return str(op.get_bind().dialect.name)


def bloquear(nome: str, momento: str, tabela: str, mensagem: str, quando: str = "") -> None:
    """Cria um gatilho que RECUSA a operação (`momento`: 'BEFORE INSERT|UPDATE|DELETE').

    Sem `quando`, recusa sempre; com `quando`, recusa só se a condição for verdadeira.
    """
    if "'" in mensagem:
        raise ValueError("Mensagem do gatilho não pode ter aspas simples.")
    if _dialeto() == "sqlite":
        condicao = f"WHEN {quando}" if quando else ""
        op.execute(
            f"CREATE TRIGGER {nome} {momento} ON {tabela} {condicao} "
            f"BEGIN SELECT RAISE(ABORT, '{mensagem}'); END"
        )
        return
    # PostgreSQL: função PL/pgSQL + gatilho por linha. ERRCODE 23000 = violação de integridade.
    recusar = f"RAISE EXCEPTION '{mensagem}' USING ERRCODE = '23000';"
    retorno = "OLD" if "DELETE" in momento else "NEW"
    corpo = f"IF {quando} THEN {recusar} END IF; RETURN {retorno};" if quando else recusar
    op.execute(
        f"CREATE FUNCTION {nome}_fn() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN {corpo} END $$"
    )
    op.execute(
        f"CREATE TRIGGER {nome} {momento} ON {tabela} FOR EACH ROW EXECUTE FUNCTION {nome}_fn()"
    )


def remover(nome: str, tabela: str) -> None:
    if _dialeto() == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {nome}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {nome} ON {tabela}")
        op.execute(f"DROP FUNCTION IF EXISTS {nome}_fn()")
