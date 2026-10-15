"""Datas: tudo gravado em UTC, exibido em America/Sao_Paulo."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO_LOJA = ZoneInfo("America/Sao_Paulo")


def agora_utc() -> datetime:
    return datetime.now(UTC)


def para_local(momento: datetime) -> datetime:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(FUSO_LOJA)


def hoje_local() -> date:
    return para_local(agora_utc()).date()


def formatar_data_hora(momento: datetime | None) -> str:
    if momento is None:
        return ""
    return para_local(momento).strftime("%d/%m/%Y %H:%M")


def formatar_data(dia: date | None) -> str:
    return dia.strftime("%d/%m/%Y") if dia else ""


def inicio_do_dia_utc(dia: date) -> datetime:
    """00:00 do dia (horário da loja) convertido para UTC."""
    return datetime.combine(dia, time.min, tzinfo=FUSO_LOJA).astimezone(UTC)


def intervalo_utc(inicio: date, fim: date) -> tuple[datetime, datetime]:
    """[início 00:00, dia seguinte ao fim 00:00) da loja, em UTC (fim inclusivo)."""
    return inicio_do_dia_utc(inicio), inicio_do_dia_utc(fim + timedelta(days=1))


def dia_local(momento: datetime) -> date:
    return para_local(momento).date()
