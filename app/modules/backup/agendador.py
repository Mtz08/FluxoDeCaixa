"""Backup diário automático: verifica a cada minuto se já é hora de fazer o backup do dia.

Roda dentro do próprio servidor (sem Agendador de Tarefas do Windows). Se o PC
estiver desligado no horário, o backup sai assim que o sistema for aberto.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from app.modules.backup import service
from app.modules.backup.models import Gatilho

log = logging.getLogger(__name__)
INTERVALO_SEGUNDOS = 60


def verificar_e_executar(app: FastAPI) -> bool:
    """Faz o backup diário se for a hora. Devolve True se executou."""
    cfg, fabrica = app.state.config, app.state.fabrica_sessao
    db = fabrica()
    try:
        devido = service.deve_fazer_diario(db, cfg)
    finally:
        db.close()
    if devido:
        service.fazer_backup(cfg, fabrica, Gatilho.DIARIO, None)
    return devido


async def rodar(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(INTERVALO_SEGUNDOS)
        if app.state.banco_corrompido:
            continue
        try:
            await asyncio.to_thread(verificar_e_executar, app)
        except Exception:  # o agendador nunca pode derrubar o sistema
            log.exception("Falha no agendador de backup")
