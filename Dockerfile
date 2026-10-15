# Caixa da Papelaria — imagem para publicar na nuvem (funciona em servidores ARM e x86).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CAIXA_DIR_DADOS=/dados \
    CAIXA_PASTA_BACKUP=/dados/backups \
    CAIXA_COOKIES_SEGUROS=1 \
    CAIXA_ATRAS_DE_PROXY=1

WORKDIR /app

# Dependências primeiro (aproveita o cache do Docker), nas versões exatas testadas.
COPY pyproject.toml constraints.txt ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts
RUN pip install -c constraints.txt -e . \
    && useradd --system --uid 1000 --home /dados caixa \
    && mkdir -p /dados/backups \
    && chown -R caixa:caixa /dados

# O sistema roda sem privilégios de administrador.
USER caixa
VOLUME ["/dados"]
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://localhost:8765/login'), timeout=4)"

CMD ["python", "-m", "app"]
