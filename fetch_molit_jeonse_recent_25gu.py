"""지역 스캔용 — 최근 12개월(rolling) MOLIT 전월세 실거래 수집 중 순전세만(전세가율·전세중위용).
출력: examples/molit_jeonse_recent_25gu_20260710.json {lawd: [{apt, area, price, ym}]} — 매매(molit_recent_25gu)와
동일 스키마(price 자리에 전세보증금 원). build_explorer._match_records/_median_of 를 매매와 같은 코드로 재사용.
fetch_molit_recent_25gu.py 의 전월세(RTMSDataSvcAptRent) 변형: 25구 default · 구 단위 flush · 호출간 0.3s · 실패시 1회 재시도.
순전세만 사용(monthlyRent==0 — 반전세/월세 제외, fetch_molit_jeonse.py 필터 그대로). RE_DISTRICTS env override 가능."""
from __future__ import annotations  # cron python(/usr/bin/python3=3.9.6)에서 PEP604 `str | None` 런타임 평가 회피
import os, json, time, socket, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from agent_realestate import config; config.load_env_file()
from agent_realestate.collectors.lawd import lawd_for_district

# ★DNS 자가회복(recent_25gu 버전과 동일): 시스템 getaddrinfo 고장 시에도 수집 지속. 정상 시 오버헤드 0.
_real_gai = socket.getaddrinfo
_doh_cache: dict = {}


def _resolve_doh(host: str) -> str | None:
    if host in _doh_cache:
        return _doh_cache[host]
    try:
        req = urllib.request.Request(f"https://8.8.8.8/resolve?name={host}&type=A",
                                     headers={"accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        ips = [a["data"] for a in d.get("Answer", []) if a.get("type") == 1]
        _doh_cache[host] = ips[0] if ips else None
    except Exception:
        _doh_cache[host] = None
    return _doh_cache[host]


def _gai(host, *a, **k):
    try:
        return _real_gai(host, *a, **k)
    except socket.gaierror:
        ip = _resolve_doh(host)
        if ip:
            return _real_gai(ip, *a, **k)   # IP 핀 (Host 헤더는 urllib 이 원 URL 로 유지)
        raise


socket.getaddrinfo = _gai

K = os.environ["MOLIT_API_KEY"]
EP = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
_DEFAULT_DISTRICTS = ("종로,중구,용산,성동,광진,동대문,중랑,성북,강북,도봉,노원,은평,서대문,"
                      "마포,양천,강서,구로,금천,영등포,동작,관악,서초,강남,송파,강동")
LAWD = {}
for _gu in os.environ.get("RE_DISTRICTS", _DEFAULT_DISTRICTS).split(","):
    _gu = _gu.strip()
    _code = lawd_for_district(_gu)
    if _code is None:
        raise SystemExit(f"[RE_DISTRICTS] 알 수 없는 구: {_gu} (서울 25구 이름만 지원 — collectors/lawd.py)")
    LAWD[_gu] = _code

def _rolling_months(n=12):
    """직전 n개 완결월 (당월 제외 — 미완결 월의 표본 왜곡 방지)."""
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return sorted(out)

MONTHS = _rolling_months()
OUT = "examples/molit_jeonse_recent_25gu_20260710.json"

def _t(it, tag):
    e = it.find(tag); return (e.text or "").strip() if e is not None else ""

def fetch(lawd, ym):
    """전월세 응답 → 순전세(월세=0)만 매매 스키마로. price 자리에 전세보증금(원)."""
    qs = urllib.parse.urlencode({"serviceKey": K, "LAWD_CD": lawd, "DEAL_YMD": ym, "numOfRows": 4000, "pageNo": 1})
    for _ in range(2):   # 최초 + 1회 재시도
        try:
            with urllib.request.urlopen(f"{EP}?{qs}", timeout=40) as r:
                root = ET.fromstring(r.read().decode("utf-8")); break
        except Exception:
            time.sleep(1.5)
    else:
        return None
    out = []
    for it in root.iter("item"):
        dep = _t(it, "deposit").replace(",", "")
        if not dep: continue
        mon = _t(it, "monthlyRent").replace(",", "") or "0"
        if int(mon) != 0: continue   # 순전세만 — 반전세/월세(월세>0) 제외 (fetch_molit_jeonse.py 필터 그대로)
        out.append({"apt": _t(it, "aptNm"), "area": float(_t(it, "excluUseAr") or 0),
                    "price": int(dep)*10_000, "ym": ym})   # price=전세보증금(원) — 매매 스키마 하위호환
    return out

def main():
    cache = json.load(open(OUT)) if os.path.exists(OUT) else {}
    agg = {k: v for k, v in cache.items() if k != "_done"}
    fetched = cache.get("_done", []) if isinstance(cache.get("_done"), list) else []
    done = set(fetched)
    pending = sum(1 for lawd in LAWD.values() for ym in MONTHS if f"{lawd}|{ym}" not in done)
    print(f"전세 수집 대상 {len(LAWD)}구 × {len(MONTHS)}개월 = {len(LAWD)*len(MONTHS)}건 (미완료 {pending}). 시작…", flush=True)
    for gi, (gu, lawd) in enumerate(LAWD.items(), 1):
        got = 0
        for ym in MONTHS:
            if f"{lawd}|{ym}" in done: continue
            rows = fetch(lawd, ym)
            if rows is None:
                print(f"  FAIL {gu}-{ym}", flush=True); time.sleep(0.3); continue
            agg.setdefault(lawd, []).extend(rows)
            fetched.append(f"{lawd}|{ym}"); done.add(f"{lawd}|{ym}")
            got += len(rows)
            time.sleep(0.3)
        json.dump({**agg, "_done": fetched}, open(OUT, "w"), ensure_ascii=False)   # 구 단위 flush → 부분 실패 견고
        print(f"  [{gi:>2}/{len(LAWD)}] {gu}({lawd}): +{got}건 (누적 {len(agg.get(lawd,[]))}) flush✓", flush=True)
    tot = sum(len(v) for k, v in agg.items() if k != "_done")
    print(f"완료. {len(LAWD)}구 순전세 {tot}건 → {OUT}", flush=True)
    for gu, lawd in LAWD.items():
        print(f"  {gu}({lawd}): {len(agg.get(lawd,[]))}건")

if __name__ == "__main__":
    main()
