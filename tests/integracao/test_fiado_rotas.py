"""Fluxo do fiado pelas telas: cadastro, busca, compra, recebimento, estorno, LGPD."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.modules.auth.models import Usuario
from tests.conftest import SENHA_ADMIN, SENHA_OPERADOR, entrar, postar


def _chave(html: str) -> str:
    achado = re.search(r'name="chave" value="([0-9a-f-]{36})"', html)
    assert achado, "formulário sem chave"
    return achado.group(1)


def _campos(html: str, prefixo: str) -> list[str]:
    return re.findall(rf'name="({prefixo}\d+)"', html)


def _abrir_caixa(cliente: TestClient, fundo: str = "0") -> None:
    postar(cliente, "/caixa/abrir", {"fundo": fundo, "chave": _chave(cliente.get("/").text)})
    cliente.get("/")  # consome o aviso


def _cadastrar(cliente: TestClient, nome: str = "Sebastião Lima", **extra: str) -> int:
    dados = {"nome": nome, "apelido": "Tião", "telefone": "(11) 98888-7777", "consentimento": "1"}
    resposta = postar(cliente, "/fiado/clientes/novo", dados | extra)
    assert resposta.status_code == 303, resposta.text
    return int(resposta.headers["location"].split("/")[3])


def _comprar(cliente: TestClient, cliente_id: int, total: str, **extra: str):  # type: ignore[no-untyped-def]
    form = cliente.get(f"/fiado/clientes/{cliente_id}/compra").text
    dados = {"total": total, "descricao": "caderno", "chave": _chave(form)} | extra
    return postar(cliente, f"/fiado/clientes/{cliente_id}/compra", dados | {"etapa": "gravar"})


def test_cadastro_exige_consentimento(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    resposta = postar(cliente, "/fiado/clientes/novo", {"nome": "Ana Souza"})
    assert resposta.status_code == 400 and "autorizou" in resposta.text
    resposta = postar(
        cliente,
        "/fiado/clientes/novo",
        {"nome": "Ana Souza", "telefone": "123", "consentimento": "1"},
    )
    assert resposta.status_code == 400 and "Telefone inválido" in resposta.text


def test_busca_rapida_htmx(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _cadastrar(cliente)
    resultado = cliente.get("/fiado/busca", params={"q": "tiao", "acao": "compra"})
    assert "Sebastião Lima" in resultado.text and "Anotar" in resultado.text
    assert "Receber" not in resultado.text
    assert "Nenhum cliente" in cliente.get("/fiado/busca", params={"q": "zzz"}).text


def test_compra_com_confirmacao_e_extrato(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir_caixa(cliente)
    cid = _cadastrar(cliente)
    form = cliente.get(f"/fiado/clientes/{cid}/compra").text
    dados = {"total": "50,00", "descricao": "material escolar", "chave": _chave(form)}
    dinheiro = _campos(form, "pago_")[0]

    revisao = postar(cliente, f"/fiado/clientes/{cid}/compra", dados | {dinheiro: "20,00"})
    assert "Confira antes de gravar" in revisao.text
    assert "R$ 30,00" in revisao.text  # vai para o fiado

    for _ in range(2):  # duplo clique
        gravado = postar(
            cliente, f"/fiado/clientes/{cid}/compra", dados | {dinheiro: "20,00", "etapa": "gravar"}
        )
        assert gravado.status_code == 303
    ficha = cliente.get(f"/fiado/clientes/{cid}").text
    assert "envio repetido ignorado" in ficha
    assert "Deve R$ 30,00" in ficha
    assert "https://wa.me/5511988887777?text=" in ficha
    assert ficha.count("material escolar") == 1


def test_limite_exige_senha_do_admin(
    cliente: TestClient, operador: Usuario, admin: Usuario
) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    _abrir_caixa(cliente)
    cid = _cadastrar(cliente, limite="10,00")
    postar(cliente, "/sair")
    entrar(cliente, "joao", SENHA_OPERADOR)

    form = cliente.get(f"/fiado/clientes/{cid}/compra").text
    dados = {"total": "15,00", "chave": _chave(form)}
    revisao = postar(cliente, f"/fiado/clientes/{cid}/compra", dados)
    assert "Passa do limite" in revisao.text and "admin_senha" in revisao.text

    negado = postar(cliente, f"/fiado/clientes/{cid}/compra", dados | {"etapa": "gravar"})
    assert negado.status_code == 400 and "senha do administrador" in negado.text

    ok = postar(
        cliente,
        f"/fiado/clientes/{cid}/compra",
        dados | {"etapa": "gravar", "admin_login": "maria", "admin_senha": SENHA_ADMIN},
    )
    assert ok.status_code == 303


def test_recebimento_com_troco(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir_caixa(cliente)
    cid = _cadastrar(cliente)
    _comprar(cliente, cid, "30,00")

    form = cliente.get(f"/fiado/clientes/{cid}/receber").text
    dados = {_campos(form, "parte_")[0]: "50,00", "chave": _chave(form)}
    pergunta = postar(cliente, f"/fiado/clientes/{cid}/receber", dados)
    assert "O que fazer?" in pergunta.text and 'value="troco"' in pergunta.text

    confirmacao = postar(cliente, f"/fiado/clientes/{cid}/receber", dados | {"excedente": "troco"})
    assert "Troco a devolver" in confirmacao.text and "R$ 20,00" in confirmacao.text

    gravado = postar(
        cliente,
        f"/fiado/clientes/{cid}/receber",
        dados | {"excedente": "troco", "etapa": "gravar"},
    )
    assert gravado.status_code == 303
    assert "Em dia" in cliente.get(f"/fiado/clientes/{cid}").text
    inicio = cliente.get("/").text
    assert "Recebimento de fiado" in inicio and "Ver no fiado" in inicio


def test_estorno_de_pagamento_pela_ficha(
    cliente: TestClient, operador: Usuario, admin: Usuario
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir_caixa(cliente)
    cid = _cadastrar(cliente)
    _comprar(cliente, cid, "30,00")
    form = cliente.get(f"/fiado/clientes/{cid}/receber").text
    postar(
        cliente,
        f"/fiado/clientes/{cid}/receber",
        {_campos(form, "parte_")[1]: "10,00", "chave": _chave(form), "etapa": "gravar"},
    )
    ficha = cliente.get(f"/fiado/clientes/{cid}").text
    lanc_id = re.findall(r"/fiado/lancamentos/(\d+)/estornar", ficha)[-1]

    form = cliente.get(f"/fiado/lancamentos/{lanc_id}/estornar").text
    assert "o caixa também será estornado" in form
    resposta = postar(
        cliente,
        f"/fiado/lancamentos/{lanc_id}/estornar",
        {
            "motivo": "valor errado",
            "chave": _chave(form),
            "admin_login": "maria",
            "admin_senha": SENHA_ADMIN,
        },
    )
    assert resposta.status_code == 303
    assert "Deve R$ 30,00" in cliente.get(f"/fiado/clientes/{cid}").text


def test_operador_nao_ve_totais_nem_operacoes_de_admin(
    cliente: TestClient, operador: Usuario
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    cid = _cadastrar(cliente)
    assert "Total a receber" not in cliente.get("/fiado").text
    assert cliente.get(f"/fiado/clientes/{cid}/operacao/abater").status_code == 403
    assert cliente.get(f"/fiado/clientes/{cid}/exportar").status_code == 403
    assert (
        postar(
            cliente, f"/fiado/clientes/{cid}/anonimizar", {"confirmacao": "ANONIMIZAR"}
        ).status_code
        == 403
    )


def test_admin_saldo_inicial_painel_exportar_e_anonimizar(
    cliente: TestClient, admin: Usuario
) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    cid = _cadastrar(cliente)
    form = cliente.get(f"/fiado/clientes/{cid}/operacao/saldo-inicial").text
    resposta = postar(
        cliente,
        f"/fiado/clientes/{cid}/operacao/saldo-inicial",
        {"valor": "45,00", "data": "2026-06-01", "chave": _chave(form)},
    )
    assert resposta.status_code == 303
    painel = cliente.get("/fiado").text
    assert "Total a receber" in painel and "R$ 45,00" in painel and "90+ dias" in painel

    exportado = cliente.get(f"/fiado/clientes/{cid}/exportar")
    assert exportado.headers["content-type"].startswith("application/json")
    assert exportado.json()["cliente"]["telefone"] == "11988887777"

    form = cliente.get(f"/fiado/clientes/{cid}/operacao/abater").text
    postar(
        cliente,
        f"/fiado/clientes/{cid}/operacao/abater",
        {"valor": "45,00", "motivo": "perdoado", "chave": _chave(form)},
    )
    sem_confirmar = postar(cliente, f"/fiado/clientes/{cid}/anonimizar", {"confirmacao": "sim"})
    assert sem_confirmar.status_code == 303
    assert "digite ANONIMIZAR" in cliente.get(f"/fiado/clientes/{cid}").text

    postar(cliente, f"/fiado/clientes/{cid}/anonimizar", {"confirmacao": "anonimizar"})
    ficha = cliente.get(f"/fiado/clientes/{cid}").text
    assert f"Cliente anonimizado #{cid}" in ficha and "Sebastião" not in ficha
    assert "wa.me" not in ficha


def test_editar_cliente(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    cid = _cadastrar(cliente)
    assert 'value="(11) 98888-7777"' in cliente.get(f"/fiado/clientes/{cid}/editar").text
    resposta = postar(
        cliente,
        f"/fiado/clientes/{cid}/editar",
        {"nome": "Sebastião L. Lima", "limite": "100,00", "ativo": "1"},
    )
    assert resposta.status_code == 303
    assert "Limite R$ 100,00" in cliente.get(f"/fiado/clientes/{cid}").text
