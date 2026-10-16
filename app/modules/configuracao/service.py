"""Configurações da loja: nome, sessão, campos de cliente, backup, formas e categorias,
e o endereço/QR Code para abrir o sistema no celular."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import uuid
from dataclasses import dataclass, replace
from datetime import time
from pathlib import Path

import segno
from sqlalchemy.orm import Session

from app.core import auditoria
from app.core.config import Config
from app.core.datas import agora_utc
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa.models import CATEGORIA_DEVOLUCAO_FIADO, CategoriaDespesa, FormaPagamento
from app.modules.configuracao import repository as repo
from app.modules.fiado.parcelas import percentual_para_ppm

MAX_PASTAS_BACKUP = 3
# Juros por atraso de parcela (fiado parcelado). PADRÃO PROVISÓRIO: 0,033% ao dia
# (~1% ao mês, juros simples) — a taxa definitiva deve ser validada com a contabilidade
# (CDC art. 52; Código Civil art. 406; Decreto 22.626/1933).
JUROS_TAXA_PADRAO_PPM = 330
JUROS_CARENCIA_PADRAO = 10
_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Preferencias:
    nome_loja: str
    sessao_minutos: int
    cliente_campos_extras: bool
    backup_pastas: tuple[str, ...]
    backup_hora: str  # "HH:MM", horário da loja
    juros_taxa_ppm: int = JUROS_TAXA_PADRAO_PPM  # por dia (330 = 0,033% ao dia)
    juros_carencia_dias: int = JUROS_CARENCIA_PADRAO

    @property
    def hora_backup(self) -> time:
        horas, minutos = self.backup_hora.split(":")
        return time(int(horas), int(minutos))


def carregar(db: Session, base: Config) -> Preferencias:
    """Valores gravados pela dona; o que não foi gravado usa o padrão da instalação."""
    valores = repo.todas(db)
    return Preferencias(
        nome_loja=valores.get("nome_loja", base.nome_loja),
        sessao_minutos=int(valores.get("sessao_minutos", base.sessao_minutos)),
        cliente_campos_extras=valores.get("cliente_campos_extras") == "1",
        backup_pastas=_pastas_backup(valores, base),
        backup_hora=valores.get("backup_hora", "21:00"),
        juros_taxa_ppm=int(valores.get("juros_taxa_ppm", JUROS_TAXA_PADRAO_PPM)),
        juros_carencia_dias=int(valores.get("juros_carencia_dias", JUROS_CARENCIA_PADRAO)),
    )


def _pastas_backup(valores: dict[str, str], base: Config) -> tuple[str, ...]:
    """Pastas gravadas pela dona; sem nenhuma, a pasta padrão do servidor (modo nuvem)."""
    if "backup_pastas" in valores:
        return tuple(json.loads(valores["backup_pastas"]))
    return (base.pasta_backup_padrao,) if base.pasta_backup_padrao else ()


def config_efetiva(db: Session, base: Config) -> Config:
    """Config da instalação com os ajustes feitos na tela de configurações."""
    prefs = carregar(db, base)
    return replace(base, nome_loja=prefs.nome_loja, sessao_minutos=prefs.sessao_minutos)


def _exigir_admin(usuario: Usuario) -> None:
    if not usuario.eh_admin:
        raise SemPermissao("Só o administrador altera as configurações.")


def _gravar(db: Session, usuario: Usuario, valores: dict[str, str], ip: str | None) -> None:
    antes = repo.todas(db)
    agora = agora_utc()
    for chave, valor in valores.items():
        repo.gravar(db, chave, valor, usuario.id, agora)
    auditoria.registrar(
        db,
        acao="configuracao_alterada",
        entidade="configuracao",
        usuario_id=usuario.id,
        antes={k: antes.get(k) for k in valores},
        depois=valores,
        ip=ip,
    )
    db.commit()


# ---------------------------------------------------------------- gerais


def salvar_gerais(
    db: Session,
    usuario: Usuario,
    nome_loja: str,
    sessao_minutos: int,
    campos_extras: bool,
    ip: str | None,
) -> None:
    _exigir_admin(usuario)
    nome = " ".join(nome_loja.split())
    if not 2 <= len(nome) <= 80:
        raise ErroNegocio("O nome da loja deve ter entre 2 e 80 caracteres.")
    if not 5 <= sessao_minutos <= 480:
        raise ErroNegocio("O tempo de sessão deve ficar entre 5 e 480 minutos.")
    _gravar(
        db,
        usuario,
        {
            "nome_loja": nome,
            "sessao_minutos": str(sessao_minutos),
            "cliente_campos_extras": "1" if campos_extras else "0",
        },
        ip,
    )


# ---------------------------------------------------------------- juros do fiado


def salvar_juros(
    db: Session, usuario: Usuario, taxa_texto: str, carencia_texto: str, ip: str | None
) -> None:
    """Vale para as compras parceladas NOVAS (as antigas guardam a taxa do dia da compra)."""
    _exigir_admin(usuario)
    try:
        ppm = percentual_para_ppm(taxa_texto)
    except ValueError as erro:
        raise ErroNegocio(str(erro)) from erro
    carencia = carencia_texto.strip()
    if not carencia.isdigit() or not 0 <= int(carencia) <= 60:
        raise ErroNegocio("A carência deve ser de 0 a 60 dias.")
    _gravar(
        db, usuario, {"juros_taxa_ppm": str(ppm), "juros_carencia_dias": str(int(carencia))}, ip
    )


# ---------------------------------------------------------------- backup


def validar_pasta(texto: str) -> str:
    """A pasta precisa existir e aceitar gravação (teste com arquivo temporário)."""
    caminho = Path(texto.strip().strip('"'))
    if not caminho.is_absolute():
        raise ErroNegocio(f"Use o caminho completo da pasta (ex.: D:\\Backup). Recebido: {texto}")
    if not caminho.is_dir():
        raise ErroNegocio(f"A pasta não existe ou não está acessível: {caminho}")
    teste = caminho / f".teste-caixa-{uuid.uuid4().hex}"
    try:
        teste.write_bytes(b"ok")
        teste.unlink()
    except OSError as erro:
        raise ErroNegocio(f"Sem permissão para gravar na pasta: {caminho}") from erro
    return str(caminho)


def salvar_backup(
    db: Session, usuario: Usuario, pastas_texto: str, hora: str, ip: str | None
) -> None:
    _exigir_admin(usuario)
    linhas = [linha for linha in pastas_texto.splitlines() if linha.strip()]
    if len(linhas) > MAX_PASTAS_BACKUP:
        raise ErroNegocio(f"Informe no máximo {MAX_PASTAS_BACKUP} pastas.")
    pastas = list(dict.fromkeys(validar_pasta(linha) for linha in linhas))
    if not _HORA.match(hora.strip()):
        raise ErroNegocio("Informe o horário no formato HH:MM (ex.: 21:00).")
    _gravar(
        db,
        usuario,
        {"backup_pastas": json.dumps(pastas, ensure_ascii=False), "backup_hora": hora.strip()},
        ip,
    )


# ---------------------------------------------------------------- formas e categorias


def listar_formas(db: Session) -> list[FormaPagamento]:
    return repo.formas(db)


def listar_categorias(db: Session) -> list[CategoriaDespesa]:
    return [c for c in repo.categorias(db) if c.nome != CATEGORIA_DEVOLUCAO_FIADO]


def _nome_cadastro(nome: str) -> str:
    nome = " ".join(nome.split())
    if not 2 <= len(nome) <= 40:
        raise ErroNegocio("O nome deve ter entre 2 e 40 caracteres.")
    return nome


def criar_forma(db: Session, usuario: Usuario, nome: str, ip: str | None) -> FormaPagamento:
    """Novas formas NÃO são dinheiro físico (não entram na gaveta), ex.: vale, transferência."""
    _exigir_admin(usuario)
    nome = _nome_cadastro(nome)
    if repo.forma_por_nome(db, nome):
        raise ErroNegocio("Já existe uma forma de pagamento com esse nome.")
    ordem = max((f.ordem for f in repo.formas(db)), default=0) + 1
    forma = FormaPagamento(nome=nome, afeta_gaveta=False, ativo=True, ordem=ordem)
    db.add(forma)
    db.flush()
    auditoria.registrar(
        db,
        acao="forma_criada",
        entidade="forma_pagamento",
        entidade_id=forma.id,
        usuario_id=usuario.id,
        depois={"nome": nome},
        ip=ip,
    )
    db.commit()
    return forma


def alternar_forma(db: Session, usuario: Usuario, forma_id: int, ip: str | None) -> FormaPagamento:
    _exigir_admin(usuario)
    forma = db.get(FormaPagamento, forma_id)
    if forma is None:
        raise ErroNegocio("Forma de pagamento não encontrada.")
    if forma.afeta_gaveta and forma.ativo:
        raise ErroNegocio("A forma Dinheiro não pode ser desativada (é ela que conta a gaveta).")
    forma.ativo = not forma.ativo
    auditoria.registrar(
        db,
        acao="forma_alterada",
        entidade="forma_pagamento",
        entidade_id=forma.id,
        usuario_id=usuario.id,
        depois={"ativo": forma.ativo},
        ip=ip,
    )
    db.commit()
    return forma


def criar_categoria(db: Session, usuario: Usuario, nome: str, ip: str | None) -> CategoriaDespesa:
    _exigir_admin(usuario)
    nome = _nome_cadastro(nome)
    if repo.categoria_por_nome(db, nome):
        raise ErroNegocio("Já existe uma categoria com esse nome.")
    categoria = CategoriaDespesa(nome=nome, ativo=True)
    db.add(categoria)
    db.flush()
    auditoria.registrar(
        db,
        acao="categoria_criada",
        entidade="categoria_despesa",
        entidade_id=categoria.id,
        usuario_id=usuario.id,
        depois={"nome": nome},
        ip=ip,
    )
    db.commit()
    return categoria


def alternar_categoria(
    db: Session, usuario: Usuario, categoria_id: int, ip: str | None
) -> CategoriaDespesa:
    _exigir_admin(usuario)
    categoria = db.get(CategoriaDespesa, categoria_id)
    if categoria is None or categoria.nome == CATEGORIA_DEVOLUCAO_FIADO:
        raise ErroNegocio("Categoria não encontrada.")
    categoria.ativo = not categoria.ativo
    auditoria.registrar(
        db,
        acao="categoria_alterada",
        entidade="categoria_despesa",
        entidade_id=categoria.id,
        usuario_id=usuario.id,
        depois={"ativo": categoria.ativo},
        ip=ip,
    )
    db.commit()
    return categoria


# ---------------------------------------------------------------- acesso pelo celular


# Faixa usada pela Tailscale (rede privada criptografada para acesso de fora da loja).
FAIXA_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def _ip_privado(ip: str) -> bool:
    return ip.startswith(("192.168.", "10.")) or bool(re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip))


def _ip_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in FAIXA_TAILSCALE
    except ValueError:
        return False


def _ips_deste_pc() -> set[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    try:
        # Não envia nada: só pergunta ao sistema qual placa de rede seria usada.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as teste:
            teste.connect(("10.255.255.255", 1))
            ips.add(teste.getsockname()[0])
    except OSError:
        pass
    return ips


def enderecos_locais(porta: int) -> list[str]:
    """Endereços deste PC na rede da loja (Wi-Fi/cabo). Funciona sem internet."""
    return [f"http://{ip}:{porta}" for ip in sorted(filter(_ip_privado, _ips_deste_pc()))]


def enderecos_remotos(porta: int) -> list[str]:
    """Endereços pela Tailscale (acesso de fora da loja, só para aparelhos autorizados)."""
    return [f"http://{ip}:{porta}" for ip in sorted(filter(_ip_tailscale, _ips_deste_pc()))]


def qr_code(url: str) -> str:
    """QR Code como imagem SVG embutida (data URI)."""
    return str(segno.make(url, error="m").svg_data_uri(scale=6, border=2))
