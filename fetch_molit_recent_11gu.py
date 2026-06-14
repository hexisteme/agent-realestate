"""지역 스캔용 — 최근 12개월(rolling) MOLIT 매매 실거래 수집(예산필터·ghost제거용 median).
출력: examples/molit_recent_11gu_20260606.json {lawd: [{apt, area, price, ym}]}. resumable. stdlib + MOLIT_API_KEY.
범용화(2026-06-12): ① MONTHS = 오늘 기준 직전 12개 완결월 rolling(기존 고정 윈도우는 장기 cron 시
점차 노후 — 알려진 버그 해소) ② 지역 = RE_DISTRICTS env(쉼표 구 이름, lawd 자동 해석)로 override."""
import os, json, time, socket, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from agent_realestate import config; config.load_env_file()
from agent_realestate.collectors.lawd import lawd_for_district

# ★2026-06-14 DNS 자가회복: 시스템 getaddrinfo 고장(mDNSResponder 장애 등) 시에도 수집 지속.
#   8.8.8.8 은 리터럴 IP라 DNS 없이 접속 가능 → DoH(dns.google)로 호스트 해석 후 IP 핀.
#   시스템 DNS 정상이면 폴백 미작동(오버헤드 0). 사고배경: 06-14 cron 131/132 실패→캐시 43,046→148 회귀.
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
EP = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
_DEFAULT_DISTRICTS = "양천,강서,구로,노원,도봉,동대문,동작,마포,성북,영등포,종로"
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
OUT = "examples/molit_recent_11gu_20260606.json"

def _t(it, tag):
    e = it.find(tag); return (e.text or "").strip() if e is not None else ""

def fetch(lawd, ym):
    qs = urllib.parse.urlencode({"serviceKey": K, "LAWD_CD": lawd, "DEAL_YMD": ym, "numOfRows": 4000, "pageNo": 1})
    for _ in range(4):
        try:
            with urllib.request.urlopen(f"{EP}?{qs}", timeout=40) as r:
                root = ET.fromstring(r.read().decode("utf-8")); break
        except Exception:
            time.sleep(1.5)
    else:
        return None
    out = []
    for it in root.iter("item"):
        amt = _t(it,"dealAmount").replace(",","")
        if not amt: continue
        out.append({"apt": _t(it,"aptNm"), "area": float(_t(it,"excluUseAr") or 0),
                    "price": int(amt)*10_000, "ym": ym})
    return out

def main():
    cache = json.load(open(OUT)) if os.path.exists(OUT) else {}
    done_keys = {f"{lawd}|{ym}" for lawd in cache.get("_done",[])} if isinstance(cache.get("_done"),list) else set()
    agg = {k:v for k,v in cache.items() if k != "_done"}
    fetched = cache.get("_done", [])
    todo = [(gu,lawd,ym) for gu,lawd in LAWD.items() for ym in MONTHS if f"{lawd}|{ym}" not in set(fetched)]
    print(f"수집 대상 {len(todo)}건 ({len(LAWD)}구 × {len(MONTHS)}개월). 시작…", flush=True)
    for i,(gu,lawd,ym) in enumerate(todo,1):
        rows = fetch(lawd, ym)
        if rows is None:
            print(f"  FAIL {gu}-{ym}", flush=True); continue
        agg.setdefault(lawd, []).extend(rows)
        fetched.append(f"{lawd}|{ym}")
        if i % 20 == 0:
            json.dump({**agg,"_done":fetched}, open(OUT,"w"), ensure_ascii=False)
            print(f"  진행 {i}/{len(todo)} (마지막 {gu}-{ym}: {len(rows)}건)", flush=True)
        time.sleep(0.12)
    json.dump({**agg,"_done":fetched}, open(OUT,"w"), ensure_ascii=False)
    tot = sum(len(v) for k,v in agg.items() if k!="_done")
    print(f"완료. {len(LAWD)}구 실거래 {tot}건 → {OUT}", flush=True)
    for gu,lawd in LAWD.items():
        print(f"  {gu}({lawd}): {len(agg.get(lawd,[]))}건")

if __name__ == "__main__":
    main()
