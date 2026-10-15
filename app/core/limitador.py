"""Limite de tentativas de login por endereço (IP).

Complementa o bloqueio por usuário: num site aberto na internet, robôs testam
muitos usuários diferentes a partir do mesmo endereço. Guardado em memória
(um único processo); reiniciar o sistema zera a contagem.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class LimitadorTentativas:
    def __init__(self, maximo: int = 20, janela_segundos: int = 15 * 60) -> None:
        self.maximo = maximo
        self.janela = janela_segundos
        self._falhas: dict[str, deque[float]] = defaultdict(deque)
        self._trava = threading.Lock()

    def _limpar_antigas(self, ip: str, agora: float) -> deque[float]:
        falhas = self._falhas[ip]
        while falhas and agora - falhas[0] > self.janela:
            falhas.popleft()
        return falhas

    def bloqueado(self, ip: str | None) -> bool:
        if not ip:
            return False
        with self._trava:
            return len(self._limpar_antigas(ip, time.monotonic())) >= self.maximo

    def registrar_falha(self, ip: str | None) -> None:
        if not ip:
            return
        with self._trava:
            agora = time.monotonic()
            self._limpar_antigas(ip, agora).append(agora)

    def limpar(self, ip: str | None) -> None:
        if not ip:
            return
        with self._trava:
            self._falhas.pop(ip, None)
