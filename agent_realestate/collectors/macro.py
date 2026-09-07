"""거시 지표 수집 — MacroContext 의 배관(2026-09-07, `report/2026-09-07-macro-context-briefing.md` §3.3).
무키 경로 우선(FRED CSV·bok.or.kr 기준금리 표·네이버금융 시장지표). ECOS 는 `ECOS_API_KEY` 가 있을 때만(백필용).

지표 코드 · 출처 · 빈도
  bok_base         한국은행 기준금리(변경일 시계열)        bok.or.kr 기준금리 추이 표            event(changes)
  fed_target_hi/lo 연방기금 목표범위 상단/하단             FRED DFEDTARU/DFEDTARL(무키 CSV)     daily(step)
  us10y · us2y     미 국채 10년·2년                        FRED DGS10/DGS2                        daily
  us_m2            미 M2(계절조정, 십억달러)               FRED M2SL                              monthly
  us_mortgage30    미 30년 고정 모기지                     FRED MORTGAGE30US(프레디맥)            weekly
  kr_govt10y_m     국고채 10년(월평균, OECD 경유 1~2개월 지연) FRED IRLTLT01KRM156N              monthly
  kr_govt3y · cd91 국고채 3년 · CD 91일                    금융투자협회 고시(네이버금융 일별 표)   daily
  cofix_new/bal    코픽스 신규취급액/잔액기준              은행연합회 공시(네이버금융 일별 표)     monthly(step)
  gold_krw_g       국내 금 매매기준율(원/g)                네이버금융 금 시세 표(은행 고시)        daily
  gold_usd_oz      국제 금($/oz)                           같은 표 '기준 국제 금 시세'             daily
  usdkrw           원/달러 매매기준율                      네이버금융 환율 표(은행 고시)           daily

규약
- 실패한 지표는 `errors[code]` 에 사유만 남기고 `indicators` 에서 빠진다(카드 생략 — 지어내지 않음). 빈 표(새 관측 0)도 실패다.
  빠진 지표의 마지막 성공 이력은 `carried[code]` 로 넘겨 다음 수집이 이어받는다(카드 생성엔 쓰지 않는다).
- 시계열은 이전 스냅샷과 병합해 누적(`merge_series`). 네이버 일별 표는 페이지당 10행·최대 6페이지뿐이라
  첫 수집 뒤엔 2페이지만 읽고 나머지는 누적분으로 채운다. 지표당 보존 관측 수 = SERIES_KEEP.
- 시크릿: ECOS 키는 os.environ(`.env` 경유)에서만 읽고, 키가 든 URL·예외 원문은 밖으로 내지 않는다.
- 한국 M2 는 무키 출처가 없다(FRED MYAGM2KRM189N 은 2017 종료) → ECOS 키 확보 후 시리즈 확정([사용자]).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

from agent_realestate import config

UA = "Mozilla/5.0 (compatible; agent-realestate-macro/1.0)"
TIMEOUT_S = 15
SERIES_KEEP = 400            # 지표당 보존 관측 수(일별 ≈ 1.5년 · 월별 33년)
NAVER_PAGES_FIRST = 6        # 첫 수집(이전 스냅샷 없음) — 네이버 일별 표 최대 페이지
NAVER_PAGES_DAILY = 2        # 이후 — 신규 행만 읽고 이전 스냅샷과 병합
PAGE_DELAY_S = 0.2

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
FRED_PAGE = "https://fred.stlouisfed.org/series/{id}"
FRED_SERIES = {  # code: (fred_id, label, unit, freq)
    "fed_target_hi": ("DFEDTARU", "미 연방기금 목표범위 상단", "%", "daily"),
    "fed_target_lo": ("DFEDTARL", "미 연방기금 목표범위 하단", "%", "daily"),
    "us10y": ("DGS10", "미 국채 10년", "%", "daily"),
    "us2y": ("DGS2", "미 국채 2년", "%", "daily"),
    "us_m2": ("M2SL", "미 M2(계절조정)", "십억달러", "monthly"),
    "us_mortgage30": ("MORTGAGE30US", "미 30년 고정 모기지(프레디맥)", "%", "weekly"),
    "kr_govt10y_m": ("IRLTLT01KRM156N", "국고채 10년(월평균, OECD 경유)", "%", "monthly"),
}
BOK_BASE_URL = "https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643"
NAVER_INTEREST = "https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd={cd}&page={page}"
NAVER_INTEREST_SERIES = {  # code: (marketindexCd, label, freq, source)
    "kr_govt3y": ("IRR_GOVT03Y", "국고채 3년", "daily", "금융투자협회 고시(네이버금융 경유)"),
    "cd91": ("IRR_CD91", "CD 91일", "daily", "금융투자협회 고시(네이버금융 경유)"),
    "cofix_new": ("IRR_COFIXNEW", "코픽스 신규취급액", "monthly", "은행연합회 공시(네이버금융 경유)"),
    "cofix_bal": ("IRR_COFIXBAL", "코픽스 잔액기준", "monthly", "은행연합회 공시(네이버금융 경유)"),
}
NAVER_GOLD = "https://finance.naver.com/marketindex/goldDailyQuote.naver?page={page}"
NAVER_FX = "https://finance.naver.com/marketindex/exchangeDailyQuote.naver?marketindexCd=FX_USDKRW&page={page}"
NAVER_ENCODING = "cp949"

# ── 발표 캘린더(연 1회 갱신 — [사용자] 2027 일정은 한은·연준 공표 후 등록) ─────────────────────
# 검증 2026-09-07: bok.or.kr 통화정책방향 결정회의(검증된 날짜만 — 02·04월 회의는 미검증이라 미등록), federalreserve.gov FOMC(2일차=성명일, 미 동부시간).
BOK_MPC_DATES = ("2026-01-15", "2026-05-28", "2026-07-16", "2026-08-27", "2026-10-22", "2026-11-26")
FOMC_DATES = ("2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09")
FOMC_KST_SHIFT_DAYS = 1   # FOMC_DATES 는 미국 날짜(회의 2일차, 성명 미 동부 14:00) — 한국 날짜는 익일 03~04시 → release_dates 가 +1일(S2 Codex)
RELEASE_LABELS = {"bok": "한국은행 금통위 기준금리 결정", "fomc": "미 FOMC 정책금리 결정(한국시간 발표일)", "cofix": "은행연합회 코픽스 공시"}
# 관공서 공휴일(대체공휴일 포함) — 코픽스 '다음 영업일' 판정용. 출처: 천문연구원 2026년 월력요항(kasi.re.kr/kor/post/newsMaterial/32031):
# 3·1절(3.1 일)·부처님오신날(5.24 일) → 대체 3.2·5.25 / 토요일 겹침 광복절(8.15)·추석 마지막 날(9.26)·개천절(10.3) → 대체 8.17·9.28·10.5
# (현충일 6.6 토요일은 대체 대상 아님). 6.3 = 제9회 지방선거(공휴일 규정 §2-10). 연 1회 갱신([사용자]) — 미등록 연도는 주말만 제외.
KR_HOLIDAYS = frozenset((
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-01", "2026-03-02", "2026-05-05",
    "2026-05-24", "2026-05-25", "2026-06-03", "2026-06-06", "2026-08-15", "2026-08-17",
    "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-28", "2026-10-03", "2026-10-05", "2026-10-09",
    "2026-12-25", "2027-01-01",
))


class MacroSourceMissing(RuntimeError):
    """출처 자체가 없음(키 미설정 등) — 실패가 아니라 생략."""


# ── 전송 ─────────────────────────────────────────────────────────────────────
def fetch_text(url: str, encoding: str = "utf-8") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read().decode(encoding, errors="replace")


# ── 파서(출처별) ───────────────────────────────────────────────────────────────
_BOK_ROW = re.compile(r'<td class="fb">\s*(\d{4})\s*</td>\s*<td>\s*(\d{1,2})월\s*(\d{1,2})일\s*</td>\s*<td>\s*([\d.]+)\s*</td>')
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_NAVER_DATE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def parse_bok_base_rate(html: str) -> list[tuple[str, float]]:
    """bok.or.kr 기준금리 추이 표 → [(변경일, %)] 오름차순. 변경일만 있는 계단 시계열."""
    rows = [(f"{y}-{int(mo):02d}-{int(d):02d}", float(v)) for y, mo, d, v in _BOK_ROW.findall(html)]
    if not rows:
        raise ValueError("bok.or.kr 기준금리 표를 찾지 못함(마크업 변경?)")
    return sorted(set(rows))


def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    """FRED fredgraph.csv(헤더 observation_date,<ID>) → [(YYYY-MM-DD, 값)]. 결측 '.' 은 건너뜀."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not re.match(r"^\d{4}-\d{2}-\d{2}$", row[0]):
            continue
        try:
            out.append((row[0], float(row[1])))
        except ValueError:
            continue
    if not out:
        raise ValueError("FRED CSV 에 관측이 없음")
    return sorted(out)


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("+", "").replace("%", "").strip())


def parse_naver_daily_rows(html: str) -> list[list[str]]:
    """네이버금융 일별 표(tbl_exchange) → 행마다 태그를 벗긴 셀 리스트(첫 셀 = YYYY.MM.DD)."""
    rows = []
    for tr in _TR.findall(html):
        cells = [_TAG.sub("", c).strip() for c in _TD.findall(tr)]
        if len(cells) >= 2 and _NAVER_DATE.match(cells[0]):
            rows.append(cells)
    return rows


def _iso(naver_date: str) -> str:
    return naver_date.replace(".", "-")


def parse_naver_interest(html: str) -> list[tuple[str, float]]:
    """금리 일별 표(날짜·금리·전일대비·등락률) → [(날짜, %)] 오름차순."""
    return sorted((_iso(c[0]), _num(c[1])) for c in parse_naver_daily_rows(html))


def parse_naver_gold(html: str) -> dict[str, list[tuple[str, float]]]:
    """금 일별 표(날짜·매매기준율·전일대비·사실때·파실때·입금시·해지시·국제금·환율) → krw_g / usd_oz / usdkrw."""
    if "기준 국제 금 시세" not in html or "기준 원달러 환율" not in html:
        raise ValueError("금 표 헤더 변경(국제 금·환율 열을 찾지 못함)")
    krw, usd, fx = [], [], []
    for c in parse_naver_daily_rows(html):
        if len(c) != 9:                                       # 날짜·매매기준율·전일대비·사실때·파실때·입금시·해지시·국제금·환율
            raise ValueError(f"금 표 열 수 변경({len(c)}≠9)")
        d = _iso(c[0])
        krw.append((d, _num(c[1])))
        usd.append((d, _num(c[-2])))
        fx.append((d, _num(c[-1])))
    return {"gold_krw_g": sorted(krw), "gold_usd_oz": sorted(usd), "usdkrw_ref": sorted(fx)}


def parse_naver_fx(html: str) -> list[tuple[str, float]]:
    """환율 일별 표(날짜·매매기준율·전일대비·현찰2·송금2) → [(날짜, 원)] 오름차순."""
    return sorted((_iso(c[0]), _num(c[1])) for c in parse_naver_daily_rows(html))


def parse_ecos_json(text: str) -> list[tuple[str, float]]:
    """ECOS StatisticSearch JSON → [(날짜, 값)]. TIME 이 YYYYMMDD/YYYYMM/YYYY 면 각각 일/월초/연초로."""
    obj = json.loads(text)
    if "StatisticSearch" not in obj:
        code = (obj.get("RESULT") or {}).get("CODE", "?")
        raise ValueError(f"ECOS 응답 오류 코드 {code}")
    out = []
    for r in obj["StatisticSearch"].get("row", []):
        t, v = str(r.get("TIME", "")), r.get("DATA_VALUE")
        if v in (None, "") or len(t) not in (4, 6, 8):
            continue
        d = t[:4] + "-" + (t[4:6] if len(t) >= 6 else "01") + "-" + (t[6:8] if len(t) == 8 else "01")
        out.append((d, float(v)))
    return sorted(out)


# ── 시계열 조작 ────────────────────────────────────────────────────────────────
def merge_series(old: list, new: list, keep: int = SERIES_KEEP) -> list[tuple[str, float]]:
    """날짜 키로 병합(new 우선) → 오름차순 → 최근 keep 개."""
    d = {str(k): float(v) for k, v in old}
    d.update({str(k): float(v) for k, v in new})
    return sorted(d.items())[-keep:]


def collapse_changes(series: list) -> list[tuple[str, float]]:
    """관측 시계열 → 값이 바뀐 지점만(첫 관측 포함). 코픽스처럼 매일 같은 값이 반복되는 계단 지표용."""
    out: list[tuple[str, float]] = []
    for d, v in series:
        if not out or out[-1][1] != v:
            out.append((d, v))
    return out


def build_indicator(code: str, label: str, unit: str, freq: str, source: str, url: str,
                    series: list, series_kind: str = "observations") -> dict:
    """마지막 관측 = value/date, 같은 값이 시작된 날 = since, 직전 다른 값 = prev_value/prev_date.
    한계: 보존 창(SERIES_KEEP·네이버 첫 수집 6페이지)의 첫 관측까지 같은 값이면 since 는 '관측 창 시작일' 이지 실제 변경일이
    아니고 prev_value 도 None 이다 → since_window_start=True 로 표시해 카드가 '이후 같은 값' 문구를 만들지 않게 한다."""
    series = [(str(d), float(v)) for d, v in series][-SERIES_KEEP:]
    if not series:
        raise ValueError(f"{code}: 빈 시계열")
    last_d, last_v = series[-1]
    since, prev_v, prev_d = last_d, None, None
    for d, v in reversed(series[:-1]):
        if v == last_v:
            since = d
        else:
            prev_v, prev_d = v, d
            break
    return {"code": code, "label": label, "unit": unit, "freq": freq, "source": source, "url": url,
            "series_kind": series_kind, "value": last_v, "date": last_d, "since": since,
            "since_window_start": prev_v is None,
            "prev_value": prev_v, "prev_date": prev_d, "series": [[d, v] for d, v in series]}


# ── 캘린더 ──────────────────────────────────────────────────────────────────────
def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in KR_HOLIDAYS


def cofix_release_date(year: int, month: int) -> date:
    """코픽스 공시일 = 매월 15일 15:00, 주말·공휴일이면 다음 영업일(대체공휴일 포함 — 2026-08: 15 토·17 대체공휴일 → 18, 실측 since 08-18)."""
    d = date(year, month, 15)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def release_dates(kind: str, around: date) -> list[date]:
    if kind == "bok":
        return [date.fromisoformat(s) for s in BOK_MPC_DATES]
    if kind == "fomc":
        return [date.fromisoformat(s) + timedelta(days=FOMC_KST_SHIFT_DAYS) for s in FOMC_DATES]   # 한국 날짜
    if kind == "cofix":
        out = []
        for k in range(-2, 4):
            y, m = divmod(around.month - 1 + k, 12)
            out.append(cofix_release_date(around.year + y, m + 1))
        return out
    raise KeyError(kind)


def next_release(kind: str, today: date) -> date | None:
    fut = [d for d in release_dates(kind, today) if d >= today]
    return min(fut) if fut else None


def upcoming_releases(today: date, horizon_days: int = 45) -> list[dict]:
    """오늘부터 horizon 안의 발표 일정(날짜순). 등록된 캘린더가 없는 종류는 빠진다(2027 이후 = [사용자] 갱신)."""
    out = []
    for kind in RELEASE_LABELS:
        for d in release_dates(kind, today):
            if today <= d <= today + timedelta(days=horizon_days):
                out.append({"kind": kind, "date": d.isoformat(), "label": RELEASE_LABELS[kind]})
    return sorted(out, key=lambda x: x["date"])


def recent_releases(today: date, window_days: int = 2) -> list[dict]:
    """발표 직후 판정 — 오늘 기준 window 일 안에 지난 발표(날짜는 전부 한국 날짜 — FOMC 는 release_dates 가 +1일)."""
    out = []
    for kind in RELEASE_LABELS:
        for d in release_dates(kind, today):
            if 0 <= (today - d).days <= window_days:
                out.append({"kind": kind, "date": d.isoformat(), "label": RELEASE_LABELS[kind]})
    return sorted(out, key=lambda x: x["date"])


# ── ECOS(선택, 키 필요) ──────────────────────────────────────────────────────────
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/{stat}/{cycle}/{start}/{end}/{item}"
ECOS_SERIES = {  # code: (stat, item, cycle) — 첫 행만 브리핑에서 검증. 국고채 10년·M2 는 키 확보 후 통계코드검색으로 확정([사용자]).
    "bok_base_ecos": ("722Y001", "0101000", "D"),
}
_load_env_file = config.load_env_file


def fetch_ecos_series(stat: str, item: str, cycle: str, start: str, end: str,
                      fetch=fetch_text, n: int = 1000) -> list[tuple[str, float]]:
    """ECOS StatisticSearch — 키는 환경변수에서만. 키가 든 URL 과 원 예외는 밖으로 내지 않는다."""
    _load_env_file()
    key = os.environ.get("ECOS_API_KEY")
    if not key:
        raise MacroSourceMissing("ECOS_API_KEY 없음")
    url = ECOS_URL.format(key=key, n=n, stat=stat, cycle=cycle, start=start, end=end, item=item)
    try:
        text = fetch(url, "utf-8")
    except Exception as e:                                    # noqa: BLE001
        raise RuntimeError(f"ECOS 요청 실패({type(e).__name__})") from None
    return parse_ecos_json(text)


# ── 수집 오케스트레이션 ─────────────────────────────────────────────────────────
def _reason(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:80]}"


def _naver_pages(get_page, parse, pages: int) -> list[tuple[str, float]]:
    """페이지 1..pages 를 읽되 새 날짜가 안 나오면 멈춘다."""
    acc: dict[str, float] = {}
    for p in range(1, pages + 1):
        rows = parse(get_page(p))
        new = [(d, v) for d, v in rows if d not in acc]
        if not new:
            break
        acc.update(new)
        if p < pages and PAGE_DELAY_S:
            time.sleep(PAGE_DELAY_S)
    return sorted(acc.items())


def collect_macro(today: str, prev: dict | None = None, fetch=fetch_text, naver_pages: int | None = None) -> dict:
    """지표별 수집 → 이전 스냅샷과 병합 → {asof, indicators, errors, n_ok}. 지표 하나의 실패가 다른 지표를 막지 않는다."""
    prev_ind = {**((prev or {}).get("carried") or {}), **((prev or {}).get("indicators") or {})}   # 실패로 빠진 지표의 이력도 이어받는다
    ind: dict[str, dict] = {}
    errors: dict[str, str] = {}
    pages = naver_pages or (NAVER_PAGES_DAILY if prev_ind else NAVER_PAGES_FIRST)

    def _put(code, label, unit, freq, source, url, series, series_kind="observations"):
        if not series:
            raise ValueError("새 관측 없음(빈 표)")                  # 이전 이력만으로 '수집 성공' 이 되지 않게
        merged = merge_series((prev_ind.get(code) or {}).get("series") or [], series)
        ind[code] = build_indicator(code, label, unit, freq, source, url, merged, series_kind)

    try:
        _put("bok_base", "한국은행 기준금리", "%", "event", "한국은행 기준금리 추이(bok.or.kr)", BOK_BASE_URL,
             parse_bok_base_rate(fetch(BOK_BASE_URL)), "changes")
    except Exception as e:                                    # noqa: BLE001
        errors["bok_base"] = _reason(e)
    for code, (fid, label, unit, freq) in FRED_SERIES.items():
        try:
            _put(code, label, unit, freq, f"FRED {fid}", FRED_PAGE.format(id=fid), parse_fred_csv(fetch(FRED_CSV.format(id=fid))))
        except Exception as e:                                # noqa: BLE001
            errors[code] = _reason(e)
    for code, (cd, label, freq, source) in NAVER_INTEREST_SERIES.items():
        try:
            _put(code, label, "%", freq, source, NAVER_INTEREST.format(cd=cd, page=1),
                 _naver_pages(lambda p, cd=cd: fetch(NAVER_INTEREST.format(cd=cd, page=p), NAVER_ENCODING), parse_naver_interest, pages))
        except Exception as e:                                # noqa: BLE001
            errors[code] = _reason(e)
    try:
        gold: dict[str, dict[str, float]] = {"gold_krw_g": {}, "gold_usd_oz": {}}
        for p in range(1, pages + 1):
            parsed = parse_naver_gold(fetch(NAVER_GOLD.format(page=p), NAVER_ENCODING))
            fresh = [(d, v) for d, v in parsed["gold_krw_g"] if d not in gold["gold_krw_g"]]
            if not fresh:
                break
            for k in gold:
                gold[k].update(parsed[k])
            if p < pages and PAGE_DELAY_S:
                time.sleep(PAGE_DELAY_S)
        _put("gold_krw_g", "국내 금 매매기준율(원/g)", "원/g", "daily", "네이버금융 금 시세(은행 고시)", NAVER_GOLD.format(page=1), sorted(gold["gold_krw_g"].items()))
        _put("gold_usd_oz", "국제 금($/oz)", "$/oz", "daily", "네이버금융 금 시세 '기준 국제 금 시세'", NAVER_GOLD.format(page=1), sorted(gold["gold_usd_oz"].items()))
    except Exception as e:                                    # noqa: BLE001
        errors["gold_krw_g"] = errors["gold_usd_oz"] = _reason(e)
    try:
        _put("usdkrw", "원/달러 매매기준율", "원", "daily", "네이버금융 환율(은행 고시)", NAVER_FX.format(page=1),
             _naver_pages(lambda p: fetch(NAVER_FX.format(page=p), NAVER_ENCODING), parse_naver_fx, pages))
    except Exception as e:                                    # noqa: BLE001
        errors["usdkrw"] = _reason(e)
    carried = {c: v for c, v in prev_ind.items() if c not in ind}   # 이번에 실패한 지표의 마지막 성공 이력(카드에는 쓰지 않음)
    return {"asof": today, "collected_at": datetime.now().isoformat(timespec="seconds"),
            "indicators": ind, "errors": errors, "carried": carried, "n_ok": len(ind)}
