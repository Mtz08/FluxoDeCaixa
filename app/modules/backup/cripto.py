"""Criptografia dos backups e guarda da senha no PC.

Formato do arquivo .cxbk (versão 1):
    "CXBK" + 0x01 | sal (16 bytes) | nonce (12 bytes) | AES-256-GCM( gzip(banco SQLite) )
A chave vem da senha via scrypt (n=2^16, r=8, p=1). O cabeçalho entra como dado
autenticado: qualquer alteração no arquivo é detectada ao restaurar.

A senha fica no PC protegida pela DPAPI do Windows (só o mesmo usuário do Windows,
nesta máquina, consegue ler). Ela protege as CÓPIAS (Drive/pendrive), não o PC.
"""

from __future__ import annotations

import ctypes
import gzip
import logging
import os
import sys
from ctypes import wintypes
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

log = logging.getLogger(__name__)

MAGICO = b"CXBK\x01"
TAM_SAL = 16
TAM_NONCE = 12
SENHA_MINIMA = 8
ARQUIVO_SENHA = "backup.senha"


class BackupIlegivel(Exception):
    """Senha errada, arquivo alterado/corrompido ou que não é backup do sistema."""


def _chave(senha: str, sal: bytes) -> bytes:
    return Scrypt(salt=sal, length=32, n=2**16, r=8, p=1).derive(senha.encode("utf-8"))


def cifrar(dados: bytes, senha: str) -> bytes:
    sal, nonce = os.urandom(TAM_SAL), os.urandom(TAM_NONCE)
    cabecalho = MAGICO + sal
    cifrado = AESGCM(_chave(senha, sal)).encrypt(nonce, gzip.compress(dados, 6), cabecalho)
    return cabecalho + nonce + cifrado


def decifrar(conteudo: bytes, senha: str) -> bytes:
    if not conteudo.startswith(MAGICO) or len(conteudo) < len(MAGICO) + TAM_SAL + TAM_NONCE + 16:
        raise BackupIlegivel("Este arquivo não é um backup do Caixa da Papelaria.")
    sal = conteudo[len(MAGICO) : len(MAGICO) + TAM_SAL]
    inicio_nonce = len(MAGICO) + TAM_SAL
    nonce = conteudo[inicio_nonce : inicio_nonce + TAM_NONCE]
    try:
        comprimido = AESGCM(_chave(senha, sal)).decrypt(
            nonce, conteudo[inicio_nonce + TAM_NONCE :], conteudo[:inicio_nonce]
        )
    except InvalidTag as erro:
        raise BackupIlegivel("Senha do backup incorreta ou arquivo danificado.") from erro
    return gzip.decompress(comprimido)


# ---------------------------------------------------------------- DPAPI (Windows)


class _Blob(ctypes.Structure):
    _fields_ = [  # noqa: RUF012 - formato exigido pelo ctypes
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi(dados: bytes, proteger: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(dados, len(dados))
    entrada = _Blob(len(dados), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    saida = _Blob()
    funcao = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    ok = funcao(ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(saida))
    if not ok:
        raise OSError("O Windows não conseguiu proteger/ler a senha do backup (DPAPI).")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def proteger(dados: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi(dados, proteger=True)
    # Servidor Linux: sem DPAPI. O arquivo fica com permissão só do dono (ver guardar_senha)
    # dentro da pasta de dados, que não é acessível pela web.
    return dados


def desproteger(dados: bytes) -> bytes:
    return _dpapi(dados, proteger=False) if sys.platform == "win32" else dados


def guardar_senha(pasta_dados: Path, senha: str) -> None:
    if len(senha) < SENHA_MINIMA:
        raise ValueError(f"A senha do backup precisa ter pelo menos {SENHA_MINIMA} caracteres.")
    pasta_dados.mkdir(parents=True, exist_ok=True)
    temporario = pasta_dados / (ARQUIVO_SENHA + ".tmp")
    temporario.write_bytes(proteger(senha.encode("utf-8")))
    if sys.platform != "win32":
        os.chmod(temporario, 0o600)
    os.replace(temporario, pasta_dados / ARQUIVO_SENHA)


def ler_senha(pasta_dados: Path) -> str | None:
    arquivo = pasta_dados / ARQUIVO_SENHA
    if not arquivo.exists():
        return None
    return desproteger(arquivo.read_bytes()).decode("utf-8")
