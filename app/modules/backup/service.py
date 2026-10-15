"""Backup e restauração.

Fluxo do backup:
1. Cópia do banco EM USO pela API de backup online do SQLite (nunca copiar o arquivo).
2. PRAGMA integrity_check na cópia.
3. Compacta + criptografa (cripto.py) e grava em cada pasta configurada
   (grava como .tmp e renomeia: um arquivo pela metade nunca parece um backup válido).
4. Relê o arquivo gravado e confere o SHA-256.
5. Rotação: mantém o último de cada um dos 30 dias mais recentes + o último de cada
   um dos 12 meses mais recentes.
Falha num destino (pendrive fora, disco cheio) fica registrada e vira alerta na tela
inicial, mas NUNCA trava o caixa.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core import auditoria
from app.core.auditoria import Auditoria
from app.core.config import Config
from app.core.database import criar_engine, eh_sqlite
from app.core.datas import FUSO_LOJA, agora_utc, formatar_data_hora, hoje_local, para_local
from app.core.erros import ErroNegocio, SemPermissao
from app.core.migracoes import migrar, migrar_na_transacao
from app.modules.auth.models import Usuario
from app.modules.backup import cripto
from app.modules.backup.models import BackupRegistro, Gatilho
from app.modules.backup.transferencia import transferir
from app.modules.configuracao import service as configuracao

log = logging.getLogger(__name__)

EXTENSAO = ".cxbk"
_NOME = re.compile(r"^caixa_(\d{8})_(\d{6})_([a-z_]+)\.cxbk$")
DIAS_MANTIDOS = 30
MESES_MANTIDOS = 12
ALERTA_DIAS = 2
PASTA_PRE_RESTAURACAO = "pre_restauracao"


# ---------------------------------------------------------------- cópia do banco


def _integridade(caminho: Path) -> list[str]:
    conexao = sqlite3.connect(caminho)
    try:
        linhas = [str(r[0]) for r in conexao.execute("PRAGMA integrity_check")]
    finally:
        conexao.close()
    return [] if linhas == ["ok"] else linhas


def copiar_banco(origem: Path, destino: Path) -> None:
    """API de backup online do SQLite: cópia consistente mesmo com o sistema em uso."""
    fonte = sqlite3.connect(origem)
    alvo = sqlite3.connect(destino)
    try:
        fonte.backup(alvo)
    finally:
        alvo.close()
        fonte.close()


def _engine_da_sessao(db: Session) -> Engine:
    engine = db.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError("Sessão sem engine associado.")
    return engine


def _pasta_temporaria(cfg: Config) -> Path:
    pasta_tmp = cfg.dir_dados / "tmp"
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=pasta_tmp))


def instantaneo(cfg: Config, engine: Engine | None = None) -> bytes:
    """Bytes de uma cópia íntegra do banco atual, sempre no formato SQLite.

    SQLite: API de backup online. PostgreSQL: os dados são copiados para um SQLite
    novo (mesma estrutura), então o backup é o mesmo arquivo nos dois casos.
    """
    pasta = _pasta_temporaria(cfg)
    caminho = pasta / "caixa.db"
    try:
        if engine is None or eh_sqlite(engine):
            copiar_banco(cfg.caminho_banco, caminho)
        else:
            _copiar_postgres_para_sqlite(engine, Config(dir_dados=pasta))
        problemas = _integridade(caminho)
        if problemas:
            raise ErroNegocio(f"O banco está com problema de integridade: {problemas[0]}")
        return caminho.read_bytes()
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _copiar_postgres_para_sqlite(engine: Engine, cfg_destino: Config) -> None:
    destino_engine = criar_engine(cfg_destino)
    try:
        migrar(destino_engine)
        with engine.connect() as origem, destino_engine.connect() as destino:
            with destino.begin():
                transferir(origem, destino)
            origem.rollback()
    finally:
        destino_engine.dispose()


# ---------------------------------------------------------------- rotação


def data_do_arquivo(nome: str) -> datetime | None:
    achado = _NOME.match(nome)
    if not achado:
        return None
    return datetime.strptime(achado.group(1) + achado.group(2), "%Y%m%d%H%M%S").replace(
        tzinfo=FUSO_LOJA
    )


def selecionar_para_manter(arquivos: dict[str, datetime], hoje: date | None = None) -> set[str]:
    """Quais backups ficam:
    - todos os de hoje;
    - o último de cada um dos 30 dias mais recentes;
    - o último de cada um dos 12 meses mais recentes;
    - os feitos antes de uma restauração (nunca apagados automaticamente).
    """
    hoje = hoje or hoje_local()
    manter = {
        nome
        for nome, momento in arquivos.items()
        if momento.date() >= hoje or nome.endswith(f"_{Gatilho.PRE_RESTAURACAO}{EXTENSAO}")
    }
    por_dia: dict[object, tuple[datetime, str]] = {}
    por_mes: dict[object, tuple[datetime, str]] = {}
    for nome, momento in arquivos.items():
        for grupo, chave in ((por_dia, momento.date()), (por_mes, (momento.year, momento.month))):
            atual = grupo.get(chave)
            if atual is None or (momento, nome) > atual:
                grupo[chave] = (momento, nome)
    dias = sorted(por_dia.values(), reverse=True)[:DIAS_MANTIDOS]
    meses = sorted(por_mes.values(), reverse=True)[:MESES_MANTIDOS]
    return manter | {nome for _, nome in dias} | {nome for _, nome in meses}


def rotacionar(pasta: Path) -> list[str]:
    """Apaga backups antigos do sistema (só arquivos com o nome padrão). Devolve os apagados."""
    arquivos = {
        caminho.name: momento
        for caminho in pasta.glob(f"caixa_*{EXTENSAO}")
        if (momento := data_do_arquivo(caminho.name)) is not None
    }
    manter = selecionar_para_manter(arquivos)
    apagados = []
    for nome in sorted(set(arquivos) - manter):
        try:
            (pasta / nome).unlink()
            apagados.append(nome)
        except OSError:
            log.warning("Não foi possível apagar backup antigo %s", nome)
    return apagados


# ---------------------------------------------------------------- backup


def nome_arquivo(gatilho: Gatilho, momento: datetime) -> str:
    return f"caixa_{para_local(momento):%Y%m%d_%H%M%S}_{gatilho}{EXTENSAO}"


def _mensagem_erro(erro: Exception) -> str:
    if isinstance(erro, ErroNegocio):
        return erro.mensagem
    if isinstance(erro, OSError):
        if getattr(erro, "errno", None) == 28:  # ENOSPC
            return "Disco cheio na pasta de backup."
        return f"Pasta de backup inacessível ({type(erro).__name__})."
    return f"Erro inesperado no backup ({type(erro).__name__})."


def _gravar_destino(pasta: Path, nome: str, conteudo: bytes, sha: str) -> None:
    temporario = pasta / (nome + ".tmp")
    temporario.write_bytes(conteudo)
    os.replace(temporario, pasta / nome)
    if hashlib.sha256((pasta / nome).read_bytes()).hexdigest() != sha:
        raise ErroNegocio("O arquivo gravado não confere (falha de gravação no destino).")


def fazer_backup(
    cfg: Config, fabrica: sessionmaker[Session], gatilho: Gatilho, usuario_id: int | None
) -> list[BackupRegistro]:
    """Faz o backup em todas as pastas configuradas. Nunca levanta exceção: registra falhas."""
    db = fabrica()
    try:
        prefs = configuracao.carregar(db, cfg)
        db.rollback()  # não segurar a trava do banco durante a cópia
        engine = _engine_da_sessao(db)
        registros = _executar(cfg, engine, prefs.backup_pastas, gatilho, usuario_id)
        for registro in registros:
            db.add(registro)
        db.commit()
        return registros
    except Exception:
        log.exception("Falha inesperada ao registrar backup")
        db.rollback()
        return []
    finally:
        db.close()


def _executar(
    cfg: Config,
    engine: Engine,
    pastas: tuple[str, ...],
    gatilho: Gatilho,
    usuario_id: int | None,
) -> list[BackupRegistro]:
    agora = agora_utc()

    def registro(status: str, **dados: object) -> BackupRegistro:
        return BackupRegistro(
            gatilho=gatilho, status=status, usuario_id=usuario_id, criado_em=agora, **dados
        )

    try:
        senha = cripto.ler_senha(cfg.dir_dados)
    except OSError as erro:
        return [registro("falha", erro=_mensagem_erro(erro))]
    if not senha or not pastas:
        return [registro("falha", erro="Backup não configurado: defina a senha e as pastas.")]
    try:
        conteudo = cripto.cifrar(instantaneo(cfg, engine), senha)
    except Exception as erro:  # banco travado, integridade, disco local...
        log.exception("Falha ao preparar o backup")
        return [registro("falha", erro=_mensagem_erro(erro))]

    nome = nome_arquivo(gatilho, agora)
    sha = hashlib.sha256(conteudo).hexdigest()
    resultado = []
    for pasta_texto in pastas:
        pasta = Path(pasta_texto)
        try:
            _gravar_destino(pasta, nome, conteudo, sha)
            rotacionar(pasta)
            resultado.append(
                registro(
                    "ok", arquivo=nome, destino=str(pasta), tamanho_bytes=len(conteudo), sha256=sha
                )
            )
            log.info("Backup %s gravado em %s", nome, pasta)
        except Exception as erro:
            log.warning("Backup em %s falhou: %s", pasta, type(erro).__name__)
            resultado.append(
                registro("falha", arquivo=nome, destino=str(pasta), erro=_mensagem_erro(erro))
            )
    return resultado


# ---------------------------------------------------------------- baixar cópia


def gerar_para_download(
    cfg: Config, db: Session, admin: Usuario, ip: str | None
) -> tuple[str, bytes]:
    """Backup criptografado para a dona guardar no computador/pendrive dela.

    No modo nuvem é a proteção principal: se o servidor sumir, a cópia está com ela.
    """
    if not admin.eh_admin:
        raise SemPermissao("Só o administrador baixa backups.")
    senha = cripto.ler_senha(cfg.dir_dados)
    if not senha:
        raise ErroNegocio("Defina antes a senha do backup (Configurações → Backup).")
    db.rollback()  # não segurar o banco durante a cópia
    engine = _engine_da_sessao(db)
    conteudo = cripto.cifrar(instantaneo(cfg, engine), senha)
    nome = nome_arquivo(Gatilho.MANUAL, agora_utc()).replace("_manual", "_baixado")
    auditoria.registrar(
        db,
        acao="backup_baixado",
        entidade="backup",
        usuario_id=admin.id,
        depois={"arquivo": nome, "tamanho_bytes": len(conteudo)},
        ip=ip,
    )
    db.commit()
    return nome, conteudo


def ultimo_download(db: Session) -> datetime | None:
    consulta = select(func.max(Auditoria.criado_em)).where(Auditoria.acao == "backup_baixado")
    return db.scalar(consulta)


# ---------------------------------------------------------------- situação e alertas


@dataclass(frozen=True)
class Situacao:
    configurado: bool
    senha_definida: bool
    ultimo_ok: BackupRegistro | None
    falhas_recentes: list[BackupRegistro]
    historico: list[BackupRegistro]


def situacao(db: Session, cfg: Config) -> Situacao:
    prefs = configuracao.carregar(db, cfg)
    senha = (
        cripto.senha_no_ambiente() is not None or (cfg.dir_dados / cripto.ARQUIVO_SENHA).exists()
    )
    historico = list(
        db.scalars(select(BackupRegistro).order_by(BackupRegistro.id.desc()).limit(30))
    )
    ultimo_ok = next((r for r in historico if r.ok), None)
    # Falhas a partir do último sucesso, inclusive do mesmo lote (ex.: pendrive fora).
    desde = ultimo_ok.criado_em if ultimo_ok else None
    falhas = [r for r in historico if not r.ok and (desde is None or r.criado_em >= desde)]
    return Situacao(bool(prefs.backup_pastas) and senha, senha, ultimo_ok, falhas, historico)


def alerta(db: Session, cfg: Config) -> str | None:
    """Mensagem para a tela inicial, ou None se está tudo certo."""
    s = situacao(db, cfg)
    if cfg.modo_nuvem:
        # Na nuvem não há pastas de backup: o backup é o "Baixar backup" (a data da última
        # cópia aparece em Configurações → Backup, não na tela inicial).
        if not s.senha_definida:
            return "Defina a senha do backup em Configurações → Backup."
        return None
    if not s.configurado:
        return "O backup ainda não foi configurado. Vá em Configurações → Backup."
    if s.ultimo_ok is None:
        return "Nenhum backup foi feito ainda. Use Configurações → Fazer backup agora."
    dias = (agora_utc() - s.ultimo_ok.criado_em).days
    if dias >= ALERTA_DIAS:
        return f"O último backup foi há {dias} dias ({formatar_data_hora(s.ultimo_ok.criado_em)})."
    if s.falhas_recentes:
        falha = s.falhas_recentes[0]
        onde = f" em {falha.destino}" if falha.destino else ""
        return f"O último backup falhou{onde}: {falha.erro}"
    return None


def deve_fazer_diario(db: Session, cfg: Config) -> bool:
    """Já passou do horário e ainda não há backup diário bem-sucedido hoje.
    Em caso de falha, tenta de novo a cada hora."""
    prefs = configuracao.carregar(db, cfg)
    agora = para_local(agora_utc())
    if not prefs.backup_pastas or agora.time() < prefs.hora_backup:
        return False
    tentativas = [
        r
        for r in db.scalars(
            select(BackupRegistro)
            .where(BackupRegistro.gatilho == Gatilho.DIARIO)
            .order_by(BackupRegistro.id.desc())
            .limit(10)
        )
        if para_local(r.criado_em).date() == hoje_local()
    ]
    if any(r.ok for r in tentativas):
        return False
    return not tentativas or agora_utc() - tentativas[0].criado_em > timedelta(hours=1)


# ---------------------------------------------------------------- restauração


@dataclass(frozen=True)
class ArquivoBackup:
    caminho: Path
    momento: datetime
    tamanho_bytes: int


def listar_arquivos(db: Session, cfg: Config) -> list[ArquivoBackup]:
    arquivos = []
    for pasta_texto in configuracao.carregar(db, cfg).backup_pastas:
        pasta = Path(pasta_texto)
        if not pasta.is_dir():
            continue
        for caminho in pasta.glob(f"caixa_*{EXTENSAO}"):
            momento = data_do_arquivo(caminho.name)
            if momento:
                arquivos.append(ArquivoBackup(caminho, momento, caminho.stat().st_size))
    return sorted(arquivos, key=lambda a: a.momento, reverse=True)


def arquivo_da_lista(db: Session, cfg: Config, caminho_texto: str) -> Path:
    """Só aceita arquivos que estão na lista (evita ler caminhos arbitrários do PC)."""
    for arquivo in listar_arquivos(db, cfg):
        if str(arquivo.caminho) == caminho_texto:
            return arquivo.caminho
    raise ErroNegocio("Backup não encontrado. Atualize a lista e tente de novo.")


def validar_banco_restaurado(caminho: Path) -> None:
    try:
        problemas = _integridade(caminho)
        conexao = sqlite3.connect(caminho)
        try:
            consulta = "SELECT name FROM sqlite_master WHERE type='table'"
            tabelas = {r[0] for r in conexao.execute(consulta)}
        finally:
            conexao.close()
    except sqlite3.DatabaseError as erro:
        raise ErroNegocio("O arquivo não contém um banco do Caixa da Papelaria.") from erro
    if problemas:
        raise ErroNegocio("O backup está danificado (falhou na verificação de integridade).")
    if not {"alembic_version", "usuario", "movimento_caixa"} <= tabelas:
        raise ErroNegocio("O arquivo não contém um banco do Caixa da Papelaria.")


def _restaurar_no_postgres(engine: Engine, cfg_backup: Config) -> None:
    """Apaga e recria tudo e copia o backup — numa ÚNICA transação: se algo falhar,
    o PostgreSQL volta exatamente como estava."""
    backup_engine = criar_engine(cfg_backup)
    try:
        migrar(backup_engine)  # backup de versão anterior: atualiza a estrutura da cópia
        with backup_engine.connect() as origem, engine.connect() as destino:
            with destino.begin():
                destino.execute(text("DROP SCHEMA public CASCADE"))
                destino.execute(text("CREATE SCHEMA public"))
                migrar_na_transacao(destino)
                transferir(origem, destino)
            origem.rollback()
    finally:
        backup_engine.dispose()


def restaurar(
    cfg: Config,
    engine: Engine,
    fabrica: sessionmaker[Session],
    conteudo: bytes,
    senha_informada: str | None,
    admin: Usuario,
    ip: str | None,
) -> Path:
    """Substitui o banco atual pelo backup. Antes, guarda uma cópia do estado atual.

    Devolve o caminho da cópia de segurança local feita antes da restauração.
    """
    if not admin.eh_admin:
        raise SemPermissao("Só o administrador restaura backups.")
    senha = senha_informada or cripto.ler_senha(cfg.dir_dados)
    if not senha:
        raise ErroNegocio("Informe a senha do backup.")
    try:
        dados = cripto.decifrar(conteudo, senha)
    except cripto.BackupIlegivel as erro:
        raise ErroNegocio(str(erro)) from erro

    pasta = _pasta_temporaria(cfg)
    candidato = pasta / "caixa.db"
    candidato.write_bytes(dados)
    try:
        validar_banco_restaurado(candidato)

        # 1) Cópia local do estado atual (sempre) + backup normal nas pastas (se configurado).
        pasta_pre = cfg.dir_dados / PASTA_PRE_RESTAURACAO
        pasta_pre.mkdir(parents=True, exist_ok=True)
        copia_atual = (
            pasta_pre / f"caixa_antes_de_restaurar_{para_local(agora_utc()):%Y%m%d_%H%M%S}.db"
        )
        copia_atual.write_bytes(instantaneo(cfg, engine))
        fazer_backup(cfg, fabrica, Gatilho.PRE_RESTAURACAO, admin.id)

        # 2) Troca os dados.
        if eh_sqlite(engine):
            engine.dispose()
            copiar_banco(candidato, cfg.caminho_banco)
            # O backup pode ser de uma versão anterior do sistema: atualiza a estrutura.
            migrar(engine)
        else:
            _restaurar_no_postgres(engine, Config(dir_dados=pasta))
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

    db = fabrica()
    try:
        auditoria.registrar(
            db,
            acao="backup_restaurado",
            entidade="banco",
            usuario_id=None,  # o usuário pode não existir no banco restaurado
            depois={"restaurado_por": admin.login, "copia_anterior": copia_atual.name},
            ip=ip,
        )
        db.commit()
    finally:
        db.close()
    log.warning("Banco restaurado a partir de backup por %s", admin.login)
    return copia_atual
