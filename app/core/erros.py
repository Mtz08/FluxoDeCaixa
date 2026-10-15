"""Exceções de negócio. As mensagens vão direto para a tela: português simples,
dizendo o que fazer."""

from __future__ import annotations


class ErroNegocio(Exception):
    """Regra de negócio violada. Mostrada ao usuário como está."""

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


class NaoAutenticado(Exception):
    """Sem sessão válida: manda para o login."""


class SemPermissao(ErroNegocio):
    def __init__(
        self, mensagem: str = "Você não tem permissão para isso. Peça ao administrador."
    ) -> None:
        super().__init__(mensagem)


class PrecisaTrocarSenha(Exception):
    """Usuário com senha provisória: manda para a troca de senha."""


class SemUsuarios(Exception):
    """Banco sem nenhum usuário: manda para o primeiro acesso."""


class CsrfInvalido(Exception):
    """Formulário sem o código de segurança correto."""
