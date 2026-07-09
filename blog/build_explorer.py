"""방문자 필터형 탐색기 (A 모델: 실명 개별 매물 · 점수 없음) — 라이브 site/ 배포용 dataset+explorer 생성.

설계 결정(2026-06-16 사용자 확정 — "실명 개별 매물, 점수화 안 함, 사실 수치만"):
  · 실명 개별 단지  — 단지명 + 위치 + 전용면적/평형 + 세대수 + 준공연도 + 유형.
  · 공공 실거래만    — 국토부 RTMS 12개월 동일평형 중위(공공데이터라 정확값) + 평단가(파생). MOLIT 매칭 없으면 가격 null.
  · 사설 호가 배제    — 네이버/KB 호가(listing.price_krw)는 dataset 에 일절 미포함(DB권).
  · 점수 없음        — 자체 구조점수·순위·등급·강점축·세그먼트 전부 미산출/미노출(명예훼손 표면 0 = 아실 모델).
  · 가드 동반         — 면책·CC-BY-NC·출처·이의제기(takedown)를 dataset 루트 + explorer 모든 뷰 고정.

근거: [[reference-korea-re-publishing-legal]] — 공공 실거래가 named 재이용 합법(공공데이터법) · 점수 없으면 사실적시
명예훼손 구성요건 자체가 사라짐 · 사설 호가 정량 재게시는 DB권(대법 2021도1533)이라 band/배제.
"""
from __future__ import annotations
import os, re, json, glob, statistics as st
from datetime import date

from agent_realestate.collectors.naver_live import load_candidates

# 발행 구(run_daily.GU_LAWD 와 동일 — 단일소스화는 후속)
GU_LAWD = {"양천":"11470","강서":"11500","구로":"11530","동대문":"11230","마포":"11440",
           "성북":"11290","영등포":"11560","종로":"11110","동작":"11590","노원":"11350","도봉":"11320"}

DISCLAIMER = ("본 자료는 개인 연구·정보 공유이며 투자자문·매수권유가 아닙니다. "
              "수치는 국토교통부 공공 실거래가 기준이며 게시 시점 스냅샷입니다. "
              "거래 전 반드시 국토부 실거래가·현장에서 재확인하십시오.")
SOURCES = [
    {"name": "국토교통부 아파트 실거래가(RTMS) 공공데이터", "url": "https://rt.molit.go.kr",
     "license": "공공데이터포털(data.go.kr) 이용약관 — 출처표시 후 영리 포함 재이용 가능"},
    {"name": "단지 기본정보(세대수·준공·전용면적·유형)", "note": "공공 건축물대장 등 공개정보 기준."},
]

def _takedown() -> str:
    """이의제기(takedown) 연락처 — RE_EMAIL_TO(.env, gitignored)에서 호출시점 주입(공개 repo 미노출)."""
    return ("사실 오류·이의제기 접수 시 확인 후 수정·삭제합니다. 연락처: "
            + os.environ.get("RE_EMAIL_TO", "(운영자 연락처 — RE_EMAIL_TO 환경변수)"))

# 필터/표시 band (실명이라 익명화 목적 아님 — 필터 입도용)
def area_band(a: float) -> str:
    a = round(a)
    return "~59㎡" if a < 60 else "60-84㎡" if a < 85 else "85-114㎡" if a < 115 else "115㎡+"
def decade(y: int) -> str:
    return f"{(y//10)*10}년대" if y else "—"
def unit_band(u: int) -> str:
    return ("2천세대급" if u >= 2000 else "1천세대급" if u >= 1000 else
            "5백세대급" if u >= 500 else "2백세대급" if u >= 200 else "소규모")
def product_type(c) -> str:
    nm = c.listing.complex_name or ""
    if "도시형" in nm: return "도생"
    if "주상복합" in nm or "오피스텔" in nm: return "주상복합"
    return "아파트"

core = lambda nm: re.sub(r"[\(\[].*?[\)\]]", "", nm).replace(" ", "")


def _match_records(c, lawd, molit) -> list[dict]:
    """동일평형(±3.5㎡)·이름매칭된 lawd 의 12개월 RTMS 레코드(price·ym 보존 — tier_now 파생용)."""
    cn = core(c.listing.complex_name); ar = c.listing.area_exclusive_m2
    return [r for r in molit.get(lawd, []) if r.get("price") and abs(r["area"] - ar) <= 3.5
            and (core(r["apt"]) in cn or cn in core(r["apt"]) or core(r["apt"])[:4] == cn[:4])]


def _median_of(recs: list[dict]) -> tuple[float | None, int]:
    """매칭표본 → 이상치(−40%)컷 후 중위(억)·표본수 n. molit_median 의 결정론 코어."""
    px = [r["price"] for r in recs]
    if len(px) < 2:
        return None, 0
    m0 = st.median(px); px = [p for p in px if p >= m0 * 0.6]
    return (round(st.median(px) / 1e8, 2) if px else None), len(px)


def molit_median(c, lawd, molit) -> tuple[float | None, int]:
    """공공 RTMS 12개월 동일평형 중위(억) — run_daily.gen_gu.med 와 동일 로직. 사설 호가 fallback 없음."""
    return _median_of(_match_records(c, lawd, molit))


def _pctile(xs: list[float], q: float) -> float:
    """선형보간 분위수(numpy 기본 type-7). xs 는 오름차순 정렬 가정, q∈[0,1]."""
    n = len(xs)
    if n == 1:
        return float(xs[0])
    pos = (n - 1) * q
    lo = int(pos); frac = pos - lo
    return float(xs[lo]) if lo + 1 >= n else xs[lo] + (xs[lo + 1] - xs[lo]) * frac


def _month_windows(asof: str) -> tuple[set, set]:
    """asof 기준 직전 3개 완결월(recent) / 그 이전 9개월(4~12개월 전, prior)의 'YYYYMM' 집합.
    MOLIT ym 키와 매칭(fetch_molit_recent_11gu._rolling_months 와 동일 윈도우 정의 — 당월 제외)."""
    y, m = int(asof[:4]), int(asof[5:7])
    yms = []
    for _ in range(12):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        yms.append(f"{y}{m:02d}")
    return set(yms[:3]), set(yms[3:12])


def derive_tier_now(recs: list[dict], asof: str) -> dict:
    """동일평형 매칭 12개월 RTMS 표본에서 무점수 사실 3종 파생 — molit_median 과 *동일 outlier-cut* 표본 사용.
      ① 분포   : 실거래 P25·P75(억) — 중위와 함께 'P25–중위–P75' 협상 레인지(분위수 사실, 등급화 없음).
      ② 추세   : 최근3개월 중위 vs 직전9개월(4~12개월 전) 중위, 방향(▲/▼/—)·변화%. 과거 비교 사실(전망 단정 없음).
      ③ 52주위치: 최근3개월 체결 중위가 12개월(52주) 실거래 min~max 레인지에서 차지하는 위치(0~100%).
                 '현재'=최근3개월 중위(헤드라인 12개월 중위와 기준점 다름) — 지금 고점/저점 근처 신호 보존.
    표본부족 가드(오도방지): 분포 n<5(분위수 불안정), 52주위치 n<5 또는 최근3개월 표본<2(현재 시세 미정),
    추세 각 분기 표본<2 → None. 무점수 정합: 분위수·과거중위비교·레인지내 위치(전부 사실값)만 — 점수·등급·추천·전망 없음."""
    px_all = [r["price"] for r in recs]
    out = {"p25_eok": None, "p75_eok": None, "trend_dir": None, "trend_pct": None, "pos_52w": None}
    if len(px_all) < 2:
        return out
    m0 = st.median(px_all)
    clean = [r for r in recs if r["price"] >= m0 * 0.6]   # molit_median(_median_of) 과 동일 컷
    px = sorted(r["price"] for r in clean)
    n = len(px)
    # ① IQR 분포 — n>=5 일 때만(4점 이하는 사분위 불안정 → null)
    if n >= 5:
        out["p25_eok"] = round(_pctile(px, 0.25) / 1e8, 2)
        out["p75_eok"] = round(_pctile(px, 0.75) / 1e8, 2)
    # ② 추세 — 각 분기 표본>=2 일 때만(분기 1점은 변화율 노이즈 → null)
    recent_set, prior_set = _month_windows(asof)
    rp = [r["price"] for r in clean if r.get("ym") in recent_set]
    pp = [r["price"] for r in clean if r.get("ym") in prior_set]
    if len(rp) >= 2 and len(pp) >= 2:
        prior_med = st.median(pp)
        chg = round((st.median(rp) - prior_med) / prior_med * 100, 1)
        out["trend_pct"] = chg
        out["trend_dir"] = "▲" if chg > 0 else "▼" if chg < 0 else "—"
    # ③ 52주 위치 — 최근3개월 체결 중위(rp)의 12개월 레인지 내 위치. n>=5 + 레인지 존재(max>min) + 최근표본>=2 모두 충족 시.
    #    rp 는 clean 부분집합이라 중위는 [min,max] 내부 → 0~100 보장(방어적 clamp). 최근 거래 없으면 '현재 위치' 미정 = null.
    if n >= 5 and px[-1] > px[0] and len(rp) >= 2:
        pos = (st.median(rp) - px[0]) / (px[-1] - px[0]) * 100
        out["pos_52w"] = max(0, min(100, round(pos)))
    return out


# 사용자 고정 제외 규칙([[feedback-realestate-scan-exclusions]], 2026-06-06 확인) — 실명 공개라 hard 적용.
MIN_UNITS = 200                                   # ① 세대수<200 제외(환금성 우려)
CORRIDOR_EXCLUDE = {"구로현대", "구로두산", "두산"}  # ② 대림역~남구로역 corridor(구로동) hard 제외 — gu==구로 한정


def build_dataset(universe: str, molit_path: str, asof: str, today: str) -> dict:
    uni = load_candidates(universe)
    molit = json.load(open(molit_path))
    # complex_no: Candidate 도메인 미포함 필드 → raw JSON 에서 이름매핑으로 추출
    raw_uni = json.load(open(universe, encoding="utf-8"))
    complex_no_map = {re.sub(r"\[.*?\]", "", d.get("complex_name", "")).strip(): str(d["complex_no"])
                      for d in raw_uni if d.get("complex_no")}
    rows = []
    excluded = {"under_min_units": 0, "corridor": 0}
    for gu, lawd in GU_LAWD.items():
        for c in (x for x in uni if gu in x.district):
            disp = re.sub(r"\[.*?\]", "", c.listing.complex_name).strip()
            if c.units < MIN_UNITS:               # ① 세대수 하한
                excluded["under_min_units"] += 1; continue
            if gu == "구로" and disp in CORRIDOR_EXCLUDE:  # ② corridor hard 제외
                excluded["corridor"] += 1; continue
            matched = _match_records(c, lawd, molit)
            md, n = _median_of(matched)
            tier = derive_tier_now(matched, asof)            # 동일표본 파생 무점수 사실 3종(분포·추세·52주위치)
            pyeong = round(c.listing.area_exclusive_m2 / 3.305785, 1)
            rows.append({
                "name": disp,                                  # 실명 — 내부 주석태그([재발견]·[주상복합] 등) 제거
                "gu": gu, "saeng": c.saenghwalgwon,
                "area_m2": round(c.listing.area_exclusive_m2, 1),
                "area_band": area_band(c.listing.area_exclusive_m2),
                "pyeong": pyeong,
                "units": c.units, "units_band": unit_band(c.units),
                "built_year": c.built_year, "decade": decade(c.built_year),
                "product_type": product_type(c),
                "molit_recent_eok": md,                          # 공공 실거래 중위(없으면 null) — 사설 호가 미포함
                "molit_n": n,
                "molit_p25_eok": tier["p25_eok"],                # ① 동일평형 실거래 P25(억) — 협상 레인지 하단
                "molit_p75_eok": tier["p75_eok"],                # ① 동일평형 실거래 P75(억) — 협상 레인지 상단
                "molit_trend_dir": tier["trend_dir"],            # ② 최근3개월 vs 직전9개월 중위 방향(▲/▼/—)
                "molit_trend_pct": tier["trend_pct"],            # ② 변화%(과거 비교 사실 — 전망 아님)
                "molit_pos_52w": tier["pos_52w"],                # ③ 최근3개월 체결 중위의 12개월(52주) 레인지 내 위치(%)
                "pyeong_price_man": round(md * 1e8 / pyeong / 1e4) if (md and pyeong) else None,  # 평단가(만원/평) 파생 공개사실
                # ★ 점수·순위·등급·강점축·세그먼트 일절 없음(A 모델) — 사실 수치만.
                # ── 입지·인프라 사실 필드(A 모델 확장, 2026-07-03) ──
                "slope_pct": round(c.slope_pct, 1) if c.slope_pct is not None else None,    # 경사도(%) [사실]
                "far_pct": round(c.far_pct, 1) if c.far_pct else None,                       # 용적률(%) [사실]
                "bcr_pct": round(c.bcr_pct, 1) if c.bcr_pct is not None else None,           # 건폐율(%) [사실]
                "review_score": round(c.review_score, 1) if c.review_score is not None else None,  # 네이버 주민 평점(0~5)
                "academy_exam": c.academy_exam,                                               # 입시학원 수(학원가 사실)
                "subway_m": int((c.infra or {}).get("subway_m")) if (c.infra or {}).get("subway_m") else None,  # 최근접 지하철(m)
                "cbd_km": c.cbd_km,                                                           # 주요 업무지구까지(km)
                "cbd_name": c.cbd_name or "",
                "complex_no": complex_no_map.get(disp, ""),                                   # 네이버 매물링크용
                "facing": c.listing.facing or "",                                              # 향(남향 등)
                # ── 단지·학군·시세 사실 필드(A 모델 확장, 2026-07-03 2차) ──
                # 대지지분(평) — 재건축 환급 단위. 추정값(is_estimate, universe 플레이스홀더 11.0)은
                # 발행 제외: A모델 = 확인된 사실만 (2026-07-07, 114/117 동일 11.0평 오발행 수정).
                "land_share_pyeong": (c.land_share_pyeong
                                      if c.land_share_pyeong and not c.land_share_is_estimate else None),
                "floor": c.listing.floor,                                                      # 층/전체층("2/15층")
                "tukmokgo_pct": c.tukmokgo_pct,                                               # 특목/자사고 진학률 %(학군 팩트)
                "school_achievement": c.school_achievement,                                    # 학업성취도 보통학력이상 %(학교알리미)
                "gu_jeonse_ratio_pct": round(c.gu_jeonse_ratio * 100, 1) if c.gu_jeonse_ratio else None,  # 구 전세가율(%) 사실
                "trade_annual": round(c.trade_annual, 1) if c.trade_annual is not None else None,  # 연평균 실거래수 (유동성)
                "transit": c.transit or "",                                                    # 입지 한 줄(역·노선)
                "mart_800": int((c.infra or {}).get("mart_800")) if (c.infra or {}).get("mart_800") is not None else None,
                "hosp_800": int((c.infra or {}).get("hosp_800")) if (c.infra or {}).get("hosp_800") is not None else None,
                "park_1k": int((c.infra or {}).get("park_1k")) if (c.infra or {}).get("park_1k") is not None else None,
                "dept_1500": int((c.infra or {}).get("dept_1500")) if (c.infra or {}).get("dept_1500") is not None else None,
                "heating": c.heating or "",
                "corridor_type": c.corridor_type or "",
                "parking_per_unit": c.parking_per_unit,
                "builder": c.builder or "",
                "nearest_elem_school": c.nearest_elem_school,                                 # 인근 초등학교명(학군 팩트·근접성)
                "gongsi_man": c.gongsi_man,                                                    # 공동주택 공시가격(만원)
                "maint_fee_won": c.maint_fee_won,                                              # K-apt 세대당 월 관리비(원)
            })
    rows.sort(key=lambda x: (x["gu"], x["name"], x["area_m2"]))
    return {
        "schema_version": "explorer-facts/1", "generated": today, "data_asof": asof,
        "license": "CC-BY-NC-4.0", "disclaimer": DISCLAIMER, "takedown": _takedown(), "sources": SOURCES,
        "count": len(rows), "complexes": rows, "excluded": excluded,
    }


def write_out(ds: dict, outdir: str) -> dict:
    os.makedirs(outdir, exist_ok=True)
    json.dump(ds, open(f"{outdir}/dataset.json", "w"), ensure_ascii=False, separators=(",", ":"))
    open(f"{outdir}/explorer.html", "w").write(EXPLORER_HTML)
    priced = sum(1 for r in ds["complexes"] if r["molit_recent_eok"] is not None)
    # SOFT 커버리지 경고(발행은 지속) — UI 컬럼만 있고 수집 배선이 끊겨 전량 null 로
    # 조용히 나가던 사고(2026-07-07, 7필드 0/117) 재발 방지. 임계 50%.
    # 배선 완료 필드만 강경고 — 승인대기(고정 발화)와 섞으면 알람 피로로 진짜 단절을 놓친다.
    # land_share_pyeong 은 is_estimate 게이트 의도적 억제라 경고 대상 아님(실측 소스 확보 시 재활성).
    n = len(ds["complexes"]) or 1
    _cov = lambda k: sum(1 for r in ds["complexes"] if r.get(k) not in (None, "", "-", "—"))
    for k in ("heating", "corridor_type", "parking_per_unit", "builder", "nearest_elem_school"):
        nn = _cov(k)
        if nn / n < 0.5:
            print(f"⚠️ [coverage-soft] {k}: {nn}/{n} ({nn*100//n}%) — 수집 배선 점검 필요")
    for k in ("gongsi_man", "maint_fee_won"):   # data.go.kr 활용신청 대기(2026-07-07)
        nn = _cov(k)
        if nn / n < 0.5:
            print(f"ℹ️ [coverage-pending] {k}: {nn}/{n} — 승인 대기, 승인 후 수집 배치 실행")
    return {"complexes": ds["count"], "priced": priced, "outdir": outdir}


EXPLORER_HTML = r"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>서울 부동산 탐색기 — 공공 실거래 + 단지정보</title>
<meta name=description content="서울 자치구 아파트 단지를 예산·평형·연식·유형으로 필터하고 공공 실거래가로 정렬. 국토부 실거래가 + 세대수·연식 등 공개정보. 투자자문 아님.">
<style>
:root{--bd:#e3e6ea;--mut:#667;--ac:#0969da;--bg:#f6f8fa}
*{box-sizing:border-box}body{font:15px/1.6 -apple-system,Pretendard,Segoe UI,sans-serif;margin:0;color:#1a1a1a}
header{padding:14px 18px;border-bottom:1px solid var(--bd);position:sticky;top:0;background:#fff;z-index:5}
h1{font-size:18px;margin:0 0 4px}.disc{font-size:12px;color:var(--mut)}
.wrap{display:grid;grid-template-columns:280px 1fr;min-height:calc(100vh - 64px)}
.panel{padding:16px 18px;border-right:1px solid var(--bd);background:var(--bg)}
.panel h3{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:18px 0 8px}
.panel h3:first-child{margin-top:0}
.chip{display:inline-block;padding:4px 10px;margin:3px 4px 3px 0;border:1px solid var(--bd);border-radius:14px;background:#fff;cursor:pointer;font-size:13px}
.chip.on{background:var(--ac);color:#fff;border-color:var(--ac)}
.results{padding:14px 18px}
.row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}
input[type=search]{width:100%;padding:8px 10px;border:1px solid var(--bd);border-radius:8px;font-size:14px}
input[type=number]{width:70px;padding:6px 8px;border:1px solid var(--bd);border-radius:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--bd);text-align:left;white-space:nowrap}
th{background:#fafbfc;color:#334;font-weight:600;cursor:pointer;position:sticky;top:0;user-select:none}
th.num,td.num{text-align:right}th .ar{color:var(--ac);font-size:11px}
tbody tr:hover{background:#f6faff}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;background:#eef2f6;color:#445}
.muted{color:var(--mut)}.sup{font-size:11px;color:var(--ac)}
.foot{font-size:12px;color:var(--mut);border-top:1px solid var(--bd);padding:14px 18px;margin-top:16px}
.extra-col{display:none}
#tbl.show-extra .extra-col{display:table-cell}
.toggle-btn{display:inline-block;padding:4px 12px;border:1px solid var(--bd);border-radius:14px;background:#fff;cursor:pointer;font-size:12px;margin:2px 0;color:#334}
.toggle-btn.on{background:var(--ac);color:#fff;border-color:var(--ac)}
@media(max-width:760px){.wrap{grid-template-columns:1fr}.panel{border-right:0;border-bottom:1px solid var(--bd)}
  table{font-size:12px}th,td{padding:6px 7px}}
</style></head><body>
<header>
  <h1>서울 부동산 탐색기 <span class=muted style="font-size:13px">— 공공 실거래 + 단지정보</span></h1>
  <div class=disc id=disc>불러오는 중…</div>
</header>
<div class=wrap>
  <aside class=panel id=filters></aside>
  <main class=results>
    <div class=row>
      <div><b id=count></b> <span class=muted id=countsub></span></div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class=muted style="font-size:12px">열 머리글 클릭 = 정렬</span>
        <button class=toggle-btn id=infraToggle onclick="const t=document.getElementById('tbl');t.classList.toggle('show-extra');this.classList.toggle('on');this.textContent=this.classList.contains('on')?'인프라 숨기기':'📍 인프라 보기'">📍 인프라 보기</button>
      </div>
    </div>
    <div style="overflow:auto"><table id=tbl><thead id=thead></thead><tbody id=tbody></tbody></table></div>
    <div class=foot id=foot></div>
  </main>
</div>
<script>
const S={q:"",gu:new Set(),area:new Set(),decade:new Set(),ptype:new Set(),emin:null,emax:null,units_min:null,ppmin:null,ppmax:null,sort:"molit_recent_eok",dir:-1};
let DB=null;
const COLS=[
  {k:"name",t:"단지명",num:false},
  {k:"gu",t:"구·생활권",num:false,get:r=>r.gu+(r.saeng?" · "+r.saeng:"")},
  {k:"area_m2",t:"전용㎡",num:true,get:r=>r.area_m2,fmt:r=>r.area_m2+"㎡ <span class=muted>("+r.pyeong+"평)</span>"},
  {k:"units",t:"세대수",num:true,fmt:r=>r.units.toLocaleString()},
  {k:"built_year",t:"준공",num:true,fmt:r=>r.built_year},
  {k:"product_type",t:"유형",num:false,fmt:r=>`<span class=tag>${r.product_type}</span>`},
  {k:"molit_recent_eok",t:"공공 실거래(중위)",num:true,
     fmt:r=>r.molit_recent_eok!=null?`<b>${r.molit_recent_eok}억</b><sup class=sup> F</sup> <span class=muted>n${r.molit_n}</span>`
       +(r.molit_p25_eok!=null?`<br><span class=muted>${r.molit_p25_eok}–${r.molit_recent_eok}–${r.molit_p75_eok} <span class=sup>P25·중위·P75</span></span>`:``)
       :`<span class=muted>—</span>`},
  {k:"molit_trend_pct",t:"추세<span class=muted style=font-weight:400> 3/9개월</span>",num:true,
     fmt:r=>r.molit_trend_pct!=null?`${r.molit_trend_dir}${Math.abs(r.molit_trend_pct)}%<sup class=sup> F</sup>`:`<span class=muted>—</span>`},
  {k:"molit_pos_52w",t:"52주위치<span class=muted style=font-weight:400> 최근3개월</span>",num:true,
     fmt:r=>r.molit_pos_52w!=null?`${r.molit_pos_52w}%<sup class=sup> F</sup>`:`<span class=muted>—</span>`},
  {k:"pyeong_price_man",t:"평단가",num:true,fmt:r=>r.pyeong_price_man!=null?`${r.pyeong_price_man.toLocaleString()}만`:`<span class=muted>—</span>`},
  // ── 입지·인프라 추가 열(기본 숨김, '인프라 보기' 토글로 표시) ──
  {k:"subway_m",t:"지하철(m)",num:true,extra:true,
   fmt:r=>r.subway_m!=null?`${r.subway_m.toLocaleString()}m`:`<span class=muted>—</span>`},
  {k:"cbd_km",t:"CBD(km)",num:true,extra:true,
   fmt:r=>r.cbd_km!=null?`${r.cbd_km}km<br><span class=muted>${esc(r.cbd_name||"")}</span>`:`<span class=muted>—</span>`},
  {k:"slope_pct",t:"경사도(%)",num:true,extra:true,
   fmt:r=>r.slope_pct!=null?`${r.slope_pct}%`:`<span class=muted>—</span>`},
  {k:"far_pct",t:"용적/건폐율",num:true,extra:true,
   fmt:r=>r.far_pct!=null?`${r.far_pct}%`+(r.bcr_pct!=null?`<br><span class=muted>${r.bcr_pct}%건폐</span>`:""):`<span class=muted>—</span>`},
  {k:"academy_exam",t:"학원가(곳)",num:true,extra:true,
   fmt:r=>r.academy_exam!=null?`${r.academy_exam}곳`:`<span class=muted>—</span>`},
  {k:"review_score",t:"주민평점",num:true,extra:true,
   fmt:r=>r.review_score!=null?`${r.review_score}/5`:`<span class=muted>—</span>`},
  {k:"complex_no",t:"네이버 매물",num:false,extra:true,
   fmt:r=>r.complex_no?`<a href="https://m.land.naver.com/complex/info/${esc(r.complex_no)}" target=_blank rel=noopener>매물보기</a>`:`<span class=muted>—</span>`},
  // ── 단지·학군·시세 사실 열(2026-07-03 2차) ──
  {k:"land_share_pyeong",t:"대지지분(평)",num:true,extra:true,
   fmt:r=>r.land_share_pyeong!=null?`${r.land_share_pyeong}평`:`<span class=muted>—</span>`},
  {k:"floor",t:"층",num:false,extra:true,
   fmt:r=>r.floor?esc(r.floor):`<span class=muted>—</span>`},
  {k:"tukmokgo_pct",t:"특목고 진학률",num:true,extra:true,
   fmt:r=>r.tukmokgo_pct!=null?`${r.tukmokgo_pct}%`:`<span class=muted>—</span>`},
  {k:"school_achievement",t:"학업성취도",num:true,extra:true,
   fmt:r=>r.school_achievement!=null?`${r.school_achievement}%`:`<span class=muted>—</span>`},
  {k:"gu_jeonse_ratio_pct",t:"전세가율",num:true,extra:true,
   fmt:r=>r.gu_jeonse_ratio_pct!=null?`${r.gu_jeonse_ratio_pct}%<span class=muted> 구중위</span>`:`<span class=muted>—</span>`},
  {k:"trade_annual",t:"연거래수",num:true,extra:true,
   fmt:r=>r.trade_annual!=null?`${r.trade_annual}건/년`:`<span class=muted>—</span>`},
  {k:"transit",t:"입지",num:false,extra:true,
   fmt:r=>r.transit?`<span class=muted>${esc(r.transit)}</span>`:`<span class=muted>—</span>`},
  {k:"mart_800",t:"마트(800m)",num:true,extra:true,
   fmt:r=>r.mart_800!=null?`${r.mart_800}개`:`<span class=muted>—</span>`},
  {k:"hosp_800",t:"의료(800m)",num:true,extra:true,
   fmt:r=>r.hosp_800!=null?`${r.hosp_800}개`:`<span class=muted>—</span>`},
  {k:"park_1k",t:"공원(1km)",num:true,extra:true,
   fmt:r=>r.park_1k!=null?`${r.park_1k}개`:`<span class=muted>—</span>`},
  {k:"dept_1500",t:"백화점(1.5km)",num:true,extra:true,
   fmt:r=>r.dept_1500!=null?`${r.dept_1500}개`:`<span class=muted>—</span>`},
  {k:"maint_fee_won",t:"월관리비",num:true,extra:true,
   fmt:r=>r.maint_fee_won!=null?`${Math.round(r.maint_fee_won/10000)}만원`:`<span class=muted>—</span>`},
  {k:"heating",t:"난방방식",num:false,extra:true,
   fmt:r=>r.heating?esc(r.heating):`<span class=muted>—</span>`},
  {k:"corridor_type",t:"복도유형",num:false,extra:true,
   fmt:r=>r.corridor_type?esc(r.corridor_type):`<span class=muted>—</span>`},
  {k:"parking_per_unit",t:"세대당주차",num:true,extra:true,
   fmt:r=>r.parking_per_unit!=null?`${r.parking_per_unit}대`:`<span class=muted>—</span>`},
  {k:"builder",t:"시공사",num:false,extra:true,
   fmt:r=>r.builder?esc(r.builder):`<span class=muted>—</span>`},
  {k:"nearest_elem_school",t:"인근초등학교",num:false,extra:true,
   fmt:r=>r.nearest_elem_school?esc(r.nearest_elem_school):`<span class=muted>—</span>`},
  {k:"gongsi_man",t:"공시가(만원)",num:true,extra:true,
   fmt:r=>r.gongsi_man!=null?`${r.gongsi_man.toLocaleString()}만`:`<span class=muted>—</span>`},
];
const uniq=a=>[...new Set(a)];
const esc=s=>(s==null?"":String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

fetch("./dataset.json").then(r=>r.json()).then(d=>{DB=d;init();render();});

function init(){
  document.getElementById("disc").innerHTML="⚖ "+esc(DB.disclaimer)+" · 데이터 "+DB.data_asof+" · "+DB.license;
  const cx=DB.complexes, F=document.getElementById("filters");
  const mk=(title,key,vals)=>`<h3>${title}</h3>`+vals.map(v=>`<span class=chip data-k="${key}" data-v="${esc(v)}">${esc(v)}</span>`).join("");
  F.innerHTML=
     `<h3>단지명 검색</h3><input type=search id=q placeholder="예: 목동, 래미안…">`
    +mk("자치구","gu",uniq(cx.map(x=>x.gu)).sort())
    +mk("평형","area",["~59㎡","60-84㎡","85-114㎡","115㎡+"].filter(b=>cx.some(x=>x.area_band===b)))
    +mk("연식","decade",uniq(cx.map(x=>x.decade)).sort())
    +mk("유형","ptype",uniq(cx.map(x=>x.product_type)).sort())
    +`<h3>세대수 최소</h3><div>`
    +["200+","500+","1000+","2000+"].map(v=>`<span class=chip data-k="units_min" data-v="${v}">${v}</span>`).join("")
    +`</div>`
    +`<h3>공공 실거래(억)</h3><div>최소 <input type=number id=emin min=0 step=1> ~ 최대 <input type=number id=emax min=0 step=1></div>`
    +`<p class=muted style="margin-top:4px;font-size:12px">입력 시 거래 없는 단지(—)는 제외됩니다.</p>`
    +`<h3>평단가(만원/평)</h3><div>최소 <input type=number id=ppmin min=0 step=100> ~ 최대 <input type=number id=ppmax min=0 step=100></div>`
    +`<p class=muted style="margin-top:4px;font-size:12px">입력 시 실거래 없는 단지는 제외됩니다.</p>`;
  F.querySelectorAll(".chip[data-k='units_min']").forEach(c=>c.onclick=()=>{
    const v=+c.dataset.v; const prev=S.units_min;
    S.units_min=(prev===v?null:v);
    F.querySelectorAll(".chip[data-k='units_min']").forEach(x=>x.classList.toggle("on",+x.dataset.v===S.units_min));
    render();});
  F.querySelectorAll(".chip:not([data-k='units_min'])").forEach(c=>c.onclick=()=>{const k=c.dataset.k,v=c.dataset.v;S[k].has(v)?S[k].delete(v):S[k].add(v);c.classList.toggle("on");render();});
  document.getElementById("q").oninput=e=>{S.q=e.target.value.trim();render();};
  document.getElementById("emin").oninput=e=>{S.emin=e.target.value===""?null:+e.target.value;render();};
  document.getElementById("emax").oninput=e=>{S.emax=e.target.value===""?null:+e.target.value;render();};
  document.getElementById("ppmin").oninput=e=>{S.ppmin=e.target.value===""?null:+e.target.value;render();};
  document.getElementById("ppmax").oninput=e=>{S.ppmax=e.target.value===""?null:+e.target.value;render();};
  // 헤더
  document.getElementById("thead").innerHTML="<tr>"+COLS.map(c=>
    `<th class="${[c.num?'num':'',c.extra?'extra-col':''].filter(Boolean).join(' ')}" data-k="${c.k}">${c.t}<span class=ar data-ar="${c.k}"></span></th>`).join("")+"</tr>";
  document.querySelectorAll("#thead th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; if(S.sort===k)S.dir*=-1; else{S.sort=k;S.dir=(COLS.find(c=>c.k===k).num?-1:1);} render();});
  document.getElementById("foot").innerHTML=
    "<b>출처·고지</b><br>"+DB.sources.map(s=>`• ${esc(s.name)}${s.url?` — <a href="${esc(s.url)}">${esc(s.url)}</a>`:""}${s.note?` <span class=muted>(${esc(s.note)})</span>`:""}`).join("<br>")
    +`<br>• 가격은 국토부 공공 실거래가(12개월 동일평형 중위)만 표시 — 사설 시세(호가)는 게재하지 않습니다. <sup class=sup>F</sup>=공공 실거래 사실, n=표본수.`
    +`<br>• P25·중위·P75 = 동일평형 실거래 분위수(협상 레인지). 추세 = 최근3개월 중위 vs 직전9개월 중위(과거 비교 사실 — 전망 아님). 52주위치 = <b>최근 3개월 체결 중위</b>가 12개월(52주) 실거래 최저~최고 레인지에서 차지하는 위치(%) — 헤드라인 중위(12개월)와 기준점 다름(최근 거래 없으면 —). 표본 부족 구간은 —.`
    +`<br>• 자체 평가·점수·순위를 매기지 않습니다. 공개된 사실 수치만 제공합니다.`
    +`<br>• ${esc(DB.takedown)}`;
}
function passFilter(x){
  if(S.q && !x.name.toLowerCase().includes(S.q.toLowerCase())) return false;
  if(S.gu.size&&!S.gu.has(x.gu)) return false;
  if(S.area.size&&!S.area.has(x.area_band)) return false;
  if(S.decade.size&&!S.decade.has(x.decade)) return false;
  if(S.ptype.size&&!S.ptype.has(x.product_type)) return false;
  if(S.units_min!=null&&x.units<S.units_min) return false;
  if(S.emin!=null||S.emax!=null){
    if(x.molit_recent_eok==null) return false;
    if(S.emin!=null&&x.molit_recent_eok<S.emin) return false;
    if(S.emax!=null&&x.molit_recent_eok>S.emax) return false;
  }
  if(S.ppmin!=null||S.ppmax!=null){
    if(x.pyeong_price_man==null) return false;
    if(S.ppmin!=null&&x.pyeong_price_man<S.ppmin) return false;
    if(S.ppmax!=null&&x.pyeong_price_man>S.ppmax) return false;
  }
  return true;
}
function render(){
  const col=COLS.find(c=>c.k===S.sort);
  const get=r=>col.get?col.get(r):r[S.sort];
  let cx=DB.complexes.filter(passFilter).sort((a,b)=>{
    let va=get(a),vb=get(b);
    if(va==null)return 1; if(vb==null)return -1;          // null(거래없음) 항상 뒤로
    if(typeof va==="number")return (va-vb)*S.dir;
    return String(va).localeCompare(String(vb),"ko")*S.dir;
  });
  document.getElementById("count").textContent=cx.length.toLocaleString()+"개 매물";
  document.getElementById("countsub").textContent="("+DB.count.toLocaleString()+"개 중) · 단지×평형 단위";
  document.querySelectorAll("[data-ar]").forEach(s=>s.textContent="");
  const ar=document.querySelector(`[data-ar="${S.sort}"]`); if(ar)ar.textContent=S.dir<0?"▼":"▲";
  document.getElementById("tbody").innerHTML=cx.slice(0,600).map(r=>"<tr>"+COLS.map(c=>
    `<td class="${[c.num?'num':'',c.extra?'extra-col':''].filter(Boolean).join(' ')}">${c.fmt?c.fmt(r):esc(c.get?c.get(r):r[c.k])}</td>`).join("")+"</tr>").join("")
    +(cx.length>600?`<tr><td colspan=${COLS.length} class=muted>…상위 600개 표시(필터를 좁히면 전체)</td></tr>`:"");
}
</script>
</body></html>"""


FRESH_DAYS = 2  # data_asof 신선도 임계(D-n)


def _tier_facts(r: dict) -> list[str]:
    """추세·52주위치 사실 조각(무점수) — 표본부족 항목은 생략. 표·티저 공용(표현만 래퍼가 결정)."""
    parts = []
    if r.get("molit_trend_pct") is not None:
        d, p = r["molit_trend_dir"], r["molit_trend_pct"]
        parts.append(f'{d}{abs(p)}%(3/9개월)' if d in ("▲", "▼") else '보합(3/9개월)')
    if r.get("molit_pos_52w") is not None:
        parts.append(f'52주 {r["molit_pos_52w"]}%')
    return parts


def _tier_cell(r: dict) -> str:
    """정적 포스트 표의 '추세·52주' 셀(class=mut). 사실값만, 점수·전망 없음."""
    f = _tier_facts(r)
    return f'<span class=mut>{"<br>".join(f)}<sup>F</sup></span>' if f else '<span class=mut>—</span>'


def render_gu_post(gu: str, rows: list[dict], asof: str, today: str) -> dict:
    """구 1개 = 실명 사실 per-구 포스트(A모델 — 점수 없음, 공공 실거래·단지정보만). SEO 본체.
    반환: {html, jsonld, claims, llms_line, n, top_eok}."""
    stale = (date.fromisoformat(today) - date.fromisoformat(asof)).days > FRESH_DAYS
    badge = (f'<span class="badge stale">⚠ STALE · 데이터 {asof}</span>' if stale
             else f'<span class="badge">데이터 {asof} · 신선</span>')
    srt = sorted(rows, key=lambda r: (r["molit_recent_eok"] is None, -(r["molit_recent_eok"] or 0), r["name"]))
    priced = [r for r in rows if r["molit_recent_eok"] is not None]
    bluf = (f"{gu} {len(rows)}개 단지(세대수 200+ · 안전제외 반영)의 공공 실거래·단지정보 스냅샷. "
            f"국토부 RTMS 12개월 동일평형 중위 기준. 자체 평가·점수·순위 없음 — 공개된 사실 수치만.")
    trs = ""
    for r in srt:
        px = (f'{r["molit_recent_eok"]}억<sup>F</sup> <span class=mut>n{r["molit_n"]}</span>'
              if r["molit_recent_eok"] is not None else '<span class=mut>공공 실거래 없음</span>')
        if r.get("molit_p25_eok") is not None:                    # ① 분포 병기 — P25–중위–P75 협상 레인지
            px += (f'<br><span class=mut>{r["molit_p25_eok"]}–{r["molit_recent_eok"]}–{r["molit_p75_eok"]}'
                   f'<sup>F</sup> P25·중위·P75</span>')
        tier = _tier_cell(r)                                       # ②③ 추세·52주위치
        pp = f'{r["pyeong_price_man"]:,}만' if r["pyeong_price_man"] is not None else "—"
        trs += (f'<tr><td><b>{r["name"]}</b> <span class=mut>{r["saeng"]}</span></td>'
                f'<td>전용{r["area_m2"]}㎡<span class=mut>({r["pyeong"]}평)</span></td>'
                f'<td>{r["units_band"]}·{r["decade"]}</td><td>{r["product_type"]}</td>'
                f'<td>{px}</td><td>{tier}</td><td>{pp}</td></tr>')
    html = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>서울 {gu} 아파트 공공 실거래 + 단지정보 — {today}</title>
<meta name=description content="{gu} 아파트 단지별 국토부 공공 실거래가·세대수·연식·평형 스냅샷 ({today}). 사실 데이터만, 투자자문 아님.">
<script type="application/ld+json">{{JSONLD}}</script>
<style>body{{font:15px/1.6 -apple-system,Pretendard,sans-serif;max-width:880px;margin:0 auto;padding:24px;color:#1a1a1a}}
.badge{{display:inline-block;background:#e7f5e9;color:#1a7f37;border-radius:6px;padding:2px 9px;font-size:12px}}
.badge.stale{{background:#fff4e5;color:#b54708}}a{{color:#0969da}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}}th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left}}th{{background:#f6f8fa}}
.mut{{font-size:12px;color:#667}}sup{{color:#0969da;font-size:11px}}.disc{{font-size:12px;color:#667;border-top:1px solid #ddd;margin-top:24px;padding-top:12px}}</style>
</head><body>
<h1>서울 {gu} 아파트 공공 실거래 + 단지정보 <small>{today}</small></h1>
<p>{badge} · 라이선스 CC-BY-NC-4.0 · <a href="../explorer.html">전체 탐색기(내 기준 필터)</a></p>
<p class=disc style="border:0;margin:6px 0">⚖ {DISCLAIMER}</p>
<h2>한 줄 요약</h2><p>{bluf}</p>
<table><tr><th>단지명</th><th>전용</th><th>규모·연식</th><th>유형</th><th>공공 실거래(중위)</th><th>추세·52주</th><th>평단가</th></tr>{trs}</table>
<p class=mut><sup>F</sup>=국토부 실거래 사실 · n=표본수 · 평단가=실거래÷평형. 사설 시세(호가)는 게재하지 않습니다.<br>
P25·중위·P75=동일평형 실거래 분위수(협상 레인지). 추세=최근3개월 중위 vs 직전9개월 중위(과거 비교 사실 — 전망 아님). 52주=<b>최근 3개월 체결 중위</b>가 12개월(52주) 실거래 최저~최고 레인지 내 위치(%) — 헤드라인 중위(12개월)와 기준점 다름(최근 거래 없으면 —). 표본 부족 항목은 —.</p>
<div class=disc>
<b>방법론·출처</b><br>
• 실거래 = 국토교통부 RTMS 공공데이터(12개월 동일평형 중위), 매일 자동 재수집. 세대수·연식·전용면적·유형 = 공개정보.<br>
• 자체 평가·점수·순위를 매기지 않습니다. 사설 시세 원본은 미게재(공공 실거래만).<br>
• 세대수 200+ 단지 대상, 일부 단지 안전제외 반영. 데이터 {asof} 기준. {('<b>현재 STALE</b>.' if stale else '신선도 임계 내.')} 거래 전 원출처 재확인 필수.<br>
• {_takedown()}<br>
• <a href="../methodology.html">방법론 전문</a> · 코드: <a href="https://github.com/hexisteme/agent-realestate">github.com/hexisteme/agent-realestate</a>
</div></body></html>"""
    jsonld = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"서울 {gu} 아파트 공공 실거래·단지정보 {today}",
        "description": bluf, "dateModified": today, "datePublished": today,
        "license": "https://creativecommons.org/licenses/by-nc/4.0/",
        "creator": {"@type": "Organization", "name": "agent_realestate (개인 연구)"},
        "isBasedOn": [{"@type": "Dataset", "name": "국토교통부 아파트 실거래가(RTMS) 공공데이터", "url": "https://rt.molit.go.kr"}],
        "measurementTechnique": ("MOLIT RTMS 12-month same-area median, IQR (P25/P75), "
                                 "recent-3m vs prior-9m median change, position of recent-3m median within 52-week range — "
                                 "public data, no proprietary valuation, no scoring"),
        "variableMeasured": [
            {"@type": "PropertyValue", "name": "transaction_median_eok", "description": "12개월 동일평형 실거래 중위(억)"},
            {"@type": "PropertyValue", "name": "transaction_iqr_eok", "description": "동일평형 실거래 P25–P75 분위수(억) — 협상 레인지"},
            {"@type": "PropertyValue", "name": "recent3m_vs_prior9m_median_change_pct", "description": "최근3개월 중위 대비 직전9개월 중위 변화율(%) — 과거 비교 사실, 전망 아님"},
            {"@type": "PropertyValue", "name": "position_in_52w_range_pct", "description": "최근3개월 체결 중위의 12개월(52주) 실거래 최저~최고 레인지 내 위치(%) — 헤드라인 12개월 중위와 기준점 다름"}],
        "isAccessibleForFree": True, "keywords": ["부동산", "실거래", "공공데이터", "서울", gu]}
    html = html.replace("{JSONLD}", json.dumps(jsonld, ensure_ascii=False))
    claims = []
    for r in srt:
        if r["molit_recent_eok"] is not None:
            claims.append({"name": r["name"], "gu": gu, "claim": "recent_transaction_median_eok",
                           "value": r["molit_recent_eok"], "grade": "fact", "source": "MOLIT_RTMS_public",
                           "asof": asof, "n": r["molit_n"], "area_m2": r["area_m2"]})
        if r.get("molit_p25_eok") is not None:   # ① 분포 IQR(분위수 사실)
            claims.append({"name": r["name"], "gu": gu, "claim": "transaction_iqr_eok",
                           "p25": r["molit_p25_eok"], "p75": r["molit_p75_eok"], "grade": "fact",
                           "source": "MOLIT_RTMS_public", "asof": asof, "n": r["molit_n"], "area_m2": r["area_m2"]})
        if r.get("molit_trend_pct") is not None:  # ② 최근3개월 vs 직전9개월 중위 변화(과거 비교 사실)
            claims.append({"name": r["name"], "gu": gu, "claim": "recent3m_vs_prior9m_median_change_pct",
                           "value": r["molit_trend_pct"], "direction": r["molit_trend_dir"], "grade": "fact",
                           "source": "MOLIT_RTMS_public", "asof": asof, "n": r["molit_n"], "area_m2": r["area_m2"]})
        if r.get("molit_pos_52w") is not None:    # ③ 12개월 레인지 내 위치(사실)
            claims.append({"name": r["name"], "gu": gu, "claim": "position_in_52w_range_pct",
                           "value": r["molit_pos_52w"], "grade": "fact",
                           "source": "MOLIT_RTMS_public", "asof": asof, "n": r["molit_n"], "area_m2": r["area_m2"]})
        claims.append({"name": r["name"], "gu": gu, "claim": "units", "value": r["units"], "grade": "fact", "source": "public_record"})
        claims.append({"name": r["name"], "gu": gu, "claim": "built_year", "value": r["built_year"], "grade": "fact", "source": "public_record"})
    top_eok = max((r["molit_recent_eok"] for r in priced), default=None)
    llms_line = (f"- [{today} {gu}](/posts/{today}-{gu}.html): {gu} {len(rows)}단지 공공 실거래·단지정보(실명). "
                 f"사실 데이터만·점수 없음·CC-BY-NC. provenance 동봉(claims.jsonl).")
    return {"html": html, "jsonld": jsonld, "claims": claims, "llms_line": llms_line,
            "n": len(rows), "top_eok": top_eok, "stale": stale}


def write_posts(ds: dict, outdir: str) -> list[dict]:
    """dataset 의 complexes 를 구별로 묶어 실명 사실 per-구 포스트 + claims.jsonl + llms.txt 작성.
    반환: 구별 summary [{gu, n, top_eok, llms, stale, post}] (tistory/naver teaser 입력)."""
    from collections import defaultdict
    os.makedirs(f"{outdir}/posts", exist_ok=True)
    by = defaultdict(list)
    for r in ds["complexes"]:
        by[r["gu"]].append(r)
    asof, today = ds["data_asof"], ds["generated"]
    out, llms = [], []
    for gu in sorted(by):
        post = render_gu_post(gu, by[gu], asof, today)
        open(f"{outdir}/posts/{today}-{gu}.html", "w").write(post["html"])
        with open(f"{outdir}/posts/{today}-{gu}.claims.jsonl", "w") as f:
            for cl in post["claims"]:
                f.write(json.dumps(cl, ensure_ascii=False) + "\n")
        llms.append(post["llms_line"])
        out.append({"gu": gu, "n": post["n"], "top_eok": post["top_eok"],
                    "llms": post["llms_line"], "stale": post["stale"], "post": post})
    hdr = ("# 서울 아파트 공공 실거래 + 단지정보 (개인 연구)\n\n"
           "> 자치구별 국토부 공공 실거래가 + 세대수·연식·평형(실명). 자체 평가·점수 없음. 투자자문 아님. CC-BY-NC.\n\n## Posts\n")
    open(f"{outdir}/llms.txt", "w").write(hdr + "\n".join(llms) + "\n")
    return out


def main():
    import argparse
    from agent_realestate import config
    config.load_env_file()   # .env 의 RE_EMAIL_TO 주입(standalone 실행 시 — cmd_daily 경유시는 이미 주입됨)
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=date.today().isoformat())
    ap.add_argument("--today", default=date.today().isoformat())
    def _latest(p, fb): f = sorted(glob.glob(p)); return f[-1] if f else fb
    ap.add_argument("--universe", default=os.environ.get("RE_UNIVERSE") or
                    _latest("examples/candidates_universe[0-9][0-9][0-9]_*.json", "examples/candidates_universe159_20260606.json"))
    ap.add_argument("--molit", default=os.environ.get("RE_MOLIT") or
                    _latest("examples/molit_recent*.json", "examples/molit_recent_11gu_20260606.json"))
    ap.add_argument("--out", default="report/blog")   # 라이브 SRC(build_site 가 site/ 로 복사). 프리뷰는 --out report/blog/preview
    a = ap.parse_args()
    ds = build_dataset(a.universe, a.molit, a.asof, a.today)
    print(json.dumps(write_out(ds, a.out), ensure_ascii=False))


if __name__ == "__main__":
    main()
