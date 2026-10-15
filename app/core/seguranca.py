"""Primitivas de segurança: hash de senha, tokens e CSRF.

CSRF usa "double submit cookie": um cookie aleatório (SameSite=Strict) e o
mesmo valor num campo oculto do formulário (ou no cabeçalho X-CSRF-Token, no
HTMX). Um site de fora não consegue ler o cookie para preencher o campo.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

if TYPE_CHECKING:
    from app.core.config import Config

COOKIE_SESSAO = "caixa_sessao"
COOKIE_CSRF = "caixa_csrf"
CAMPO_CSRF = "csrf_token"
CABECALHO_CSRF = "x-csrf-token"

_hasher = PasswordHasher()

# Hash válido de senha aleatória: usado para gastar o mesmo tempo quando o
# login não existe (não revela quais logins existem).
_HASH_FALSO = _hasher.hash(secrets.token_urlsafe(16))


def gerar_hash_senha(senha: str) -> str:
    return _hasher.hash(senha)


def conferir_senha(senha: str, senha_hash: str | None) -> bool:
    try:
        return _hasher.verify(senha_hash or _HASH_FALSO, senha)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def gerar_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_iguais(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)


def _nome_do_host(host: str) -> str:
    """'192.168.0.5:8765' -> '192.168.0.5'; '[::1]:8765' -> '::1'."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_permitido(host: str, cfg: Config) -> bool:
    """Aceita só localhost, IP de loopback/rede privada, o nome deste PC e extras configurados."""
    nome = _nome_do_host(host)
    if not nome:
        return False
    if nome in {"localhost", socket.gethostname().lower()} or nome in cfg.hosts_extras:
        return True
    try:
        ip = ipaddress.ip_address(nome)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private
