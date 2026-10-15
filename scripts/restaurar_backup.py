"""Restaura um backup (.cxbk) SEM abrir o sistema — para quando o banco estiver corrompido.

Uso (com o sistema FECHADO):
    python scripts/restaurar_backup.py CAMINHO\\DO\\BACKUP.cxbk            -> instala o backup
    python scripts/restaurar_backup.py BACKUP.cxbk --saida copia.db  -> só extrai para conferir

O banco atual NÃO é apagado: vai para a pasta "pre_restauracao" dentro da pasta de dados.
"""

from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import obter_config
from app.core.erros import ErroNegocio
from app.modules.backup import cripto
from app.modules.backup.service import PASTA_PRE_RESTAURACAO, validar_banco_restaurado


def sistema_aberto(porta: int) -> bool:
    with socket.socket() as teste:
        teste.settimeout(0.5)
        return teste.connect_ex(("127.0.0.1", porta)) == 0


def senha_guardada(pasta_dados: Path) -> str | None:
    try:
        return cripto.ler_senha(pasta_dados)
    except OSError:
        return None


def decifrar(conteudo: bytes, pasta_dados: Path) -> bytes:
    """Tenta a senha guardada neste PC; se não houver ou não servir, pergunta."""
    guardada = senha_guardada(pasta_dados)
    if guardada:
        try:
            return cripto.decifrar(conteudo, guardada)
        except cripto.BackupIlegivel:
            print("A senha guardada neste computador não abre este backup.")
    for _ in range(3):
        try:
            return cripto.decifrar(
                conteudo, getpass.getpass("Senha do backup (anotada no papel): ")
            )
        except cripto.BackupIlegivel:
            print("Senha incorreta ou arquivo danificado.")
    raise SystemExit("ERRO: não foi possível abrir o backup. Nada foi alterado.")


def principal() -> int:
    parser = argparse.ArgumentParser(description="Restaura um backup do Caixa da Papelaria.")
    parser.add_argument("arquivo", type=Path, help="arquivo .cxbk")
    parser.add_argument("--saida", type=Path, help="só extrai o banco para este arquivo")
    args = parser.parse_args()

    cfg = obter_config()
    if not args.arquivo.is_file():
        print(f"ERRO: arquivo não encontrado: {args.arquivo}")
        return 1
    conteudo = args.arquivo.read_bytes()
    dados = decifrar(conteudo, cfg.dir_dados)

    destino_teste = (args.saida or cfg.dir_dados / "tmp" / "restaurar_cli.db").resolve()
    destino_teste.parent.mkdir(parents=True, exist_ok=True)
    destino_teste.write_bytes(dados)
    try:
        validar_banco_restaurado(destino_teste)
    except ErroNegocio as erro:
        destino_teste.unlink(missing_ok=True)
        print(f"ERRO: {erro.mensagem} Nada foi alterado.")
        return 1

    if args.saida:
        print(f"OK: banco extraído e verificado em {destino_teste}")
        return 0

    if sistema_aberto(cfg.porta):
        destino_teste.unlink(missing_ok=True)
        print("ERRO: o sistema está aberto. Feche a janela preta do sistema e rode de novo.")
        return 1

    # Guarda o banco atual (mesmo com defeito) antes de substituir.
    pasta_pre = cfg.dir_dados / PASTA_PRE_RESTAURACAO
    pasta_pre.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    for sufixo in ("", "-wal", "-shm"):
        atual = Path(str(cfg.caminho_banco) + sufixo)
        if atual.exists():
            os.replace(atual, pasta_pre / f"caixa_antes_de_restaurar_{carimbo}.db{sufixo}")
    os.replace(destino_teste, cfg.caminho_banco)
    print("OK: backup restaurado. Abra o sistema normalmente (iniciar.bat).")
    print(f"O banco anterior foi guardado em: {pasta_pre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
