"""닮은 국면 사례집 입력 수집 — ECOS 시계열 7개를 한 번에 받아 판(vintage)째 보관한다. DIKW 검토 2026-10-30 P3.

코드·항목은 2026-10-30 실측(report/2026-10-30-dikw-kw-review/p3/DATA-PROBE.md). 항목 순서가 바뀌면 ECOS 가 INFO-200 을 낸다.
판을 지우지 않고 쌓는다 — 지금은 최신 개정값으로 과거를 다시 분류하지만, 쌓인 판은 나중에 '당시 알려진 값' 대조에 쓴다.
키는 환경변수에서만(`macro.fetch_ecos_series`), 키가 든 URL 은 어디에도 남기지 않는다.
"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import date

from agent_realestate.collectors.macro import fetch_ecos_series

ANALOG_SERIES = {  # raw 키: (통계, 항목, 시작)
    "price_old": ("901Y093", "H69B/R70F", "200311"),       # 부동산원 서울 아파트 매매지수, 기준 2021.06
    "price_new": ("901Y149", "01/a7", "202106"),           # 같은 지수, 기준 2026.06
    "mortgage_rate": ("121Y006", "BECBLA0302", "200109"),  # 신규취급 주택담보대출 금리
    "govt3y": ("721Y001", "5020000", "200001"),            # 국고채 3년(월)
    "m2": ("161Y005", "BBHS00", "200310"),                 # M2 평잔 계절조정
    "seoul_loans": ("151Y003", "1111000/A00", "200310"),   # 예금은행 가계대출 서울
    "permits_ytd": ("901Y105", "SEO", "200701"),           # 주택건설 인허가 서울(연초 누계)
}
SNAPSHOT_DIR = "report/blog/snapshots/analogs"
REFRESH_DAYS = 7
_FILE_RE = re.compile(r"analog-inputs-(\d{4}-\d{2}-\d{2})\.json$")


def collect_analog_inputs(today: str, fetch_series=fetch_ecos_series) -> dict:
    """ECOS 7개 시계열 → {"asof", "series": {키: [[YYYY-MM-DD, 값], ...]}}. 하나라도 비면 예외(부분 판은 만들지 않는다)."""
    end = today[:4] + today[5:7]
    series = {}
    for key, (stat, item, start) in ANALOG_SERIES.items():
        rows = fetch_series(stat, item, "M", start, end, n=1000)
        if not rows:
            raise RuntimeError(f"ECOS {stat} 빈 응답")
        series[key] = [[d, v] for d, v in rows]
    return {"asof": today, "series": series}


def analog_inputs_path_latest(dir: str = SNAPSHOT_DIR) -> str | None:
    cands = sorted(f for f in glob.glob(os.path.join(dir, "analog-inputs-*.json")) if _FILE_RE.search(f))
    return cands[-1] if cands else None


def load_analog_inputs_latest(dir: str = SNAPSHOT_DIR) -> dict | None:
    p = analog_inputs_path_latest(dir)
    if not p:
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_analog_inputs(payload: dict, dir: str = SNAPSHOT_DIR) -> str:
    os.makedirs(dir, exist_ok=True)
    dst = os.path.join(dir, f"analog-inputs-{payload['asof']}.json")
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, dst)
    return dst


def is_refresh_due(today: str, dir: str = SNAPSHOT_DIR) -> bool:
    """마지막 판이 REFRESH_DAYS 일보다 오래됐거나 없으면 True — 매일 ECOS 7회 호출을 피한다."""
    p = analog_inputs_path_latest(dir)
    if not p:
        return True
    last = date.fromisoformat(_FILE_RE.search(p).group(1))
    return (date.fromisoformat(today) - last).days >= REFRESH_DAYS
