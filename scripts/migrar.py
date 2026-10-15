"""Cria/atualiza a estrutura do banco (o sistema também faz isso sozinho ao iniciar)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import obter_config
from app.core.database import criar_engine, verificar_integridade
from app.core.migracoes import migrar


def principal() -> int:
    cfg = obter_config()
    engine = criar_engine(cfg)
    try:
        problemas = verificar_integridade(engine)
        if problemas:
            print("ERRO: o banco está com defeito. Use scripts\\restaurar_backup.bat.")
            return 1
        migrar(engine)
    finally:
        engine.dispose()
    print(f"OK: banco pronto em {cfg.caminho_banco}")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
