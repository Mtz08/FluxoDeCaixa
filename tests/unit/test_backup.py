"""Backup: criptografia, rotação, execução, alertas, restauração e script de recuperação."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.auditoria import Auditoria
from app.core.config import Config
from app.core.datas import FUSO_LOJA, agora_utc
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.backup import cripto, service
from app.modules.backup.models import BackupRegistro, Gatilho
from app.modules.configuracao import service as configuracao
from app.modules.fiado import service as fiado
from app.modules.fiado.models import Cliente
from app.modules.fiado.schemas import DadosCliente

SENHA = "senha-do-backup-1"
RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture
def pastas(tmp_path: Path) -> tuple[Path, Path]:
    nuvem, pendrive = tmp_path / "OneDrive", tmp_path / "Pendrive"
    nuvem.mkdir()
    pendrive.mkdir()
    return nuvem, pendrive


@pytest.fixture
def configurado(
    db: Session, cfg: Config, admin: Usuario, pastas: tuple[Path, Path]
) -> tuple[Path, Path]:
    cripto.guardar_senha(cfg.dir_dados, SENHA)
    configuracao.salvar_backup(db, admin, "\n".join(str(p) for p in pastas), "21:00", None)
    return pastas


def _engine(fabrica: sessionmaker[Session]) -> Engine:
    engine = fabrica.kw["bind"]
    assert isinstance(engine, Engine)
    return engine


# ---------------------------------------------------------------- criptografia


def test_cifra_e_decifra() -> None:
    dados = b"banco" * 5000
    conteudo = cripto.cifrar(dados, SENHA)
    assert dados not in conteudo  # compactado e cifrado
    assert cripto.decifrar(conteudo, SENHA) == dados


def test_senha_errada_ou_arquivo_alterado() -> None:
    conteudo = cripto.cifrar(b"dados", SENHA)
    with pytest.raises(cripto.BackupIlegivel, match="incorreta"):
        cripto.decifrar(conteudo, "outra-senha")
    adulterado = conteudo[:-1] + bytes([conteudo[-1] ^ 1])
    with pytest.raises(cripto.BackupIlegivel):
        cripto.decifrar(adulterado, SENHA)
    with pytest.raises(cripto.BackupIlegivel, match="não é um backup"):
        cripto.decifrar(b"PK\x03\x04qualquer coisa", SENHA)


def test_senha_guardada_protegida(tmp_path: Path) -> None:
    assert cripto.ler_senha(tmp_path) is None
    cripto.guardar_senha(tmp_path, SENHA)
    assert SENHA.encode() not in (tmp_path / cripto.ARQUIVO_SENHA).read_bytes()
    assert cripto.ler_senha(tmp_path) == SENHA
    with pytest.raises(ValueError, match="pelo menos 8"):
        cripto.guardar_senha(tmp_path, "curta")


# ---------------------------------------------------------------- rotação


def test_mantem_30_dias_e_12_meses() -> None:
    inicio = datetime(2025, 1, 1, 21, 0, tzinfo=FUSO_LOJA)
    arquivos = {}
    for dia in range(500):  # ~16 meses, 2 backups por dia
        for hora in (12, 21):
            momento = inicio + timedelta(days=dia, hours=hora - 21)
            arquivos[service.nome_arquivo(Gatilho.DIARIO, momento)] = momento
    mais_recente = max(arquivos.values())
    manter = service.selecionar_para_manter(arquivos, hoje=mais_recente.date() + timedelta(days=1))
    ultimos_dias = {
        m.date()
        for n, m in arquivos.items()
        if n in manter and mais_recente - m < timedelta(days=30)
    }
    assert len(ultimos_dias) == 30
    assert len({(m.year, m.month) for n, m in arquivos.items() if n in manter}) >= 12
    assert len(manter) <= 30 + 12
    assert service.nome_arquivo(Gatilho.DIARIO, mais_recente) in manter


def test_rotacao_so_apaga_arquivos_do_sistema(tmp_path: Path) -> None:
    (tmp_path / "planilha_da_dona.xlsx").write_text("não apagar")
    (tmp_path / "caixa_nome_estranho.cxbk").write_text("não apagar")
    momento = datetime(2026, 1, 1, tzinfo=FUSO_LOJA)
    for dia in range(40):
        nome = service.nome_arquivo(Gatilho.DIARIO, momento + timedelta(days=dia))
        (tmp_path / nome).write_text("x")
    pre = service.nome_arquivo(Gatilho.PRE_RESTAURACAO, momento - timedelta(days=400))
    (tmp_path / pre).write_text("estado antes de uma restauração")
    apagados = service.rotacionar(tmp_path)
    assert (tmp_path / pre).exists()  # nunca apagado automaticamente
    # 01/01 a 09/02: ficam os 30 últimos dias; o "mensal" de janeiro (31/01) já está entre eles.
    assert len(apagados) == 10
    assert (tmp_path / "planilha_da_dona.xlsx").exists()
    assert (tmp_path / "caixa_nome_estranho.cxbk").exists()


# ---------------------------------------------------------------- execução


def test_sem_configuracao_registra_falha(
    cfg: Config, fabrica: sessionmaker[Session], admin: Usuario
) -> None:
    registros = service.fazer_backup(cfg, fabrica, Gatilho.MANUAL, admin.id)
    assert [r.status for r in registros] == ["falha"]
    assert "não configurado" in (registros[0].erro or "")


def test_backup_em_duas_pastas_e_conteudo_restauravel(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
    configurado: tuple[Path, Path],
) -> None:
    registros = service.fazer_backup(cfg, fabrica, Gatilho.MANUAL, admin.id)
    assert [r.status for r in registros] == ["ok", "ok"]
    for pasta in configurado:
        arquivos = list(pasta.glob("caixa_*.cxbk"))
        assert len(arquivos) == 1
        banco = cripto.decifrar(arquivos[0].read_bytes(), SENHA)
        copia = pasta / "conferir.db"
        copia.write_bytes(banco)
        conexao = sqlite3.connect(copia)
        assert conexao.execute("SELECT login FROM usuario").fetchone() == ("maria",)
        conexao.close()
    assert service.alerta(db, cfg) is None


def test_pasta_inacessivel_nao_impede_a_outra(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
    configurado: tuple[Path, Path],
) -> None:
    nuvem, pendrive = configurado
    pendrive.rmdir()  # pendrive retirado
    registros = service.fazer_backup(cfg, fabrica, Gatilho.FECHAMENTO, admin.id)
    assert sorted(r.status for r in registros) == ["falha", "ok"]
    alerta = service.alerta(db, cfg)
    assert alerta is not None and "falhou" in alerta and "Pendrive" in alerta


def test_alertas_na_tela_inicial(
    db: Session, cfg: Config, admin: Usuario, configurado: tuple[Path, Path]
) -> None:
    assert "Nenhum backup" in (service.alerta(db, cfg) or "")
    db.add(BackupRegistro(gatilho="diario", status="ok", criado_em=agora_utc() - timedelta(days=3)))
    db.commit()
    assert "há 3 dias" in (service.alerta(db, cfg) or "")


def test_alerta_sem_configuracao(db: Session, cfg: Config) -> None:
    assert "não foi configurado" in (service.alerta(db, cfg) or "")


def test_backup_diario_uma_vez_por_dia(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
    configurado: tuple[Path, Path],
) -> None:
    configuracao.salvar_backup(db, admin, str(configurado[0]), "00:00", None)
    assert service.deve_fazer_diario(db, cfg)
    db.rollback()  # libera o banco (cada transação reserva a escrita), como faz o agendador
    service.fazer_backup(cfg, fabrica, Gatilho.DIARIO, None)
    assert not service.deve_fazer_diario(db, cfg)


def test_registro_de_backup_e_imutavel(db: Session) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    db.add(BackupRegistro(gatilho="manual", status="ok", criado_em=agora_utc()))
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE backup_registro SET status = 'falha'"))
    db.rollback()


# ---------------------------------------------------------------- restauração


def _clientes(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Cliente)) or 0


def test_restaurar_volta_ao_estado_do_backup(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
    configurado: tuple[Path, Path],
) -> None:
    service.fazer_backup(cfg, fabrica, Gatilho.MANUAL, admin.id)
    arquivo = next(configurado[0].glob("caixa_*.cxbk"))
    fiado.criar_cliente(db, admin, DadosCliente(nome="Depois do Backup", consentimento=True), None)
    assert _clientes(db) == 1
    db.close()

    copia = service.restaurar(
        cfg, _engine(fabrica), fabrica, arquivo.read_bytes(), None, admin, "127.0.0.1"
    )
    assert copia.exists()  # estado anterior guardado

    novo = fabrica()
    try:
        assert _clientes(novo) == 0
        acoes = list(novo.scalars(select(Auditoria.acao)))
        assert "backup_restaurado" in acoes
    finally:
        novo.close()
    # Backup automático do estado anterior também foi feito nas pastas.
    assert any("pre_restauracao" in p.name for p in configurado[0].glob("*.cxbk"))


def test_restaurar_com_senha_errada_nao_mexe_em_nada(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
) -> None:
    conteudo = cripto.cifrar(b"qualquer", SENHA)
    with pytest.raises(ErroNegocio, match="incorreta"):
        service.restaurar(cfg, _engine(fabrica), fabrica, conteudo, "errada!!", admin, None)


def test_restaurar_arquivo_que_nao_e_banco(
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
) -> None:
    conteudo = cripto.cifrar(b"isto nao e um banco sqlite", SENHA)
    with pytest.raises(ErroNegocio, match="danificado|não contém"):
        service.restaurar(cfg, _engine(fabrica), fabrica, conteudo, SENHA, admin, None)


def test_so_admin_restaura(cfg: Config, fabrica: sessionmaker[Session], operador: Usuario) -> None:
    with pytest.raises(SemPermissao):
        service.restaurar(cfg, _engine(fabrica), fabrica, b"", SENHA, operador, None)


def test_lista_de_restauracao_nao_aceita_caminho_qualquer(
    db: Session,
    cfg: Config,
    configurado: tuple[Path, Path],
) -> None:
    with pytest.raises(ErroNegocio, match="não encontrado"):
        service.arquivo_da_lista(db, cfg, str(cfg.caminho_banco))


# ---------------------------------------------------------------- script de recuperação


def test_script_de_recuperacao_extrai_backup(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
    configurado: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    service.fazer_backup(cfg, fabrica, Gatilho.MANUAL, admin.id)
    arquivo = next(configurado[0].glob("caixa_*.cxbk"))
    saida = tmp_path / "extraido.db"
    ambiente = os.environ | {"CAIXA_DIR_DADOS": str(cfg.dir_dados), "PYTHONIOENCODING": "utf-8"}
    resultado = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(RAIZ / "scripts" / "restaurar_backup.py"),
            str(arquivo),
            "--saida",
            str(saida),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=ambiente,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "extraído e verificado" in resultado.stdout
    assert sqlite3.connect(saida).execute("SELECT count(*) FROM usuario").fetchone() == (1,)


def test_backups_de_hoje_sao_todos_mantidos() -> None:
    hoje = datetime(2026, 10, 15, 9, 0, tzinfo=FUSO_LOJA)
    arquivos = {
        service.nome_arquivo(g, hoje + timedelta(hours=h)): hoje + timedelta(hours=h)
        for h, g in ((0, Gatilho.MANUAL), (5, Gatilho.FECHAMENTO), (10, Gatilho.DIARIO))
    }
    assert service.selecionar_para_manter(arquivos, hoje=hoje.date()) == set(arquivos)


def test_nome_do_arquivo_usa_horario_da_loja() -> None:
    momento = datetime(2026, 10, 15, 2, 30, tzinfo=FUSO_LOJA)
    assert (
        service.nome_arquivo(Gatilho.FECHAMENTO, momento) == "caixa_20261015_023000_fechamento.cxbk"
    )
    assert service.data_do_arquivo("caixa_20261015_023000_fechamento.cxbk") == momento
    assert service.data_do_arquivo("outro.cxbk") is None


# ---------------------------------------------------------------- agendador


def test_agendador_faz_o_diario_uma_vez(
    db: Session,
    cfg: Config,
    fabrica: sessionmaker[Session],
    admin: Usuario,
    configurado: tuple[Path, Path],
) -> None:
    from types import SimpleNamespace

    from app.modules.backup import agendador

    configuracao.salvar_backup(db, admin, str(configurado[0]), "00:00", None)
    db.rollback()
    app = SimpleNamespace(
        state=SimpleNamespace(config=cfg, fabrica_sessao=fabrica, banco_corrompido=False)
    )
    assert agendador.verificar_e_executar(app) is True  # type: ignore[arg-type]
    assert agendador.verificar_e_executar(app) is False  # type: ignore[arg-type]
    assert len(list(configurado[0].glob("caixa_*_diario.cxbk"))) == 1
