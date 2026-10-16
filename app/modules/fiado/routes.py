"""Telas do fiado: busca, ficha/extrato, compra, recebimento, estorno, LGPD."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.config import Config
from app.core.datas import hoje_local
from app.core.deps import exigir_admin, ip_cliente, obter_cfg, obter_db, usuario_atual
from app.core.dinheiro import ValorInvalido, formatar_reais, ler_valor
from app.core.erros import ErroNegocio, SemPermissao
from app.core.templates import redirecionar, renderizar
from app.modules.auth import service as auth_service
from app.modules.auth.models import Usuario
from app.modules.auth.schemas import primeira_mensagem
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa.schemas import Parte
from app.modules.configuracao import service as configuracao
from app.modules.fiado import service
from app.modules.fiado.calculo import formatar_cpf, formatar_telefone
from app.modules.fiado.parcelas import MAX_PARCELAS, somar_meses
from app.modules.fiado.schemas import DadosCliente, Excedente, NovaCompra, NovoPagamento

router = APIRouter(prefix="/fiado")
CAMPOS_INTERNOS = {"csrf_token", "etapa", "admin_login", "admin_senha"}


# ---------------------------------------------------------------- leitura de formulário


def _chave(texto: object) -> UUID:
    try:
        return UUID(str(texto))
    except ValueError as erro:
        raise ErroNegocio("Formulário inválido. Recarregue a página (F5).") from erro


def _valor(texto: object, rotulo: str = "o valor", obrigatorio: bool = True) -> int | None:
    texto = str(texto or "").strip()
    if not texto and not obrigatorio:
        return None
    try:
        return ler_valor(texto)
    except ValorInvalido as erro:
        raise ErroNegocio(f"Confira {rotulo}: {erro}") from erro


def _valor_positivo(texto: object, rotulo: str = "o valor") -> int:
    valor = _valor(texto, rotulo) or 0
    if valor <= 0:
        raise ErroNegocio(f"Informe {rotulo} (maior que zero).")
    return valor


def _data(texto: object) -> date:
    try:
        return date.fromisoformat(str(texto)) if texto else hoje_local()
    except ValueError as erro:
        raise ErroNegocio("Data inválida.") from erro


def _partes(db: Session, form: FormData, prefixo: str) -> list[Parte]:
    partes = []
    for forma in caixa_repo.formas_ativas(db):
        valor = _valor(form.get(f"{prefixo}{forma.id}"), f"o valor em {forma.nome}", False)
        if valor:
            partes.append(Parte(forma_id=forma.id, valor_centavos=valor))
    return partes


def _valores(form: FormData) -> dict[str, Any]:
    return {k: v for k, v in form.items() if k not in CAMPOS_INTERNOS}


def _autorizador(
    db: Session, cfg: Config, usuario: Usuario, form: FormData, ip: str | None
) -> Usuario | None:
    """Senha do admin digitada na hora (só quando quem lança é operador e preencheu)."""
    if usuario.eh_admin or not form.get("admin_login"):
        return None
    login, senha = str(form.get("admin_login")), str(form.get("admin_senha") or "")
    return auth_service.autorizar_admin(db, cfg, login, senha, ip)


def _dados_cliente(form: FormData, usuario: Usuario) -> DadosCliente:
    limite = _valor(form.get("limite"), "o limite", False) if usuario.eh_admin else None
    dia = str(form.get("dia_pagamento") or "").strip()
    try:
        return DadosCliente(
            nome=str(form.get("nome") or ""),
            apelido=str(form.get("apelido") or ""),
            telefone=str(form.get("telefone") or ""),
            observacao=str(form.get("observacao") or ""),
            limite_centavos=limite,
            dia_pagamento=int(dia) if dia.isdigit() else None,
            consentimento=form.get("consentimento") is not None,
            cpf=str(form.get("cpf") or ""),
            endereco=str(form.get("endereco") or ""),
        )
    except ValidationError as erro:
        raise ErroNegocio(primeira_mensagem(erro)) from erro


def _campos_extras(request: Request, db: Session) -> bool:
    return configuracao.carregar(db, request.app.state.config).cliente_campos_extras


# ---------------------------------------------------------------- painel e busca


@router.get("")
def painel(
    request: Request,
    q: str = "",
    acao: str = "",
    ordem: str = "valor",
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    contexto: dict[str, Any] = {
        "q": q,
        "acao": acao,
        "ordem": ordem,
        "clientes": service.buscar_clientes(db, q) if (q or acao) else [],
        # Totais da carteira são relatório: só administrador.
        "painel": service.painel(db, ordem) if usuario.eh_admin and not acao else None,
    }
    return renderizar(request, "fiado/painel.html", contexto)


@router.get("/busca")
def busca(
    request: Request,
    q: str = "",
    acao: str = "",
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    """Resultado da busca rápida (HTMX troca só a lista)."""
    contexto = {"clientes": service.buscar_clientes(db, q), "acao": acao, "q": q}
    return renderizar(request, "fiado/_resultados.html", contexto)


# ---------------------------------------------------------------- cadastro


@router.get("/clientes/novo")
def novo_cliente_form(
    request: Request,
    acao: str = "",
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    contexto = {"valores": {}, "acao": acao, "campos_extras": _campos_extras(request, db)}
    return renderizar(request, "fiado/cliente_form.html", contexto)


@router.post("/clientes/novo")
async def novo_cliente(
    request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(obter_db)
) -> Response:
    form = await request.form()
    acao = str(form.get("acao") or "")
    try:
        cliente = service.criar_cliente(
            db, usuario, _dados_cliente(form, usuario), ip_cliente(request)
        )
    except ErroNegocio as erro:
        contexto = {"valores": _valores(form), "acao": acao, "erro": erro.mensagem}
        return renderizar(request, "fiado/cliente_form.html", contexto, status=400)
    destino = f"/fiado/clientes/{cliente.id}"
    if acao in ("compra", "receber"):
        destino += f"/{acao}"
    return redirecionar(request, destino, f"Cliente {cliente.nome} cadastrado.")


@router.get("/clientes/{cliente_id}/editar")
def editar_cliente_form(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    cliente = service.obter_cliente(db, cliente_id)
    valores = {
        "nome": cliente.nome,
        "apelido": cliente.apelido or "",
        "telefone": formatar_telefone(cliente.telefone),
        "observacao": cliente.observacao or "",
        "limite": formatar_reais(cliente.limite_centavos).removeprefix("R$ ")
        if cliente.limite_centavos is not None
        else "",
        "dia_pagamento": cliente.dia_pagamento or "",
        "ativo": cliente.ativo,
        "cpf": formatar_cpf(cliente.cpf),
        "endereco": cliente.endereco or "",
    }
    contexto = {
        "valores": valores,
        "cliente": cliente,
        "campos_extras": _campos_extras(request, db),
    }
    return renderizar(request, "fiado/cliente_form.html", contexto)


@router.post("/clientes/{cliente_id}/editar")
async def editar_cliente(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    form = await request.form()
    extras = _campos_extras(request, db)
    try:
        dados = _dados_cliente(form, usuario)
        ativo = form.get("ativo") is not None
        service.editar_cliente(db, usuario, cliente_id, dados, ativo, ip_cliente(request), extras)
    except ErroNegocio as erro:
        db.rollback()
        cliente = service.obter_cliente(db, cliente_id)
        contexto = {
            "valores": _valores(form),
            "cliente": cliente,
            "erro": erro.mensagem,
            "campos_extras": extras,
        }
        return renderizar(request, "fiado/cliente_form.html", contexto, status=400)
    return redirecionar(request, f"/fiado/clientes/{cliente_id}", "Cadastro atualizado.")


# ---------------------------------------------------------------- ficha / extrato


@router.get("/clientes/{cliente_id}")
def ficha(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    ext = service.extrato(db, cliente_id, cfg.nome_loja)
    contexto = {"e": ext, "telefone": formatar_telefone(ext.cliente.telefone)}
    return renderizar(request, "fiado/ficha.html", contexto)


# ---------------------------------------------------------------- compra


def _condicoes(request: Request, db: Session) -> service.CondicoesJuros:
    """Taxa e carência atuais (Configurações) — gravadas nas parcelas das compras novas."""
    prefs = configuracao.carregar(db, request.app.state.config)
    return service.CondicoesJuros(prefs.juros_taxa_ppm, prefs.juros_carencia_dias)


def _contexto_compra(
    request: Request, db: Session, cliente_id: int, valores: dict[str, Any]
) -> dict[str, Any]:
    hoje = hoje_local()
    cliente = service.obter_cliente(db, cliente_id)
    return {
        "cliente": cliente,
        "devido": service.total_devido(db, cliente),
        "formas": caixa_repo.formas_ativas(db),
        "valores": valores,
        "chave": valores.get("chave") or str(uuid4()),
        "hoje": hoje.isoformat(),
        "vencimento_sugerido": somar_meses(hoje, 1).isoformat(),
        "condicoes": _condicoes(request, db),
        "max_parcelas": MAX_PARCELAS,
    }


def _ler_compra(db: Session, cliente_id: int, form: FormData) -> NovaCompra:
    try:
        return NovaCompra(
            cliente_id=cliente_id,
            valor_total_centavos=_valor_positivo(form.get("total"), "o valor total"),
            partes_pagas=_partes(db, form, "pago_"),
            descricao=str(form.get("descricao") or ""),
            data_referencia=_data(form.get("data")),
            chave=_chave(form.get("chave")),
            confirmar_valor_alto=form.get("confirmar_valor_alto") is not None,
            parcelas=_inteiro(form.get("parcelas")),
            primeiro_vencimento=_data(form.get("vencimento")) if form.get("vencimento") else None,
        )
    except ValidationError as erro:
        raise ErroNegocio(primeira_mensagem(erro)) from erro


def _inteiro(texto: object) -> int:
    valor = str(texto or "0").strip()
    if not valor.isdigit():
        raise ErroNegocio("Quantidade de parcelas inválida.")
    return int(valor)


@router.get("/clientes/{cliente_id}/compra")
def compra_form(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    return renderizar(request, "fiado/compra.html", _contexto_compra(request, db, cliente_id, {}))


@router.post("/clientes/{cliente_id}/compra")
async def compra(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    form = await request.form()
    valores = _valores(form)
    etapa = form.get("etapa")
    ip = ip_cliente(request)
    if etapa == "corrigir":
        contexto = _contexto_compra(request, db, cliente_id, valores)
        return renderizar(request, "fiado/compra.html", contexto)
    try:
        dados = _ler_compra(db, cliente_id, form)
        if etapa == "gravar":
            autorizador = _autorizador(db, cfg, usuario, form, ip)
            confirmar = form.get("confirmar_limite") is not None
            lanc, repetido = service.anotar_compra(
                db, usuario, dados, autorizador, confirmar, ip, _condicoes(request, db)
            )
            aviso = f"Fiado anotado: {formatar_reais(lanc.valor_centavos)}."
            if repetido:
                aviso = "Esta compra já tinha sido anotada (envio repetido ignorado)."
            return redirecionar(request, f"/fiado/clientes/{cliente_id}", aviso)
        previa = service.revisar_compra(db, dados, _condicoes(request, db))
    except ErroNegocio as erro:
        db.rollback()
        if etapa == "gravar":
            # Erro na confirmação (ex.: senha do admin): volta à confirmação.
            try:
                previa = service.revisar_compra(
                    db, _ler_compra(db, cliente_id, form), _condicoes(request, db)
                )
                contexto = {"previa": previa, "valores": valores, "erro": erro.mensagem}
                return renderizar(request, "fiado/compra_confirmar.html", contexto, status=400)
            except ErroNegocio:
                pass
        contexto = _contexto_compra(request, db, cliente_id, valores) | {"erro": erro.mensagem}
        return renderizar(request, "fiado/compra.html", contexto, status=400)
    return renderizar(
        request, "fiado/compra_confirmar.html", {"previa": previa, "valores": valores}
    )


# ---------------------------------------------------------------- receber


def _contexto_receber(db: Session, cliente_id: int, valores: dict[str, Any]) -> dict[str, Any]:
    cliente = service.obter_cliente(db, cliente_id)
    return {
        "cliente": cliente,
        "devido": service.total_devido(db, cliente),
        "formas": caixa_repo.formas_ativas(db),
        "valores": valores,
        "chave": valores.get("chave") or str(uuid4()),
    }


def _ler_pagamento(db: Session, cliente_id: int, form: FormData) -> NovoPagamento:
    partes = _partes(db, form, "parte_")
    if not partes:
        raise ErroNegocio("Informe o valor recebido em pelo menos uma forma de pagamento.")
    excedente = str(form.get("excedente") or "")
    try:
        return NovoPagamento(
            cliente_id=cliente_id,
            partes=partes,
            excedente=Excedente(excedente) if excedente in ("troco", "credito") else None,
            chave=_chave(form.get("chave")),
            confirmar_valor_alto=form.get("confirmar_valor_alto") is not None,
        )
    except ValidationError as erro:
        raise ErroNegocio(primeira_mensagem(erro)) from erro


@router.get("/clientes/{cliente_id}/receber")
def receber_form(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    return renderizar(request, "fiado/receber.html", _contexto_receber(db, cliente_id, {}))


@router.post("/clientes/{cliente_id}/receber")
async def receber(
    request: Request,
    cliente_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    form = await request.form()
    valores = _valores(form)
    etapa = form.get("etapa")
    if etapa == "corrigir":
        return renderizar(request, "fiado/receber.html", _contexto_receber(db, cliente_id, valores))
    try:
        dados = _ler_pagamento(db, cliente_id, form)
        if etapa == "gravar":
            lanc, repetido = service.receber_pagamento(db, usuario, dados, ip_cliente(request))
            aviso = f"Pagamento de {formatar_reais(lanc.valor_centavos)} recebido."
            if repetido:
                aviso = "Este pagamento já tinha sido registrado (envio repetido ignorado)."
            return redirecionar(request, f"/fiado/clientes/{cliente_id}", aviso)
        previa = service.revisar_pagamento(db, dados)
    except ErroNegocio as erro:
        db.rollback()
        contexto = _contexto_receber(db, cliente_id, valores) | {"erro": erro.mensagem}
        return renderizar(request, "fiado/receber.html", contexto, status=400)
    return renderizar(
        request, "fiado/receber_confirmar.html", {"previa": previa, "valores": valores}
    )


# ---------------------------------------------------------------- operações simples


def _form_simples(
    request: Request, db: Session, cliente_id: int, tipo: str, **extra: Any
) -> Response:
    cliente = service.obter_cliente(db, cliente_id)
    contexto = {
        "cliente": cliente,
        "devido": service.total_devido(db, cliente),
        "tipo": tipo,
        "chave": str(uuid4()),
        "hoje": hoje_local().isoformat(),
        "valores": {},
    } | extra
    status = 400 if "erro" in extra else 200
    return renderizar(request, "fiado/operacao.html", contexto, status=status)


@router.get("/clientes/{cliente_id}/operacao/{tipo}")
def operacao_form(
    request: Request,
    cliente_id: int,
    tipo: str,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    if tipo not in ("abater", "saldo-inicial", "devolver"):
        raise ErroNegocio("Página não encontrada.")
    if tipo != "devolver" and not usuario.eh_admin:
        raise SemPermissao("Só o administrador pode fazer isso.")
    return _form_simples(request, db, cliente_id, tipo)


@router.post("/clientes/{cliente_id}/operacao/{tipo}")
async def operacao(
    request: Request,
    cliente_id: int,
    tipo: str,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    form = await request.form()
    ip = ip_cliente(request)
    try:
        valor = _valor_positivo(form.get("valor"))
        chave = _chave(form.get("chave"))
        if tipo == "abater":
            lanc, _ = service.abater(
                db, usuario, cliente_id, valor, str(form.get("motivo") or ""), chave, ip
            )
        elif tipo == "saldo-inicial":
            lanc, _ = service.lancar_saldo_inicial(
                db,
                usuario,
                cliente_id,
                valor,
                _data(form.get("data")),
                str(form.get("descricao") or ""),
                chave,
                ip,
            )
        elif tipo == "devolver":
            lanc, _ = service.devolver_credito(db, usuario, cliente_id, valor, chave, ip)
        else:
            raise ErroNegocio("Página não encontrada.")
    except ErroNegocio as erro:
        db.rollback()
        return _form_simples(
            request,
            db,
            cliente_id,
            tipo,
            erro=erro.mensagem,
            valores=_valores(form),
            chave=str(form.get("chave") or uuid4()),
        )
    aviso = f"{lanc.rotulo} de {formatar_reais(lanc.valor_centavos)} registrado."
    return redirecionar(request, f"/fiado/clientes/{cliente_id}", aviso)


# ---------------------------------------------------------------- estorno


@router.get("/lancamentos/{lancamento_id}/estornar")
def estorno_form(
    request: Request,
    lancamento_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
) -> Response:
    original = service.obter_lancamento(db, lancamento_id)
    service.validar_estornavel(db, original)
    contexto = {"original": original, "chave": str(uuid4())}
    return renderizar(request, "fiado/estorno.html", contexto)


@router.post("/lancamentos/{lancamento_id}/estornar")
async def estorno(
    request: Request,
    lancamento_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(obter_db),
    cfg: Config = Depends(obter_cfg),
) -> Response:
    form = await request.form()
    ip = ip_cliente(request)
    motivo = str(form.get("motivo") or "")
    try:
        autorizador = _autorizador(db, cfg, usuario, form, ip)
        lanc, _ = service.estornar(
            db, usuario, lancamento_id, motivo, _chave(form.get("chave")), autorizador, ip
        )
    except ErroNegocio as erro:
        db.rollback()
        original = service.obter_lancamento(db, lancamento_id)
        contexto = {
            "original": original,
            "chave": str(form.get("chave") or uuid4()),
            "motivo": motivo,
            "erro": erro.mensagem,
        }
        return renderizar(request, "fiado/estorno.html", contexto, status=400)
    aviso = f"Estorno de {formatar_reais(lanc.valor_centavos)} registrado."
    return redirecionar(request, f"/fiado/clientes/{lanc.cliente_id}", aviso)


# ---------------------------------------------------------------- LGPD (admin)


@router.post("/clientes/{cliente_id}/anonimizar")
def anonimizar(
    request: Request,
    cliente_id: int,
    confirmacao: str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    if confirmacao.strip().upper() != "ANONIMIZAR":
        return redirecionar(
            request,
            f"/fiado/clientes/{cliente_id}",
            "Para anonimizar, digite ANONIMIZAR no campo de confirmação.",
            erro=True,
        )
    try:
        service.anonimizar(db, admin, cliente_id, ip_cliente(request))
    except ErroNegocio as erro:
        return redirecionar(request, f"/fiado/clientes/{cliente_id}", erro.mensagem, erro=True)
    return redirecionar(request, f"/fiado/clientes/{cliente_id}", "Dados pessoais anonimizados.")


@router.get("/clientes/{cliente_id}/exportar")
def exportar(
    request: Request,
    cliente_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: Session = Depends(obter_db),
) -> Response:
    dados = service.exportar_dados(db, admin, cliente_id, ip_cliente(request))
    corpo = json.dumps(dados, ensure_ascii=False, indent=2)
    return Response(
        corpo,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="cliente-{cliente_id}.json"'},
    )
