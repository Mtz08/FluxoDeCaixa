"""Zerar o caixa depois de um teste (scripts/zerar_caixa.py), contra banco real."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auditoria import Auditoria
from app.core.config import Config
from app.core.datas import hoje_local
from app.modules.auth.models import Usuario
from app.modules.backup import cripto
from app.modules.caixa import manutencao
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa import service as caixa
from app.modules.caixa.models import MovimentoCaixa, SessaoCaixa, TipoMovimento
from app.modules.caixa.schemas import NovoLancamento, Parte
from app.modules.fiado import service as fiado
from app.modules.fiado.models import Cliente, FiadoLancamento, FiadoParcela
from app.modules.fiado.schemas import DadosCliente, NovaCompra, NovoPagamento
from tests.unit.test_scripts import rodar

HOJE = hoje_local()


def _pix(db: Session) -> int:
    return next(f.id for f in caixa_repo.formas_ativas(db) if f.nome == "PIX")


@pytest.fixture
def teste_feito(db: Session, admin: Usuario) -> tuple[Cliente, Cliente]:
    """O que a dona fez no teste: caixa, entrada, fiado parcelado com juros, pagamento, estorno.

    E uma dívida do caderno (lançada sem caixa) para outra cliente.
    """
    bia = fiado.criar_cliente(db, admin, DadosCliente(nome="Bia Lima", consentimento=True), None)
    fiado.lancar_saldo_inicial(db, admin, bia.id, 5000, HOJE, "caderno", uuid4(), None)
    caixa.abrir_caixa(db, admin, 10_000, uuid4(), None)
    caixa.lancar(
        db,
        admin,
        NovoLancamento(
            tipo=TipoMovimento.ENTRADA,
            partes=[Parte(forma_id=_pix(db), valor_centavos=2000)],
            chave=uuid4(),
        ),
        None,
    )
    ana = fiado.criar_cliente(db, admin, DadosCliente(nome="Ana Souza", consentimento=True), None)
    venc = HOJE - timedelta(days=40)
    compra = NovaCompra(
        cliente_id=ana.id,
        valor_total_centavos=15_000,
        descricao="mochila",
        data_referencia=venc - timedelta(days=5),
        chave=uuid4(),
        parcelas=3,
        primeiro_vencimento=venc,
    )
    fiado.anotar_compra(db, admin, compra, None, False, None, fiado.CondicoesJuros(330, 10))
    pagamento = NovoPagamento(
        cliente_id=ana.id, partes=[Parte(forma_id=_pix(db), valor_centavos=3000)], chave=uuid4()
    )
    lanc, _ = fiado.receber_pagamento(db, admin, pagamento, None)
    fiado.estornar(db, admin, lanc.id, "teste", uuid4(), None, None)
    return ana, bia


def _contar(db: Session, modelo: type) -> int:
    return int(db.scalar(select(func.count()).select_from(modelo)) or 0)


def test_levantamento_mostra_o_que_vai_sair(
    db: Session, teste_feito: tuple[Cliente, Cliente]
) -> None:
    lev = manutencao.levantar(db)
    assert len(lev.sessoes) == 1 and lev.movimentos >= 3  # entrada, pagamento, estorno
    assert lev.parcelas == 3
    # compra, juros, pagamento e estorno da Ana; o caderno da Bia fica de fora
    assert len(lev.fiado_ligado) == 4
    assert [(t, v) for _, _, t, v in lev.fiado_sem_caixa] == [("saldo_inicial", 5000)]


def test_zerar_apaga_o_caixa_e_mantem_o_resto(
    db: Session, admin: Usuario, teste_feito: tuple[Cliente, Cliente]
) -> None:
    ana, bia = teste_feito
    gatilhos = manutencao.contar_gatilhos(db)
    feito = manutencao.zerar(db)
    assert len(feito.sessoes) == 1
    for modelo in (SessaoCaixa, MovimentoCaixa, FiadoParcela):
        assert _contar(db, modelo) == 0
    restante = list(db.scalars(select(FiadoLancamento)))
    assert [(x.cliente_id, x.tipo) for x in restante] == [(bia.id, "saldo_inicial")]
    db.expire_all()
    assert (ana.saldo_centavos, bia.saldo_centavos) == (0, 5000)  # cache recalculado
    assert _contar(db, Cliente) == 2 and fiado.total_devido(db, ana) == 0

    # As proteções voltaram:
    assert manutencao.contar_gatilhos(db) == gatilhos
    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM fiado_lancamento"))
    db.rollback()
    registro = db.scalar(select(Auditoria).where(Auditoria.acao == "caixa_zerado"))
    assert registro is not None and '"caixas": 1' in (registro.depois_json or "")

    # E o caixa de verdade abre normalmente.
    caixa.abrir_caixa(db, admin, 5000, uuid4(), None)
    assert caixa.sessao_aberta(db) is not None


def test_zerar_tambem_o_fiado_sem_caixa(db: Session, teste_feito: tuple[Cliente, Cliente]) -> None:
    _, bia = teste_feito
    manutencao.zerar(db, apagar_fiado_sem_caixa=True)
    assert _contar(db, FiadoLancamento) == 0
    db.expire_all()
    assert bia.saldo_centavos == 0


def test_falha_no_meio_nao_apaga_nada(
    db: Session, teste_feito: tuple[Cliente, Cliente], monkeypatch: pytest.MonkeyPatch
) -> None:
    gatilhos = manutencao.contar_gatilhos(db)
    contagens = iter([gatilhos, gatilhos - 1])  # antes ok; depois, uma proteção "sumiu"
    monkeypatch.setattr(manutencao, "contar_gatilhos", lambda _db: next(contagens))
    with pytest.raises(RuntimeError, match="nada foi apagado"):
        manutencao.zerar(db)
    monkeypatch.undo()
    assert _contar(db, SessaoCaixa) == 1 and _contar(db, FiadoParcela) == 3
    assert manutencao.contar_gatilhos(db) == gatilhos


def _entrada(cfg: Config, *respostas: str) -> str:
    url = [] if cfg.url_banco_externo else [""]  # sem a variável, o script pergunta o endereço
    return "\n".join([*url, *respostas]) + "\n"


def test_script_faz_backup_e_zera(
    db: Session, cfg: Config, teste_feito: tuple[Cliente, Cliente], tmp_path: Path
) -> None:
    db.commit()
    saida = tmp_path / "saida"
    env = {"CAIXA_SENHA_BACKUP": "senha-backup-1", "CAIXA_PASTA_SAIDA": str(saida)}
    resultado = rodar("zerar_caixa.py", cfg, _entrada(cfg, "n", "ZERAR"), **env)
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "Caixas: 1" in resultado.stdout and "Pronto: 1 caixa(s)" in resultado.stdout
    db.expire_all()
    assert _contar(db, SessaoCaixa) == 0 and _contar(db, FiadoLancamento) == 1
    (arquivo,) = saida.glob("antes_de_zerar_caixa_*.cxbk")
    copia = tmp_path / "copia.db"
    copia.write_bytes(cripto.decifrar(arquivo.read_bytes(), "senha-backup-1"))
    import sqlite3

    with sqlite3.connect(copia) as banco:  # o backup tem o caixa do teste
        assert banco.execute("SELECT COUNT(*) FROM sessao_caixa").fetchone() == (1,)


def test_script_cancelado_nao_apaga(
    db: Session, cfg: Config, teste_feito: tuple[Cliente, Cliente], tmp_path: Path
) -> None:
    db.commit()
    env = {"CAIXA_SENHA_BACKUP": "senha-backup-1", "CAIXA_PASTA_SAIDA": str(tmp_path)}
    resultado = rodar("zerar_caixa.py", cfg, _entrada(cfg, "n", "zerar"), **env)
    assert resultado.returncode == 1 and "Nada foi apagado" in resultado.stdout
    db.expire_all()
    assert _contar(db, SessaoCaixa) == 1
