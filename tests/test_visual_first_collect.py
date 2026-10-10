"""시각 우선 개편(2026-10-10) — R-ONE 거래량 창 분할 수집과 단일 플래그."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_realestate import config
from agent_realestate.collectors import macro as collector


def _row(month, value):
    return {"STATBL_ID": "A_2024_00554", "CLS_ID": 500002, "ITM_ID": 100001,
            "UI_NM": "호수", "DTACYCLE_CD": "MM", "WRTTIME_IDTFR_ID": month, "DTA_VAL": value}


def _months(start, end):
    y, m = int(start[:4]), int(start[4:])
    out = []
    while f"{y:04d}{m:02d}" <= end:
        out.append(f"{y:04d}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _windowed_fetch(published_through="202608", calls=None, total_bump=None):
    def fetch(url, *args):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert q["pSize"] == "5"
        months = [m for m in _months(q["START_WRTTIME"], q["END_WRTTIME"]) if m <= published_through]
        if calls is not None:
            calls.append((q["START_WRTTIME"], q["END_WRTTIME"]))
        rows = [_row(m, 1000 + int(m[-2:])) for m in months]
        total = len(rows) + (1 if total_bump == q["START_WRTTIME"] else 0)
        return json.dumps({"SttsApiTblData": [{"head": [{"list_total_count": total},
                                                         {"RESULT": {"CODE": "INFO-000"}}]},
                                               {"row": rows}]})
    return fetch


def test_rone_sales_windows_cover_24_months_in_five_row_requests():
    calls = []
    series = collector.fetch_seoul_apt_sales("2026-10-03", fetch=_windowed_fetch(calls=calls), months=24)
    assert [d for d, _ in series] == [f"{m[:4]}-{m[4:]}-01" for m in _months("202411", "202608")]
    assert len(calls) == 5 and calls[0][0] == "202411" and calls[-1][1] == "202610"
    assert all(len(_months(s, e)) <= 5 for s, e in calls)
    assert len({m for s, e in calls for m in _months(s, e)}) == 24


def test_rone_sales_window_with_sample_truncation_fails_whole_series():
    with pytest.raises(RuntimeError):
        collector.fetch_seoul_apt_sales("2026-10-03", fetch=_windowed_fetch(total_bump="202503"), months=24)


def test_rone_sales_default_stays_single_five_month_window():
    calls = []
    series = collector.fetch_seoul_apt_sales("2026-10-03", fetch=_windowed_fetch(calls=calls))
    assert calls == [("202606", "202610")]
    assert [d for d, _ in series] == ["2026-06-01", "2026-07-01", "2026-08-01"]


def test_visual_first_flag_defaults_on_and_env_overrides(monkeypatch):
    monkeypatch.delenv("RE_VISUAL_FIRST", raising=False)
    assert config.visual_first_enabled() is True          # 2026-10-10 사람 승인으로 기본 켬
    monkeypatch.setenv("RE_VISUAL_FIRST", "0")
    assert config.visual_first_enabled() is False
    monkeypatch.setenv("RE_VISUAL_FIRST", "1")
    assert config.visual_first_enabled() is True
    monkeypatch.setenv("RE_VISUAL_FIRST", "yes")
    assert config.visual_first_enabled() is False


def test_visual_first_window_leaves_24_published_months_after_two_month_lag():
    series = collector.fetch_seoul_apt_sales("2026-10-10", fetch=_windowed_fetch(),
                                             months=collector.VISUAL_FIRST_SALES_MONTHS)
    assert len(series) >= 24 and series[-24][0] == "2024-09-01" and series[-1][0] == "2026-08-01"
