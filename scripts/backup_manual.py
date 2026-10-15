"""Faz um backup agora, sem abrir o navegador (mesmas regras do botão do sistema)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import obter_config
from app.core.database import criar_engine, criar_fabrica_sessao
from app.core.migracoes import migrar
from app.modules.backup import service
from app.modules.backup.models import Gatilho


def principal() -> int:
    cfg = obter_config()
    engine = criar_engine(cfg)
    try:
        migrar(engine)
        registros = service.fazer_backup(cfg, criar_fabrica_sessao(engine), Gatilho.MANUAL, None)
    finally:
        engine.dispose()
    ok = True
    for r in registros:
        if r.ok:
            print(f"OK: {r.arquivo} em {r.destino}")
        else:
            ok = False
            print(f"FALHA{' em ' + r.destino if r.destino else ''}: {r.erro}")
    return 0 if ok and registros else 1


if __name__ == "__main__":
    raise SystemExit(principal())
