"""Scripts de operação (rodam como na loja: processo separado, sem o sistema aberto)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Config
from app.modules.auth import service as auth
from app.modules.auth.models import Usuario

RAIZ = Path(__file__).resolve().parents[2]


def rodar(
    script: str, cfg: Config, entrada: str = "", **env: str
) -> subprocess.CompletedProcess[str]:
    ambiente = os.environ | {"CAIXA_DIR_DADOS": str(cfg.dir_dados), "PYTHONIOENCODING": "utf-8"}
    ambiente.pop("CAIXA_MODO_DEV", None)
    ambiente |= env
    return subprocess.run(  # noqa: S603
        [sys.executable, str(RAIZ / "scripts" / script)],
        input=entrada,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=ambiente,
        timeout=120,
        check=False,
    )


def test_migrar(cfg: Config) -> None:
    resultado = rodar("migrar.py", cfg)
    assert resultado.returncode == 0 and "banco pronto" in resultado.stdout


def test_recuperar_senha_do_admin(db: Session, cfg: Config, admin: Usuario) -> None:
    resultado = rodar("recuperar_admin.py", cfg, entrada="maria\n")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    senha = re.search(r"Senha provisória de 'maria':\s+(\w+)", resultado.stdout)
    assert senha is not None
    db.expire_all()
    usuario = auth.autenticar(db, cfg, "maria", senha.group(1), None)
    assert usuario.trocar_senha  # obrigado a criar senha nova


def test_recuperar_promove_quando_nao_ha_admin_ativo(
    db: Session, cfg: Config, admin: Usuario, operador: Usuario
) -> None:
    # Situação extrema: o único admin foi desativado direto no banco.
    admin.ativo = False
    db.commit()
    resultado = rodar("recuperar_admin.py", cfg, entrada="joao\n")
    assert "Nenhum administrador ativo" in resultado.stdout and resultado.returncode == 0
    db.expire_all()
    assert db.get(Usuario, operador.id).eh_admin  # type: ignore[union-attr]


def test_recuperar_usuario_inexistente_nao_altera(cfg: Config, admin: Usuario) -> None:
    resultado = rodar("recuperar_admin.py", cfg, entrada="ninguem\n")
    assert resultado.returncode == 1 and "Nada foi alterado" in resultado.stdout


def test_backup_manual_sem_configuracao(cfg: Config, admin: Usuario) -> None:
    resultado = rodar("backup_manual.py", cfg)
    assert resultado.returncode == 1 and "não configurado" in resultado.stdout


def test_dados_de_exemplo_so_em_modo_dev(cfg: Config, tmp_path: Path) -> None:
    assert "Recusado" in rodar("dados_exemplo.py", cfg).stdout
    treino = Config(dir_dados=tmp_path / "treino")
    resultado = rodar("dados_exemplo.py", treino, CAIXA_MODO_DEV="1")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert (
        "Recusado: este banco já tem dados"
        in rodar("dados_exemplo.py", treino, CAIXA_MODO_DEV="1").stdout
    )


def test_dados_de_exemplo_recusam_banco_com_dados(cfg: Config, admin: Usuario) -> None:
    resultado = rodar("dados_exemplo.py", cfg, CAIXA_MODO_DEV="1")
    assert resultado.returncode == 1 and "já tem dados" in resultado.stdout
