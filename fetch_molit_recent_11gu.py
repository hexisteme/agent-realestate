"""오늘자 전수 스크린용 — 11개구 최근 12개월 MOLIT 매매 실거래 수집(예산필터·ghost제거용 median).
출력: examples/molit_recent_11gu_20260606.json {lawd: [{apt, area, price, ym}]}. resumable. stdlib + MOLIT_API_KEY."""
import os, json, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from agent_realestate import config; config.load_env_file()

K = os.environ["MOLIT_API_KEY"]
EP = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
LAWD = {"양천":"11470","강서":"11500","구로":"11530","노원":"11350","도봉":"11320","동대문":"11230",
        "동작":"11590","마포":"11440","성북":"11290","영등포":"11560","종로":"11110"}
MONTHS = [f"2025{m:02d}" for m in range(6,13)] + [f"2026{m:02d}" for m in range(1,6)]  # 2025-06 ~ 2026-05
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
