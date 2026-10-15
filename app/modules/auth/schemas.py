"""Validação dos formulários de usuário."""

from __future__ import annotations

import re

from pydantic import BaseModel, ValidationError, field_validator

from app.modules.auth.models import Perfil

_LOGIN_VALIDO = re.compile(r"^[a-z0-9._-]{3,40}$")


def _validar_nome(valor: str) -> str:
    valor = " ".join(valor.split())
    if not 2 <= len(valor) <= 80:
        raise ValueError("O nome deve ter entre 2 e 80 letras.")
    return valor


def _validar_login(valor: str) -> str:
    valor = valor.strip().lower()
    if not _LOGIN_VALIDO.match(valor):
        raise ValueError(
            "O usuário deve ter de 3 a 40 caracteres: letras sem acento, números, ponto ou traço."
        )
    return valor


class NovoUsuario(BaseModel):
    nome: str
    login: str
    perfil: Perfil
    senha: str

    _nome = field_validator("nome")(_validar_nome)
    _login = field_validator("login")(_validar_login)


class EdicaoUsuario(BaseModel):
    nome: str
    perfil: Perfil
    ativo: bool

    _nome = field_validator("nome")(_validar_nome)


def primeira_mensagem(erro: ValidationError) -> str:
    """Mensagem amigável do primeiro erro de validação."""
    detalhe = erro.errors()[0]
    contexto = detalhe.get("ctx") or {}
    if "error" in contexto:
        return str(contexto["error"])
    campo = ".".join(str(p) for p in detalhe.get("loc", ()))
    return f"Confira o campo '{campo}'."
