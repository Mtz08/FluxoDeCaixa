"""Dados de exemplo para DESENVOLVIMENTO/TREINAMENTO. Nunca roda no banco da loja.

Uso:
    set CAIXA_MODO_DEV=1
    set CAIXA_DIR_DADOS=C:\\caixa-treino
    python scripts\\dados_exemplo.py
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import _dir_dados_padrao, obter_config
from app.core.database import criar_engine, criar_fabrica_sessao
from app.core.datas import hoje_local
from app.core.migracoes import migrar
from app.modules.auth import service as auth
from app.modules.auth.models import Perfil
from app.modules.auth.schemas import NovoUsuario
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa import service as caixa
from app.modules.caixa.models import TipoMovimento as T
from app.modules.caixa.schemas import NovoLancamento, Parte
from app.modules.fiado import service as fiado
from app.modules.fiado.schemas import DadosCliente, NovaCompra

SENHA = "treino123"


def principal() -> int:
    cfg = obter_config()
    if os.environ.get("CAIXA_MODO_DEV") != "1":
        print("Recusado: defina CAIXA_MODO_DEV=1 (dados de exemplo são só para treino).")
        return 1
    if cfg.dir_dados.resolve() == _dir_dados_padrao().resolve():
        print("Recusado: use uma pasta de treino em CAIXA_DIR_DADOS, nunca a pasta da loja.")
        return 1
    engine = criar_engine(cfg)
    migrar(engine)
    db = criar_fabrica_sessao(engine)()
    try:
        if auth.existe_usuario(db):
            print("Recusado: este banco já tem dados.")
            return 1
        admin = auth.criar_primeiro_admin(
            db,
            cfg,
            NovoUsuario(nome="Dona (treino)", login="dona", perfil=Perfil.ADMIN, senha=SENHA),
            SENHA,
            None,
        )
        operador = auth.criar_usuario(
            db,
            cfg,
            admin,
            NovoUsuario(
                nome="Balcão (treino)", login="balcao", perfil=Perfil.OPERADOR, senha=SENHA
            ),
            None,
        )
        operador.trocar_senha = False
        db.commit()
        formas = {f.nome: f.id for f in caixa_repo.formas_ativas(db)}
        categorias = {c.nome: c.id for c in caixa_repo.categorias_ativas(db)}

        caixa.abrir_caixa(db, operador, 10000, uuid4(), None)

        def lancar(tipo: T, partes: list[tuple[str, int]], **extra: object) -> None:
            dados = NovoLancamento(
                tipo=tipo,
                partes=[Parte(forma_id=formas[n], valor_centavos=v) for n, v in partes],
                chave=uuid4(),
                **extra,  # type: ignore[arg-type]
            )
            caixa.lancar(db, operador, dados, None)

        lancar(T.ENTRADA, [("Dinheiro", 1250)], descricao="xerox")
        lancar(T.ENTRADA, [("PIX", 4590), ("Dinheiro", 1000)], descricao="material escolar")
        lancar(T.ENTRADA, [("Cartão de débito", 8990)], descricao="mochila")
        lancar(T.SAIDA, [("Dinheiro", 1500)], categoria_id=categorias["Lanche"], descricao="café")

        for nome, apelido, valor, dias in (
            ("Sebastião Lima", "Tião", 4500, 45),
            ("Maria Souza", None, 1200, 5),
            ("Pedro Alves", "Pedrinho", 9800, 95),
        ):
            cliente = fiado.criar_cliente(
                db, admin, DadosCliente(nome=nome, apelido=apelido, consentimento=True), None
            )
            fiado.anotar_compra(
                db,
                operador,
                NovaCompra(
                    cliente_id=cliente.id,
                    valor_total_centavos=valor,
                    descricao="material",
                    data_referencia=hoje_local() - timedelta(days=dias),
                    chave=uuid4(),
                ),
                None,
                False,
                None,
            )
    finally:
        db.close()
        engine.dispose()
    print(f"OK: dados de treino criados em {cfg.dir_dados}")
    print(f"Usuários: dona / {SENHA} (administrador) e balcao / {SENHA} (operador)")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
