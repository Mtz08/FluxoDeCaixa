"""Zera o CAIXA depois de um teste: apaga aberturas, entradas, saídas e fechamentos,
e o fiado lançado nesses caixas. Clientes, usuários e configurações ficam.

Funciona no banco do site (Neon/PostgreSQL) ou no SQLite da loja:
- pede o endereço do banco do Neon (digitação oculta; Enter vazio = banco da loja);
- mostra tudo o que vai ser apagado;
- grava um backup criptografado ANTES (pasta Downloads);
- só apaga depois de digitar ZERAR. Tudo numa transação: se algo falhar, nada muda.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect

from app.core.config import obter_config
from app.core.database import criar_engine, criar_fabrica_sessao
from app.core.datas import agora_utc, formatar_data_hora, para_local
from app.core.dinheiro import formatar_reais
from app.modules.backup import cripto
from app.modules.backup import service as backup
from app.modules.caixa import manutencao


def perguntar(texto: str, oculto: bool = False) -> str:
    if oculto and sys.stdin.isatty():
        return getpass.getpass(texto).strip()
    return input(texto).strip()


def senha_do_backup() -> str:
    if senha := cripto.senha_no_ambiente():
        return senha
    print("Senha para o backup (use a MESMA do site, para poder restaurar por lá).")
    while True:
        senha = perguntar("  Senha: ", oculto=True)
        if len(senha) < 8:
            print("  Mínimo de 8 caracteres.")
            continue
        if perguntar("  Repita: ", oculto=True) == senha:
            return senha
        print("  As senhas não conferem.")


def mostrar(lev: manutencao.Levantamento) -> None:
    print(f"\nCaixas: {len(lev.sessoes)}")
    for s in lev.sessoes:
        print(
            f"  nº {s.id}  aberto em {formatar_data_hora(s.aberta_em)}  ({s.status}, "
            f"{s.movimentos} movimento(s))"
        )
    print(f"Movimentos do caixa: {lev.movimentos} (com {lev.partes} parte(s) de pagamento)")
    print(
        f"Lançamentos de fiado feitos nesses caixas: {len(lev.fiado_ligado)}"
        f" (parcelas: {lev.parcelas})"
    )


def principal() -> int:
    if not os.environ.get("CAIXA_DATABASE_URL"):
        url = perguntar(
            "Cole o endereço do banco do Neon e tecle Enter (não aparece na tela).\n"
            "Deixe vazio para usar o banco deste computador: ",
            oculto=True,
        )
        if url:
            os.environ["CAIXA_DATABASE_URL"] = url
    cfg = obter_config()
    engine = criar_engine(cfg)
    try:
        if not inspect(engine).has_table("fiado_parcela"):
            print(
                "O banco ainda não está na versão 1.5.0. Espere o site atualizar e tente de novo."
            )
            return 1
        db = criar_fabrica_sessao(engine)()
        try:
            lev = manutencao.levantar(db)
            db.rollback()
            if lev.vazio:
                print("O caixa já está vazio. Nada a fazer.")
                return 0
            mostrar(lev)
            apagar_resto = False
            if lev.fiado_sem_caixa:
                print("\nFiado lançado FORA do caixa (saldo do caderno, abatimento):")
                for cliente_id, lanc_id, tipo, valor in lev.fiado_sem_caixa:
                    print(
                        f"  cliente nº {cliente_id}: {tipo} {formatar_reais(valor)}"
                        f" (lançamento {lanc_id})"
                    )
                resposta = perguntar("Apagar esses também? (s/N): ").lower()
                apagar_resto = resposta in ("s", "sim")

            print("\nGravando backup antes de apagar...")
            senha = senha_do_backup()
            pasta = Path(os.environ.get("CAIXA_PASTA_SAIDA") or Path.home() / "Downloads")
            pasta.mkdir(parents=True, exist_ok=True)
            arquivo = pasta / f"antes_de_zerar_caixa_{para_local(agora_utc()):%Y-%m-%d_%H%M%S}.cxbk"
            arquivo.write_bytes(cripto.cifrar(backup.instantaneo(cfg, engine), senha))
            print(f"Backup salvo em: {arquivo}")

            if perguntar("\nPara apagar, digite ZERAR (qualquer outra coisa cancela): ") != "ZERAR":
                print("Cancelado. Nada foi apagado.")
                return 1
            feito = manutencao.zerar(db, apagar_fiado_sem_caixa=apagar_resto)
        finally:
            db.close()
    finally:
        engine.dispose()
    print(
        f"\nPronto: {len(feito.sessoes)} caixa(s), {feito.movimentos} movimento(s) e "
        f"{len(feito.fiado_ligado)} lançamento(s) de fiado apagados."
    )
    print("Saldos dos clientes recalculados. Já pode abrir o caixa de verdade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
