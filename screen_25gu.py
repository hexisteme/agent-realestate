"""오늘자 25개구 전수 스크린 — enumeration(frame_25gu) ↔ 최근 MOLIT median 매칭.
필터: 전용59㎡+(band 59/84) · 세대수≥200 · 예산≤CAP(RE_CAP env, 기본 15억 · MOLIT median) · corridor 제외 · MOLIT-matched(ghost 제거).
gu 귀속: frame 의 gu 태그로 그 구 MOLIT 와 매칭 → 경계누수는 자동 자가교정(타구 MOLIT 불일치→탈락). 단지당 1(complexNo).
screen_11gu.py 의 25구 확장 복제 (WS-1)."""
import os, json, statistics, math
from collections import defaultdict
from pathlib import Path

EX = Path("examples"); CAP = float(os.environ.get("RE_CAP", 15e8)); MIN_UNITS = 200
LAWD = {"종로": "11110", "중구": "11140", "용산": "11170", "성동": "11200", "광진": "11215",
        "동대문": "11230", "중랑": "11260", "성북": "11290", "강북": "11305", "도봉": "11320",
        "노원": "11350", "은평": "11380", "서대문": "11410", "마포": "11440", "양천": "11470",
        "강서": "11500", "구로": "11530", "금천": "11545", "영등포": "11560", "동작": "11590",
        "관악": "11620", "서초": "11650", "강남": "11680", "송파": "11710", "강동": "11740"}
GU_SG = {  # 기존 11구: screen_11gu.py 값 그대로 / 신규 14구: 구명-대표생활권
    "구로": "구로-신도림(여의도권)", "영등포": "영등포-여의도", "동작": "동작-노량진", "강서": "강서-마곡",
    "종로": "종로-도심", "동대문": "동대문-청량리", "성북": "성북-길음", "마포": "마포-공덕",
    "노원": "노원-상계", "도봉": "도봉-창동", "양천": "양천-목동",
    "성동": "성동-왕십리", "강동": "강동-천호", "송파": "송파-잠실", "서초": "서초-서초", "강남": "강남-대치",
    "용산": "용산-이촌", "은평": "은평-불광", "서대문": "서대문-홍제", "관악": "관악-봉천", "금천": "금천-독산",
    "중랑": "중랑-상봉", "강북": "강북-미아", "광진": "광진-구의", "중구": "중구-신당"}
CORRIDOR = {"구로현대", "구로두산", "두산"}     # 구로 corridor
EXCL_PT = {"대림역": (37.49337, 126.89567), "남구로역": (37.48592, 126.88730)}

def hav(a, b, c, d):
    p = math.pi/180; h = math.sin((c-a)*p/2)**2+math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2
    return 2*6371000*math.asin(math.sqrt(h))
def norm(n): return str(n).split("(")[0].replace(" ", "").replace("단지", "")
def base(n): return str(n).split("[")[0].strip()

frame = json.load(open(EX/"frame_25gu_20260710.json"))
molit = json.load(open(EX/"molit_recent_25gu_20260710.json"))

# 구별 (normname, band) → median price
def med_by_band(lawd):
    by59, by84 = defaultdict(list), defaultdict(list)
    for r in molit.get(lawd, []):
        a = r.get("area", 0)
        if 55 <= a < 66: by59[norm(r["apt"])].append(r["price"])
        elif 78 <= a < 95: by84[norm(r["apt"])].append(r["price"])
    return ({k: int(statistics.median(v)) for k, v in by59.items()},
            {k: int(statistics.median(v)) for k, v in by84.items()})

med_cache = {gu: med_by_band(lawd) for gu, lawd in LAWD.items()}
surv = []
for c in frame:
    gu = c["gu"]
    if (c.get("households") or 0) < MIN_UNITS: continue
    if gu == "구로" and base(c["name"]) in CORRIDOR: continue
    lat, lng = c.get("lat"), c.get("lng")
    if lat and lng and any(hav(lat, lng, p[0], p[1]) <= 1100 for p in EXCL_PT.values()): continue  # corridor 좌표
    m59, m84 = med_cache.get(gu, ({}, {}))
    k = norm(c["name"])
    band, m = (84, m84.get(k)) if m84.get(k) else (59, m59.get(k))
    if m is None or m > CAP: continue                  # ghost 제거 + 예산필터
    by = (c.get("builtYm") or "")[:4]
    redev = (c.get("far") or 999) <= 200 and by and by.isdigit() and int(by) <= 1995
    surv.append({"complex_name": c["name"], "district": f"서울 {gu}구", "gu": gu,
                 "complex_no": str(c["complexNo"]), "saenghwalgwon": GU_SG[gu],
                 "households": c.get("households"), "built_year": int(by) if by.isdigit() else None,
                 "far_pct": c.get("far"), "lat": lat, "lng": lng, "type": c.get("type"),
                 "entry_band": band, "molit_median_eok": round(m/1e8, 2),
                 "deal_count": c.get("dealCount"), "redev_proxy": bool(redev)})

# 단지당 1 (complexNo), 최저가
best = {}
for s in surv:
    k = s["complex_no"]
    if k not in best or s["molit_median_eok"] < best[k]["molit_median_eok"]: best[k] = s
surv = sorted(best.values(), key=lambda x: x["molit_median_eok"])
json.dump(surv, open(EX/"screen_25gu_survivors_20260710.json", "w"), ensure_ascii=False, indent=1)

bygu = defaultdict(list)
for s in surv: bygu[s["gu"]].append(s)
nre = sum(1 for s in surv if s["redev_proxy"])
capk = CAP/1e8
print(f"=== 오늘자 25개구 전수 스크린 (전용59+·세대수≥200·≤{capk:.2f}억·corridor제외·MOLIT매칭) → {len(surv)}단지 (재건축proxy {nre}) ===")
for gu in LAWD:
    g = sorted(bygu.get(gu, []), key=lambda x: x["molit_median_eok"])
    print(f"[{gu}] {len(g)}: " + ", ".join(f"{'★' if s['redev_proxy'] else ''}{s['complex_name'][:10]}({s['molit_median_eok']:.1f})" for s in g[:10]))
print(f"\nCAP={capk:.2f}억 → {len(surv)}단지 저장: examples/screen_25gu_survivors_20260710.json")
