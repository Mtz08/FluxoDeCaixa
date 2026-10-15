"""Fluxos completos pelo navegador (HTTP): primeiro acesso, login, CSRF, perfis."""

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.seguranca import COOKIE_SESSAO
from app.modules.auth.models import Usuario
from tests.conftest import SENHA_ADMIN, SENHA_OPERADOR, entrar, postar, so_sqlite


def test_sem_usuarios_tudo_leva_ao_primeiro_acesso(cliente: TestClient) -> None:
    for url in ("/", "/usuarios", "/login"):
        resposta = cliente.get(url, follow_redirects=False)
        assert resposta.headers["location"] == "/primeiro-acesso"


def test_assistente_de_primeiro_acesso_cria_admin_e_entra(cliente: TestClient) -> None:
    assert cliente.get("/primeiro-acesso").status_code == 200
    resposta = postar(
        cliente,
        "/primeiro-acesso",
        {"nome": "Dona Maria", "login": "Maria", "senha": "segredo1", "confirmacao": "segredo1"},
    )
    assert resposta.status_code == 303
    pagina = cliente.get("/")
    assert "Dona Maria" in pagina.text and "Administrador" in pagina.text
    assert "Caixa fechado" in pagina.text
    assert "Administrador criado" in pagina.text
    # Depois de criado, o assistente não abre mais.
    assert cliente.get("/primeiro-acesso", follow_redirects=False).headers["location"] == "/login"


def test_primeiro_acesso_com_erro_mostra_mensagem(cliente: TestClient) -> None:
    resposta = postar(
        cliente,
        "/primeiro-acesso",
        {"nome": "D", "login": "maria", "senha": "x", "confirmacao": "x"},
    )
    assert resposta.status_code == 400
    assert "nome deve ter" in resposta.text


def test_login_define_cookie_seguro(cliente: TestClient, admin: Usuario) -> None:
    resposta = postar(cliente, "/login", {"login": "maria", "senha": SENHA_ADMIN})
    assert resposta.status_code == 303
    cookie = next(h for h in resposta.headers.get_list("set-cookie") if h.startswith(COOKIE_SESSAO))
    assert "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower()


def test_login_errado_mostra_mensagem(cliente: TestClient, admin: Usuario) -> None:
    resposta = postar(cliente, "/login", {"login": "maria", "senha": "errada"})
    assert resposta.status_code == 400
    assert "incorretos" in resposta.text


def test_post_sem_csrf_e_recusado(cliente: TestClient, admin: Usuario) -> None:
    resposta = cliente.post("/login", data={"login": "maria", "senha": SENHA_ADMIN})
    assert resposta.status_code == 403
    assert "desatualizada" in resposta.text
    resposta = cliente.post(
        "/login", data={"login": "maria", "senha": SENHA_ADMIN, "csrf_token": "falso"}
    )
    assert resposta.status_code == 403


def test_csrf_pelo_cabecalho_htmx(cliente: TestClient, admin: Usuario) -> None:
    cliente.get("/login")
    resposta = cliente.post(
        "/login",
        data={"login": "maria", "senha": SENHA_ADMIN},
        headers={"X-CSRF-Token": cliente.cookies["caixa_csrf"], "HX-Request": "true"},
    )
    assert resposta.status_code == 204
    assert resposta.headers["HX-Redirect"] == "/"


def test_sem_login_vai_para_tela_de_login(cliente: TestClient, admin: Usuario) -> None:
    resposta = cliente.get("/", follow_redirects=False)
    assert resposta.headers["location"] == "/login"


def test_operador_nao_acessa_usuarios(cliente: TestClient, operador: Usuario) -> None:
    entrar(cliente, "joao", SENHA_OPERADOR)
    assert cliente.get("/").status_code == 200
    resposta = cliente.get("/usuarios")
    assert resposta.status_code == 403
    assert "permissão" in resposta.text
    assert (
        postar(
            cliente, "/usuarios/novo", {"nome": "X", "login": "xxx", "senha": "abcdef"}
        ).status_code
        == 403
    )


def test_senha_provisoria_obriga_troca_antes_de_usar(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    postar(
        cliente,
        "/usuarios/novo",
        {"nome": "Ana Paula", "login": "ana", "perfil": "operador", "senha": "provisoria1"},
    )
    postar(cliente, "/sair")
    entrar(cliente, "ana", "provisoria1")

    resposta = cliente.get("/", follow_redirects=False)
    assert resposta.headers["location"] == "/trocar-senha"

    resposta = postar(
        cliente,
        "/trocar-senha",
        {"atual": "provisoria1", "nova": "minha-senha", "confirmacao": "minha-senha"},
    )
    assert resposta.status_code == 303
    assert cliente.get("/").status_code == 200


def test_admin_gerencia_usuarios(cliente: TestClient, admin: Usuario, operador: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    assert "joao" in cliente.get("/usuarios").text
    assert cliente.get(f"/usuarios/{operador.id}").status_code == 200

    resposta = postar(
        cliente, f"/usuarios/{operador.id}", {"nome": "João Silva", "perfil": "operador"}
    )
    assert resposta.status_code == 303  # sem "ativo" = desativado
    assert "Inativo" in cliente.get("/usuarios").text

    resposta = postar(cliente, f"/usuarios/{operador.id}/senha", {"nova": "x"})
    assert resposta.status_code == 400
    resposta = postar(cliente, f"/usuarios/{operador.id}/senha", {"nova": "temporaria1"})
    assert resposta.status_code == 303


def test_admin_nao_se_rebaixa_pela_tela(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    resposta = postar(
        cliente, f"/usuarios/{admin.id}", {"nome": "Dona", "perfil": "operador", "ativo": "1"}
    )
    assert resposta.status_code == 400
    assert "pelo menos um administrador" in resposta.text


def test_sair_encerra_a_sessao(cliente: TestClient, admin: Usuario) -> None:
    entrar(cliente, "maria", SENHA_ADMIN)
    token = cliente.cookies[COOKIE_SESSAO]
    postar(cliente, "/sair")
    cliente.cookies.set(COOKIE_SESSAO, token)  # tentar reaproveitar o cookie antigo
    assert cliente.get("/", follow_redirects=False).headers["location"] == "/login"


def test_cabecalhos_de_seguranca(cliente: TestClient, admin: Usuario) -> None:
    resposta = cliente.get("/login")
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resposta.headers["Content-Security-Policy"]
    assert resposta.headers["Cache-Control"] == "no-store"


def test_documentacao_da_api_nao_exposta(cliente: TestClient) -> None:
    assert cliente.get("/docs").status_code == 404
    assert cliente.get("/openapi.json").status_code == 404


@so_sqlite("PRAGMAs são do SQLite")
def test_pragmas_do_banco(db: Session) -> None:
    assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
