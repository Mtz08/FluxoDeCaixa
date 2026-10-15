from datetime import UTC, datetime

from app.core.datas import formatar_data_hora, para_local


def test_utc_vira_horario_de_brasilia() -> None:
    # 02:30 UTC = 23:30 do dia anterior em São Paulo (UTC-3, sem horário de verão).
    momento = datetime(2026, 10, 14, 2, 30, tzinfo=UTC)
    local = para_local(momento)
    assert (local.day, local.hour) == (13, 23)
    assert formatar_data_hora(momento) == "13/10/2026 23:30"


def test_datetime_sem_fuso_e_tratado_como_utc() -> None:
    assert para_local(datetime(2026, 1, 1, 12, 0)).hour == 9
