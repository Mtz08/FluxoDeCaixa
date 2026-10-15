"""Fluxo do caixa pelas telas: abrir, lançar com confirmação, estornar, fechar cego."""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import Usuario
from app.modules.caixa.models import MovimentoCaixa
from tests.conftest import SENHA_ADMIN, SENHA_OPERADOR, entrar, postar


def _chave(html: str) -> str:
    achado = re.search(r'name="chave" value="([0-9a-f-]{36})"', html)
    assert achado, "formulário sem chave de idempotência"
    return achado.group(1)


def _abrir(cliente: TestClient, fundo: str = "100,00") -> None:
    pagina = cliente.get("/").text
    resposta = postar(cliente, "/caixa/abrir", {"fundo": fundo, "chave": _chave(pagina)})
    assert resposta.status_code == 303


def _sessao_id(cliente: TestClient) -> str:
    achado = re.search(r'name="sessao_id" value="(\d+)"', cliente.get("/caixa/fechar").text)
    assert achado
    return achado.group(1)


def test_abrir_caixa_pela_tela(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    assert "Caixa fechado" in cliente.get("/").text
    _abrir(cliente, "150,00")
    pagina = cliente.get("/").text
    assert "Caixa aberto com fundo de troco de R$ 150,00" in pagina
    assert 'data-atalho="F2"' in pagina


def test_lancamento_sem_caixa_volta_com_erro(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    resposta = cliente.get("/caixa/lancar/entrada", follow_redirects=False)
    assert resposta.status_code == 303
    assert "caixa está fechado" in cliente.get("/").text
    assert cliente.get("/caixa/lancar/inexistente").status_code == 400


def test_entrada_com_confirmacao_e_sem_duplicar(
    cliente: TestClient, operador: Usuario, db: Session
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir(cliente)
    form = cliente.get("/caixa/lancar/entrada").text
    chave = _chave(form)
    dinheiro, pix = re.findall(r'name="(parte_\d+)"', form)[:2]
    dados = {dinheiro: "20,00", pix: "R$ 30,00", "descricao": "material escolar", "chave": chave}

    revisao = postar(cliente, "/caixa/lancar/entrada", dados | {"etapa": "revisar"})
    assert revisao.status_code == 200
    assert "Confira antes de gravar" in revisao.text
    assert "R$ 50,00" in revisao.text

    corrigir = postar(cliente, "/caixa/lancar/entrada", dados | {"etapa": "corrigir"})
    assert 'value="material escolar"' in corrigir.text

    for _ in range(2):  # duplo clique / reenvio
        gravado = postar(cliente, "/caixa/lancar/entrada", dados | {"etapa": "gravar"})
        assert gravado.status_code == 303
    assert "envio repetido ignorado" in cliente.get("/").text
    assert db.scalar(select(func.count()).select_from(MovimentoCaixa)) == 1


def test_erro_de_validacao_volta_ao_formulario(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir(cliente)
    chave = _chave(cliente.get("/caixa/lancar/saida").text)
    resposta = postar(
        cliente, "/caixa/lancar/saida", {"valor": "10,00", "forma_id": "1", "chave": chave}
    )
    assert resposta.status_code == 400
    assert "categoria" in resposta.text
    resposta = postar(
        cliente, "/caixa/lancar/sangria", {"valor": "abc", "chave": chave, "descricao": "x"}
    )
    assert resposta.status_code == 400
    assert "Confira o valor" in resposta.text
    resposta = postar(cliente, "/caixa/lancar/entrada", {"chave": "nao-e-uuid"})
    assert resposta.status_code == 400


def test_operador_nao_ve_o_esperado_mas_admin_ve(
    cliente: TestClient, operador: Usuario, admin: Usuario
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir(cliente)
    assert "esperado" not in cliente.get("/").text.lower()
    postar(cliente, "/sair")
    entrar(cliente, "maria", SENHA_ADMIN)
    assert "Dinheiro na gaveta (esperado)" in cliente.get("/").text


def test_estorno_por_operador_com_senha_do_admin(
    cliente: TestClient, operador: Usuario, admin: Usuario, db: Session
) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir(cliente)
    chave = _chave(cliente.get("/caixa/lancar/suprimento").text)
    postar(
        cliente, "/caixa/lancar/suprimento", {"valor": "10,00", "chave": chave, "etapa": "gravar"}
    )
    mov_id = db.scalar(select(MovimentoCaixa.id))
    db.rollback()  # libera o banco para o servidor (cada transação reserva a escrita)

    form = cliente.get(f"/caixa/movimentos/{mov_id}/estornar").text
    assert "Autorização do administrador" in form
    dados = {"motivo": "valor errado", "chave": _chave(form)}

    negado = postar(
        cliente,
        f"/caixa/movimentos/{mov_id}/estornar",
        dados | {"admin_login": "maria", "admin_senha": "errada"},
    )
    assert negado.status_code == 400 and "Autorização negada" in negado.text

    ok = postar(
        cliente,
        f"/caixa/movimentos/{mov_id}/estornar",
        dados | {"admin_login": "maria", "admin_senha": SENHA_ADMIN},
    )
    assert ok.status_code == 303
    pagina = cliente.get("/").text
    assert "Estorno de R$ 10,00 registrado" in pagina
    assert "Estornado" in pagina
    # Já estornado: a tela de estorno não abre de novo.
    assert cliente.get(f"/caixa/movimentos/{mov_id}/estornar").status_code == 400


def test_fechamento_cego_pela_tela(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    _abrir(cliente, "100,00")
    cliente.get("/")  # consome o aviso "Caixa aberto com fundo de troco de R$ 100,00"
    form_fechar = cliente.get("/caixa/fechar").text
    assert "R$ 100,00" not in form_fechar  # não mostra o esperado
    sessao_id = _sessao_id(cliente)

    conferencia = postar(
        cliente, "/caixa/fechar", {"sessao_id": sessao_id, "contado": "95,00", "etapa": "conferir"}
    )
    assert "Falta de R$ 5,00" in conferencia.text

    sem_justificativa = postar(
        cliente, "/caixa/fechar", {"sessao_id": sessao_id, "contado": "95,00", "etapa": "gravar"}
    )
    assert sem_justificativa.status_code == 400
    assert "justificativa" in sem_justificativa.text

    fechado = postar(
        cliente,
        "/caixa/fechar",
        {
            "sessao_id": sessao_id,
            "contado": "95,00",
            "etapa": "gravar",
            "justificativa": "troco dado a mais",
        },
    )
    assert fechado.status_code == 303
    resumo = cliente.get(fechado.headers["location"])
    assert "Caixa fechado." in resumo.text
    assert "troco dado a mais" in resumo.text
    assert "Falta de R$ 5,00" in resumo.text

    # Enviar de novo não reabre nem quebra.
    repetido = postar(
        cliente, "/caixa/fechar", {"sessao_id": sessao_id, "contado": "95,00", "etapa": "gravar"}
    )
    assert repetido.status_code == 303
    assert "Caixa fechado" in cliente.get("/").text


def test_fechar_sem_caixa_aberto(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    assert cliente.get("/caixa/fechar", follow_redirects=False).status_code == 303
    resposta = postar(cliente, "/caixa/abrir", {"fundo": "-5", "chave": str(uuid4())})
    assert resposta.status_code == 303
    assert "negativo" in cliente.get("/").text


def test_operador_nao_ve_caixa_fechado_de_outro(
    cliente: TestClient, operador: Usuario, admin: Usuario
) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    _abrir(cliente, "0")
    sessao_id = _sessao_id(cliente)
    postar(cliente, "/caixa/fechar", {"sessao_id": sessao_id, "contado": "0", "etapa": "conferir"})
    postar(cliente, "/caixa/fechar", {"sessao_id": sessao_id, "contado": "0", "etapa": "gravar"})
    postar(cliente, "/sair")

    entrar(cliente, "joao", SENHA_OPERADOR)
    assert cliente.get(f"/caixa/sessoes/{sessao_id}").status_code == 403
