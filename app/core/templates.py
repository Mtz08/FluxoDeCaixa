"""Jinja2 com filtros de moeda e data, e helpers de resposta."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.datas import formatar_data, formatar_data_hora
from app.core.dinheiro import formatar_reais, valor_para_campo
from app.core.seguranca import CAMPO_CSRF

COOKIE_AVISO = "caixa_aviso"
_PREFIXO_ERRO = "erro:"

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.filters["reais"] = formatar_reais
templates.env.filters["data_hora"] = formatar_data_hora
templates.env.filters["data"] = formatar_data
templates.env.filters["campo_moeda"] = valor_para_campo


def renderizar(
    request: Request, nome: str, contexto: dict[str, Any] | None = None, status: int = 200
) -> HTMLResponse:
    aviso = request.cookies.get(COOKIE_AVISO)
    texto = unquote(aviso) if aviso else None
    eh_erro = bool(texto and texto.startswith(_PREFIXO_ERRO))
    dados: dict[str, Any] = {
        "usuario": getattr(request.state, "usuario", None),
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "campo_csrf": CAMPO_CSRF,
        "aviso": None if eh_erro else texto,
        "erro": texto.removeprefix(_PREFIXO_ERRO) if texto and eh_erro else None,
    }
    dados.update(contexto or {})
    resposta = templates.TemplateResponse(request, nome, dados, status_code=status)
    if aviso:
        resposta.delete_cookie(COOKIE_AVISO, **opcoes_cookie(request))
    return resposta


def opcoes_cookie(request: Request) -> dict[str, Any]:
    """Cookies sempre HttpOnly + SameSite=Strict; no modo nuvem (HTTPS), também Secure."""
    seguro = bool(getattr(request.app.state.config, "cookies_seguros", False))
    return {"httponly": True, "samesite": "strict", "secure": seguro}


def redirecionar(
    request: Request, url: str, aviso: str | None = None, *, erro: bool = False
) -> Response:
    """Post/Redirect/Get. Em requisições HTMX usa HX-Redirect (página inteira)."""
    resposta: Response
    if request.headers.get("hx-request"):
        resposta = Response(status_code=204, headers={"HX-Redirect": url})
    else:
        resposta = RedirectResponse(url, status_code=303)
    if aviso:
        resposta.set_cookie(
            COOKIE_AVISO,
            quote(_PREFIXO_ERRO + aviso if erro else aviso),
            max_age=60,
            **opcoes_cookie(request),
        )
    return resposta
