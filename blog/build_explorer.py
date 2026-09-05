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
from urllib.parse import quote

from agent_realestate.collectors.naver_live import load_candidates

# 발행 구(run_daily.GU_LAWD 와 동일 — 단일소스화는 후속)
GU_LAWD = {"양천":"11470","강서":"11500","구로":"11530","동대문":"11230","마포":"11440",
           "성북":"11290","영등포":"11560","종로":"11110","동작":"11590","노원":"11350","도봉":"11320",
           # ★25구 확장(2026-07-10 WS-1) — 발행 레이어 구 커버리지. old 경로(universe 11구 한정)는
           #   frame/universe 에 없는 구가 no-op 이라 영향 없음(additive).
           "중구":"11140","용산":"11170","성동":"11200","광진":"11215","중랑":"11260","강북":"11305",
           "은평":"11380","서대문":"11410","금천":"11545","관악":"11620","서초":"11650","강남":"11680",
           "송파":"11710","강동":"11740"}

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
# ↑ 레거시 fuzzy 정규화 — collect_gongsi.py 가 여전히 import 하므로 존치(하위호환 전용).
#   이 모듈의 단지↔MOLIT 매칭에는 2026-09-05부터 쓰이지 않는다(아래 canonical_complex_name 로 교체 — P0 매칭결함 수정).

# 괄호 안 내용이 이 토큰들 "뿐"일 때만 비식별 qualifier 로 보고 제거한다(측정 근거:
# examples/molit_recent_25gu_20260710.json·frame_25gu_20260710.json 괄호토큰 빈도 상위 — 2026-09-05).
# 아래 4개(토지임대부아파트·치현마을·인왕산힐스테이트·데시앙)는 2026-09-05 근접매칭(item 3) 추가분 —
# 25구 MOLIT 원본명 전수(molit_recent_25gu_20260710.json) 기준 동일-lawd 충돌 0건 실측
# (tests/test_blog_matching.py::test_new_paren_qualifiers_introduce_no_molit_collision, gongsi-gate-fix.md 참고).
_PAREN_NON_IDENTITY = {"고층", "저층", "임대", "분양", "아파트", "주상복합", "도시형", "민간임대",
                       "토지임대부아파트", "치현마을", "인왕산힐스테이트", "데시앙"}

# 라틴↔한글 브랜드 표기 변형 — 서로 다른 실체가 아니라 같은 브랜드의 재표기임을 실측(빈도·근접매칭
# 스캔)으로 확인된 것만 접는다(2026-09-05 item 3). 부분일치 일반화 아님 — 정확히 이 토큰만 치환한다
# (경계조건 없음 — "DMCSKVIEW"·"DMCSK뷰아이파크포레"처럼 라틴 브랜드 접두가 붙어 있는 실측 사례가
# 있어 collect_gongsi._name_norm 의 기존 IPARK 치환과 동일하게 무경계 치환, 2026-09-05 실측).
_BRAND_VARIANTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"IPARK", re.I), "아이파크"),          # 노원IPARK(7) → 아이파크(101) 계열
    (re.compile(r"e편한세상", re.I), "이편한세상"),      # e편한세상(65) ↔ 이편한세상(8)
    (re.compile(r"SKVIEW", re.I), "SK뷰"),             # SKVIEW(8)·에스케이뷰(9) → SK뷰(15)
    (re.compile(r"에스케이뷰"), "SK뷰"),
    (re.compile(r"XI"), "자이"),                       # 자이(133) 계열(XI 표기 현재 0건, 방어적으로 유지)
]


def _fold_brand_variants(s: str) -> str:
    """라틴/한글 브랜드 표기 변형을 단일 키로 접는다(2026-09-05 근접매칭 수정) — _BRAND_VARIANTS 순서대로 치환."""
    for pat, repl in _BRAND_VARIANTS:
        s = pat.sub(repl, s)
    return s


def canonical_complex_name(nm: str) -> str | None:
    """단지명 → canonical 키(exact 매칭 전용 — 부분일치·prefix 매칭 폐지, 2026-09-05 P0 매칭결함 수정).
    ① [..] 대괄호 태그 제거. ② 문자열 전체가 괄호(들)뿐이고 바깥 텍스트가 없으면("(791-16)" 같은
    MOLIT 지번코드 placeholder) 그 자체가 식별자일 수 없으므로 통째로 제거한다(→ 대개 빈 문자열).
    ③ 그 외의 괄호는 안 내용이 비식별 allowlist(_PAREN_NON_IDENTITY) 토큰만으로 구성될 때만
    제거하고, 그 외 내용(동수·차수·브랜드명 등)은 식별자의 일부로 보존한다
    ("현대(982)" ≠ "현대(209)"). ④ 공백 전부 제거. ⑤ 말미 "아파트" 접미 제거.
    ⑥ 라틴↔한글 브랜드 표기 변형 접기(_fold_brand_variants, 2026-09-05 근접매칭 수정 — item 3).
    결과가 빈 문자열이면 매칭 불가로 None 반환 — 구멍(전부일치) 재발 방지를 위해 반드시
    '무엇과도 매칭되지 않아야' 한다(빈 이름·지번코드뿐인 이름 등)."""
    s = re.sub(r"\[.*?\]", "", nm)
    if re.sub(r"\([^()]*\)", "", s).strip() == "":
        return None   # 바깥 텍스트 없이 괄호(들)뿐 — 식별자 없음(지번코드 등)
    def _drop_non_identity(m: re.Match) -> str:
        toks = [t for t in re.split(r"[,\s]+", m.group(1).strip()) if t]
        return "" if toks and all(t in _PAREN_NON_IDENTITY for t in toks) else m.group(0)
    s = re.sub(r"\(([^()]*)\)", _drop_non_identity, s)
    s = re.sub(r"\s+", "", s)
    if s.endswith("아파트"):
        s = s[:-3]
    s = _fold_brand_variants(s)
    return s or None


def _collapse_numbered_block(canon: str) -> str:
    """번호블록 접미 collapse(2순위 매칭 전용) — 말미 '숫자+단지'/'숫자+차' 를 숫자만 남긴다
    ("상계주공1단지"→"상계주공1", "상계주공1차"→"상계주공1")."""
    return re.sub(r"(\d+)(단지|차)$", r"\1", canon)


def match_molit_names(disp: str, names_in_lawd) -> set[str]:
    """표시명 disp 를 같은 lawd(구) 안 원본 명칭 집합(names_in_lawd — MOLIT apt 명 또는 비교대상
    단지명)과 canonical 등가로 매칭한다 — 부분일치·4자 prefix 폐지(2026-09-05 P0 매칭결함 수정).
    1순위: canonical 완전일치. 2순위(1순위 0건일 때만): 양쪽에 _collapse_numbered_block 적용 후
    비교 — 접은 키가 그 lawd 안에서 서로 다른 canonical 원본명 정확히 1개로만 이어질 때만 채택한다
    (예: '주공1단지'·'주공1차' 가 둘 다 있으면 접어도 2개로 갈라져 무매칭 — 상계주공1~16단지가
    서로 뭉치던 결함의 재발방지). 반환은 매칭된 '원본' 문자열 집합 — disp 가 canonical 불가(None,
    예: 빈 이름)면 항상 빈 집합."""
    cd = canonical_complex_name(disp)
    if cd is None:
        return set()
    by_canon: dict[str, set[str]] = {}
    for raw in names_in_lawd:
        c = canonical_complex_name(raw)
        if c is not None:
            by_canon.setdefault(c, set()).add(raw)
    exact = by_canon.get(cd)
    if exact:
        return set(exact)
    cd_collapsed = _collapse_numbered_block(cd)
    collapsed_map: dict[str, set[str]] = {}
    for c in by_canon:
        collapsed_map.setdefault(_collapse_numbered_block(c), set()).add(c)
    group = collapsed_map.get(cd_collapsed)
    if group and len(group) == 1:
        return set(by_canon[next(iter(group))])
    return set()


def _match_records(c, lawd, molit) -> list[dict]:
    """동일평형(±3.5㎡)·이름매칭(canonical 완전일치 — 부분/prefix 매칭 없음, 2026-09-05 수정)된
    lawd 의 12개월 RTMS 레코드(price·ym 보존 — tier_now 파생용)."""
    recs = molit.get(lawd, [])
    ar = c.listing.area_exclusive_m2
    matched = match_molit_names(c.listing.complex_name, (r["apt"] for r in recs))
    return [r for r in recs if r.get("price") and abs(r["area"] - ar) <= 3.5 and r["apt"] in matched]


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


# ── public-only 발행 경로(WS-0, 2026-07-10) — 호가 Listing 없이 frame(공공 enumeration 데이터)+MOLIT
# 만으로 build_dataset 과 동일한 dataset 스키마를 만든다. build_dataset·_match_records 등은 무변경.


def _name_matched(disp: str, lawd: str, molit: dict) -> list[dict]:
    """이름매칭만(면적 무제한) — 발행 방법론(match_molit_names)과 동일한 canonical 완전일치
    규칙(부분일치·4자 prefix 폐지, 2026-09-05 수정). 앵커(대표 전용면적) 후보 도출 전용."""
    recs = molit.get(lawd, [])
    matched = match_molit_names(disp, (r["apt"] for r in recs))
    return [r for r in recs if r.get("price") and r["apt"] in matched]


def _match_records_public(disp: str, anchor_area: float, lawd: str, molit: dict) -> list[dict]:
    """발행 방법론(전용 ±3.5㎡ 동일평형·이름매칭)의 record 기반 변형 — _match_records 와 규칙 동일,
    Candidate(호가 Listing) 대신 표시명+면적앵커를 받는다. 이름매칭은 match_molit_names 의 canonical
    완전일치(부분일치·4자 prefix 폐지, 2026-09-05 수정).
    ★WS-0 v2(2026-07-10): v1(스캔용 밴드 55~66/78~95·band84 우선 매칭)은 게시된 방법론(±3.5㎡)과
    달라 기존 발행 단지의 공표 수치를 바꿔버림(동일성 게이트 FAIL 21.8%) → 방법론은 그대로 두고
    '앵커 소스'만 호가 리스팅→(universe 연속성 | 최다거래 평형)으로 교체."""
    recs = molit.get(lawd, [])
    matched = match_molit_names(disp, (r["apt"] for r in recs))
    return [r for r in recs if r.get("price") and abs(r["area"] - anchor_area) <= 3.5 and r["apt"] in matched]


def _anchor_area_mode(named_recs: list[dict]) -> float:
    """앵커 미보유(신규) 단지의 대표 전용면적 = 12개월 최다 거래 면적
    (0.5㎡ 클러스터 최빈, 동률이면 작은 면적 — 결정론·재현). '최다 거래 평형'은 공개 가능한 사실."""
    counts: dict[float, int] = {}
    for r in named_recs:
        key = round(r["area"] * 2) / 2
        counts[key] = counts.get(key, 0) + 1
    top = max(counts.values())
    return min(a for a, n in counts.items() if n == top)


def _build_anchor_resolver(anchor_universe: str | None):
    """기존 발행 연속성 앵커 리졸버 — universe 항목을 (표시명, 면적, 생활권)의 '게재 정체성'으로
    보존한다. 물리 조인 순서: ① complex_no 문자열 일치(단, universe 는 'A…' 별도 ID 체계가 섞여
    있어 frame 숫자 cno 와 자주 불일치 — 2026-07-10 실측) → ② 같은 구에서 canonical 완전일치(필요시
    번호블록 collapse, match_molit_names 와 동일 규칙 — 부분일치·4자 prefix 폐지, 2026-09-05 수정)
    후보 중 세대수 최근접. 반환: (uni_disp, area, saeng) | None.
    연속성 단지는 매칭·발행 모두 universe 표시명으로 수행해야 매칭 레코드 집합까지 기존 경로와
    동일해진다(frame 변형명으로 매칭하면 동일 앵커여도 표본이 달라짐 — 게이트 실측)."""
    if not anchor_universe:
        return lambda cno, disp, gu, units: None
    by_cno: dict = {}
    by_gu: dict = {}
    for d in json.load(open(anchor_universe, encoding="utf-8")):
        disp_u = re.sub(r"\[.*?\]", "", d.get("complex_name", "")).strip()
        gu_u = (d.get("district") or "").replace("서울", "").strip()
        if gu_u.endswith("구") and len(gu_u) > 2:
            gu_u = gu_u[:-1]       # "강서구"→"강서" (단, "중구" 처럼 2자는 유지)
        if not disp_u or not d.get("area_exclusive_m2"):
            continue
        entry = (disp_u, float(d["area_exclusive_m2"]), d.get("saenghwalgwon") or "",
                 int(d.get("units") or 0))
        if d.get("complex_no"):
            by_cno.setdefault(str(d["complex_no"]), entry)
        by_gu.setdefault(gu_u, []).append(entry)

    def resolve(cno: str, disp: str, gu: str, units: int):
        e = by_cno.get(str(cno))
        if e:
            return e[:3]
        pool = by_gu.get(gu, [])
        matched = match_molit_names(disp, (u[0] for u in pool))
        if not matched:
            return None
        cands = [u for u in pool if u[0] in matched]
        best = min(cands, key=lambda u: abs((units or 0) - u[3]))
        return best[:3]

    return resolve


def product_type_from_frame(ftype: str, name: str) -> str:
    """frame 의 type 필드(+명칭 보조판별)로 기존 product_type() 3분류(아파트/주상복합/도생)와 호환 매핑.
    frame API 는 현재 전량 'type=아파트'(2026-06-06 스냅샷)라 실제 신호는 명칭 괄호태그
    (예: '아네스트염창(민간임대,도시형)') — 기존 product_type() 과 동일 우선순위(도생→주상복합→아파트)."""
    t, nm = ftype or "", name or ""
    if "도시형" in t or "도시형" in nm:
        return "도생"
    if "주상복합" in t or "오피스텔" in t or "주상복합" in nm or "오피스텔" in nm:
        return "주상복합"
    return "아파트"


def build_dataset_public(frame_path: str, molit_path: str, asof: str, today: str,
                         survivors_path: str | None = None,
                         anchor_universe: str | None = None,
                         gu_allowlist: set[str] | None = None) -> dict:
    """public-only 발행 경로(WS-0 v3, 2026-07-10) — 두 파트 병합.

    (A) 연속성 파트: anchor_universe(기존 발행 유니버스 JSON)를 기존 경로 build_dataset 으로
        *그대로* 실행 — 기존 발행 단지의 게재 정체성(명칭·면적·enrichment·중위/tier)이 코드 경로
        수준에서 동일(동일성 게이트가 구조적으로 자명). 유니버스가 이미 보유한 호가 Listing 은 그
        단지들에 한해 계속 입력으로 쓰인다(발행물엔 여전히 미게재 — A모델).
    (B) 공공 유입 파트: frame(공공 enumeration: complexNo·households·builtYm·far·type)+MOLIT 만으로
        (A)에 없는 신규 단지 행 생성 — 호가 Listing 불필요(레거시 결합 해소는 정확히 이 파트).
        · 매칭 방법론은 게시된 그대로(전용 ±3.5㎡ 동일평형·이름매칭 — _match_records 와 동일 규칙,
          canonical 완전일치·부분/prefix 매칭 없음, 2026-09-05 수정).
          면적 앵커 = 12개월 최다 거래 평형(_anchor_area_mode, 공개 가능한 결정론 사실).
        · (A) 억제: 같은 구에서 (A) 행과 canonical 명칭이 일치(match_molit_names)하는 frame 단지는
          생성하지 않는다 — universe('A…')와 frame(숫자)의 ID 체계가 달라 cno 조인 불가(실측),
          명칭 억제가 유일한 중복 방어. 보수적 편향이 옳다(억제 과다=신규 1개 지연,
          부족=실명 중복 발행).
        · v1(스캔용 밴드 55~66/78~95)·v2(frame 변형명 fuzzy 재앵커)는 기존 게재 수치를 바꿔
          게이트 FAIL → 폐기 (경위는 git/AGENTS 기록).
    공통: 세대수≥200·구로 corridor 제외(build_dataset 동일). survivors_path 지정 시 (B)를 스캔 생존
    complexNo 로 제한(발행 풀 = 스캔 레이어 단일 진실; (A)는 기존 발행 유지라 미적용). 매칭 0(ghost)
    제외. (B)의 호가·enrichment 필드는 None(문자열 ""). A모델 가드(점수·순위·사설호가 없음) 동일.

    gu_allowlist(2026-07-10, 풀확대 3단계 구별 단계오픈): 지정 시 (B) 는 이 구 목록만 생성 —
    enrichment 백필 완료 전 구를 실수로 노출하지 않는 안전판. (A) 는 이미 검증된 발행분이라 미적용.
    None(기본)이면 frame 의 모든 구 생성(제한 없음 — 호출측이 --public-gu-allow 로 명시할 책임).
    """
    frame = json.load(open(frame_path, encoding="utf-8"))
    molit = json.load(open(molit_path))
    surv: set[str] | None = None
    if survivors_path:
        sdata = json.load(open(survivors_path, encoding="utf-8"))
        surv = {str(s.get("complex_no") or s.get("complexNo") or "") for s in sdata} - {""}

    # ── (A) 연속성 파트 — 기존 경로 그대로 ──
    base_rows: list[dict] = []
    excluded = {"under_min_units": 0, "corridor": 0, "no_molit_match": 0, "base_overlap": 0}
    suppress: dict[str, list[str]] = {}          # gu → [기존 발행명(원본)] — (B) 중복 방어(명칭)
    base_cnos: set[str] = set()                  # (B) 중복 방어(cno) — universe 숫자 cno 는 frame 과 겹침
    if anchor_universe:
        base = build_dataset(anchor_universe, molit_path, asof, today)
        base_rows = base["complexes"]
        for k, v in base["excluded"].items():
            excluded[k] = excluded.get(k, 0) + v
        for b in base_rows:
            suppress.setdefault(b["gu"], []).append(b["name"])
            if b.get("complex_no"):
                base_cnos.add(str(b["complex_no"]))

    def _suppressed(disp: str, gu: str) -> bool:
        """canonical 등가(match_molit_names)로 (A) 기존 발행명과 겹치는지 — 부분일치·4자 prefix
        폐지(2026-09-05 수정)."""
        return bool(match_molit_names(disp, suppress.get(gu, ())))

    # ── (B) 공공 유입 파트 ──
    by_cno: dict[str, list[dict]] = {}
    for r in frame:
        by_cno.setdefault(str(r["complexNo"]), []).append(r)

    rows = []
    for cno in sorted(by_cno):
        if surv is not None and cno not in surv:
            continue
        if cno in base_cnos:                       # (A) 와 같은 물리단지(cno 일치) — 중복 발행 방지
            excluded["base_overlap"] += 1
            continue
        candidates = []
        under_units_hit = corridor_hit = suppressed_hit = False
        for c in by_cno[cno]:
            gu = c.get("gu")
            if gu not in GU_LAWD:
                continue
            if gu_allowlist is not None and gu not in gu_allowlist:   # ⓪ 구별 단계오픈 안전판(2026-07-10)
                continue
            if (c.get("households") or 0) < MIN_UNITS:            # ① 세대수 하한 — build_dataset 과 동일
                under_units_hit = True
                continue
            disp = re.sub(r"\[.*?\]", "", c["name"]).strip()
            if gu == "구로" and disp in CORRIDOR_EXCLUDE:          # ② corridor hard 제외 — build_dataset 과 동일
                corridor_hit = True
                continue
            if _suppressed(disp, gu):                              # ③ (A) 기존 발행 단지와 명칭 중복 방어
                suppressed_hit = True
                continue
            candidates.append((gu, c, disp))
        if not candidates:
            if suppressed_hit:
                excluded["base_overlap"] += 1
            elif under_units_hit:
                excluded["under_min_units"] += 1
            elif corridor_hit:
                excluded["corridor"] += 1
            continue

        best = None   # 매칭표본 n 많은 쪽 우선, 동률이면 median 낮은 쪽(screen_11gu dedup 관례)
        for gu, c, disp in candidates:
            lawd = GU_LAWD[gu]
            named = _name_matched(disp, lawd, molit)
            if not named:
                continue
            anchor = _anchor_area_mode(named)                  # 신규 단지 결정론 앵커(최다 거래 평형)
            recs = _match_records_public(disp, anchor, lawd, molit)
            if not recs:
                continue
            md, n = _median_of(recs)
            rank = (n, md is not None, -(md or 0))
            if best is None or rank > best["rank"]:
                best = {"gu": gu, "c": c, "disp": disp, "recs": recs, "md": md, "n": n,
                        "anchor": anchor, "rank": rank}
        if best is None:
            excluded["no_molit_match"] += 1        # ghost — 실거래 매칭 0 → area_m2 도출 불가
            continue

        gu, c, disp, recs = best["gu"], best["c"], best["disp"], best["recs"]
        md, n = best["md"], best["n"]
        tier = derive_tier_now(recs, asof)                 # 동일표본 파생 무점수 사실 3종(분포·추세·52주위치)
        area_m2 = round(best["anchor"], 1)                 # 최다 거래 평형(실거래 사실 기반)
        pyeong = round(area_m2 / 3.305785, 1)
        ym4 = (c.get("builtYm") or "")[:4]
        built_year = int(ym4) if ym4.isdigit() else 0
        units = c.get("households") or 0
        far = c.get("far")
        rows.append({
            "name": disp,
            "gu": gu, "saeng": "",
            "area_m2": area_m2,
            "area_band": area_band(area_m2),
            "pyeong": pyeong,
            "units": units, "units_band": unit_band(units),
            "built_year": built_year, "decade": decade(built_year),
            "product_type": product_type_from_frame(c.get("type"), disp),
            "molit_recent_eok": md,
            "molit_n": n,
            "molit_p25_eok": tier["p25_eok"],
            "molit_p75_eok": tier["p75_eok"],
            "molit_trend_dir": tier["trend_dir"],
            "molit_trend_pct": tier["trend_pct"],
            "molit_pos_52w": tier["pos_52w"],
            "pyeong_price_man": round(md * 1e8 / pyeong / 1e4) if (md and pyeong) else None,
            # ★ 점수·순위·등급·강점축·세그먼트 일절 없음(A 모델) — 사실 수치만.
            # ── public 경로(B)는 호가/enrichment 배선이 없어 아래 전부 None(문자열 필드는 "") ──
            "slope_pct": None, "far_pct": round(far, 1) if far else None, "bcr_pct": None,
            "review_score": None, "academy_exam": None,
            "subway_m": None, "cbd_km": None, "cbd_name": "",
            "complex_no": str(c.get("complexNo") or ""),
            "facing": "",
            "land_share_pyeong": None, "floor": None,
            "tukmokgo_pct": None, "school_achievement": None,
            "gu_jeonse_ratio_pct": None, "trade_annual": None, "transit": "",
            "mart_800": None, "hosp_800": None, "park_1k": None, "dept_1500": None,
            "heating": "", "corridor_type": "", "parking_per_unit": None, "builder": "",
            "nearest_elem_school": None, "gongsi_man": None, "maint_fee_won": None,
        })
    # (B) 내부 수렴 중복 제거 — frame 이 고층/저층·도시형/주상복합 등 분할 cno 로 열거한 동일
    # canonical 단지는 1행만(표본 n 최대, 동률이면 complex_no 사전순 — 결정론). canonical 판정은
    # match_molit_names 와 동일 규칙(2026-09-05 수정 — 이전엔 원본 name 그대로가 키라 "(도시형)"/
    # "(주상복합)" 태그 variant 가 별개 행으로 새 나가 동일시그니처 게이트에 걸렸다: 세운푸르지오
    # 헤리시티·힐스테이트세운센트럴1/2단지 실측).
    ordered = sorted(rows, key=lambda r: (-(r["molit_n"] or 0), str(r["complex_no"])))
    uniq: dict[tuple, dict] = {}
    for r in ordered:
        key = (canonical_complex_name(r["name"]) or r["name"], r["gu"], r["area_m2"])
        uniq.setdefault(key, r)
    rows = base_rows + list(uniq.values())
    rows.sort(key=lambda x: (x["gu"], x["name"], x["area_m2"]))
    return {
        "schema_version": "explorer-facts/1", "generated": today, "data_asof": asof,
        "license": "CC-BY-NC-4.0", "disclaimer": DISCLAIMER, "takedown": _takedown(), "sources": SOURCES,
        "count": len(rows), "complexes": rows, "excluded": excluded,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 가격 세그먼트 + 유동성(C)·전세 갭(D) 파생 사실 (2026-07-10, 풀확대 2단계)
# 전부 post-process(ds["complexes"] 를 직접 채움) — build_dataset/build_dataset_public 양쪽에
# 동일 함수를 적용해 경로에 무관하게 같은 사실을 얹는다(run_daily.py 단일 호출점).
# ═══════════════════════════════════════════════════════════════════════════

# 가격대는 정책 대출한도(15억 이하 6억/초과 4억, 10.15 대책)와 무관하게 매매 중위의 단순
# 사실 구간화다 — "이 가격대는 이 대출을 받을 수 있다"를 암시하지 않는다(개별 소득·상품 요건은
# build_finance_plan_actual/§4 정밀모드가 담당, FINANCE_CONFIRM_NOTICE 로 은행 확인 의무 고지).
PRICE_SEGMENTS = [(6.0, "6억 이하"), (10.0, "6~10억"), (15.0, "10~15억"), (float("inf"), "15억 초과")]


def price_segment(molit_recent_eok: float | None) -> str | None:
    if molit_recent_eok is None:
        return None
    for hi, label in PRICE_SEGMENTS:
        if molit_recent_eok <= hi:
            return label
    return PRICE_SEGMENTS[-1][1]


def add_price_segment(ds: dict) -> dict:
    """가격 세그먼트(F, 사실 구간화) — 매매 중위 기준. 추천·적격 판정 아님(disclaimer 동봉 의무)."""
    for r in ds["complexes"]:
        r["price_segment"] = price_segment(r.get("molit_recent_eok"))
    return ds


def _trade_annual_public(name: str, gu: str, molit: dict) -> float | None:
    """단지 12개월 실거래 건수(전 평형, canonical 완전일치) — match_molit_names 와 동일 규칙
    (부분일치·4자 prefix 폐지, 2026-09-05 수정. 이전 _norm_exact 정규화 대체 — 밴드/면적 무관은
    유지: 거래회전율은 '그 단지 전체'가 분모라 면적 제한 금지)."""
    lawd = GU_LAWD.get(gu)
    if not lawd:
        return None
    recs = molit.get(lawd, [])
    matched = match_molit_names(name, (r.get("apt", "") for r in recs))
    cnt = sum(1 for r in recs if r.get("apt", "") in matched)
    return float(cnt) if cnt else None


def add_liquidity_facts(ds: dict, molit_path: str) -> dict:
    """거래회전율(C, 2026-07-10) = 연 거래건수(전 평형)/세대수×100. molit_path 는 그 dataset 빌드에
    실제 사용한 MOLIT 파일과 동일해야 한다(호출측 책임 — run_daily.py 가 자신의 a.molit 을 그대로 전달).
    '회전율 낮음=환금성 나쁨' 으로 읽히지 않도록 UI 방어문구 동반(foot 참조)."""
    molit = json.load(open(molit_path))
    for r in ds["complexes"]:
        ta = _trade_annual_public(r["name"], r["gu"], molit)
        r["trade_annual"] = ta
        r["turnover_pct"] = round(ta / r["units"] * 100, 1) if (ta and r.get("units")) else None
    return ds


def add_jeonse_facts(ds: dict, jeonse_path: str) -> dict:
    """전세가율·갭(D, 2026-07-10) — 매매·전세 모두 국토부 RTMS 공공 실거래 기반 사실.
    jeonse_path 스키마는 매매(molit_recent_*)와 동일({lawd: [{"apt","area","price"(보증금),"ym"}]}) —
    _match_records_public/_median_of 를 그대로 재사용(동일평형 ±3.5㎡·이름매칭, 게시 방법론과 동일).
    jeonse_path 미존재 시 조용히 스킵(gongsi_man 류와 동일한 '승인/수집 대기' 패턴 — 후속 배치가 채움)."""
    if not jeonse_path or not os.path.exists(jeonse_path):
        return ds
    jeonse = json.load(open(jeonse_path))
    for r in ds["complexes"]:
        lawd = GU_LAWD.get(r["gu"])
        jm = jn = None
        if lawd and r.get("area_m2"):
            recs = _match_records_public(r["name"], r["area_m2"], lawd, jeonse)
            jm, jn = _median_of(recs)
        r["jeonse_recent_eok"] = jm
        r["jeonse_n"] = jn
        if jm is not None and r.get("molit_recent_eok") is not None:
            r["gap_eok"] = round(r["molit_recent_eok"] - jm, 2)
            r["jeonse_ratio_complex_pct"] = round(jm / r["molit_recent_eok"] * 100, 1)
        else:
            r["gap_eok"] = None
            r["jeonse_ratio_complex_pct"] = None
    return ds


def slugify_complex_name(name: str) -> str:
    """단지명 → 다이제스트/구허브 공용 앵커 slug(공백 제거 + percent-encode, 2026-09-05 P1).
    허브 표 행 id 와 다이제스트 링크 fragment 가 동일 규칙을 써야 앵커가 어긋나지 않는다."""
    return quote(re.sub(r"\s+", "", name))


def passes_rank_gate(r: dict) -> bool:
    """랭킹형 목록(다이제스트 12개월 상단/하단·전세가율·회전율, 구허브 집계) 공통 게이트(2026-09-05 P1) —
    아파트·전용 40㎡ 이상·매매표본 10건 이상만 순위/집계에 포함(A모델 공표정책). 통과 못하면 표에서 —."""
    return (r.get("product_type") == "아파트"
            and (r.get("area_m2") or 0) >= 40
            and (r.get("molit_n") or 0) >= 10)


def passes_jeonse_gate(r: dict) -> bool:
    """전세가율 목록 추가 게이트 — 기본 게이트 + 전세표본 5건 이상 + 전세가율 95% 이하."""
    return (passes_rank_gate(r) and (r.get("jeonse_n") or 0) >= 5
            and r.get("jeonse_ratio_complex_pct") is not None
            and r["jeonse_ratio_complex_pct"] <= 95)


def passes_turnover_gate(r: dict) -> bool:
    """회전율 목록 추가 게이트 — 기본 게이트 + 회전율 값 존재."""
    return passes_rank_gate(r) and r.get("turnover_pct") is not None


def select_gated_medians(rows: list[dict]) -> list[float]:
    """구 중위(중위의 중위)·P25-P75 등 구 단위 집계의 공용 입력 — passes_rank_gate 통과 단지의
    molit_recent_eok 오름차순 정렬 리스트(억). _pctile 은 정렬 입력을 가정하므로(2026-09-05 발견:
    구허브 P25>P75 역전 버그 — 미정렬 리스트를 그대로 넘겨 발생) 여기서 정렬해 반환.
    다이제스트 구별 요약과 구허브 요약타일이 동일 값을 쓰도록 단일화."""
    return sorted(r["molit_recent_eok"] for r in rows
                  if passes_rank_gate(r) and r.get("molit_recent_eok") is not None)


def compute_gu_median(rows: list[dict]) -> float | None:
    """구 중위(중위의 중위, 억) — select_gated_medians 표본 없으면 None(— 처리)."""
    vals = select_gated_medians(rows)
    return round(st.median(vals), 2) if vals else None


def compute_gu_jeonse_ratio_median(rows: list[dict]) -> float | None:
    """구 전세가율 중위(%) — passes_jeonse_gate 통과 단지만. 표본 없으면 None."""
    vals = [r["jeonse_ratio_complex_pct"] for r in rows if passes_jeonse_gate(r)]
    return round(st.median(vals), 1) if vals else None


def add_enrich_overlay(ds: dict, overlay_path: str) -> dict:
    """public 경로 신규단지 enrichment overlay(K-apt·공시가·관리비·카카오, 2026-07-10 collect_public_enrich.py)
    병합 — complex_no 매칭 행만, 기존 값(None/빈문자열)일 때만 채움(기존 발행 단지 값은 건드리지 않음).
    overlay_path 미존재 시 조용히 스킵(수집 배치 완료 후 자동 반영 — gongsi_man 류와 동일 패턴).
    kapt_code/gu_ipsi_academy 는 overlay 에 있어도 dataset 스키마 필드가 아니라 병합 대상에서 제외."""
    if not overlay_path or not os.path.exists(overlay_path):
        return ds
    overlay = json.load(open(overlay_path, encoding="utf-8"))
    fields = ("heating", "corridor_type", "parking_per_unit", "builder",
              "nearest_elem_school", "academy_exam", "gongsi_man", "maint_fee_won")
    for r in ds["complexes"]:
        ov = overlay.get(str(r.get("complex_no") or ""))
        if not ov:
            continue
        for f in fields:
            if r.get(f) in (None, "") and ov.get(f) is not None:
                r[f] = ov[f]
    return ds


def assert_no_duplicate_signatures(ds: dict) -> None:
    """동일시그니처(매칭결함 재발) 게이트 — (molit_recent_eok, molit_n, molit_p25_eok, molit_p75_eok,
    molit_trend_pct, molit_pos_52w) 가 완전히 같은 단지가 2개 이상(molit_n>=5 한정 — 소표본 우연
    일치는 실제 매칭결함이 아닐 수 있어 제외) 있으면 이름매칭이 다시 뭉쳤다는 신호로 보고
    ValueError(그룹 목록 포함)를 낸다. run_daily.py 가 write_out 직전에 호출해 회귀 시 발행을
    막는다(2026-09-05, 188/690 동일시그니처 사고 재발방지 — [[feedback-realestate-regen-pipeline]])."""
    groups: dict[tuple, list[str]] = {}
    for r in ds["complexes"]:
        if (r.get("molit_n") or 0) < 5:
            continue
        sig = (r.get("molit_recent_eok"), r.get("molit_n"), r.get("molit_p25_eok"),
               r.get("molit_p75_eok"), r.get("molit_trend_pct"), r.get("molit_pos_52w"))
        groups.setdefault(sig, []).append(f'{r.get("gu")}/{r.get("name")}')
    dups = {sig: names for sig, names in groups.items() if len(names) > 1}
    if dups:
        lines = [f"  {sig} -> {names}" for sig, names in dups.items()]
        raise ValueError(f"동일 시그니처(매칭결함 의심) 단지 그룹 {len(dups)}개:\n" + "\n".join(lines))


def write_out(ds: dict, outdir: str) -> dict:
    os.makedirs(outdir, exist_ok=True)
    json.dump(ds, open(f"{outdir}/dataset.json", "w"), ensure_ascii=False, separators=(",", ":"))
    from blog.build_site import ga4_snippet   # lazy: build_site 는 본 모듈을 import 하지 않지만 순환 예방적으로 지연
    open(f"{outdir}/explorer.html", "w").write(EXPLORER_HTML.replace("</head>", ga4_snippet() + "</head>", 1))
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
    for k in ("trade_annual", "jeonse_recent_eok"):   # C·D(2026-07-10) — 신규 공공유입 단지 백필 진행중
        nn = _cov(k)
        if nn / n < 0.5:
            print(f"ℹ️ [coverage-pending] {k}: {nn}/{n} — public 유입 단지 백필/전세수집 진행중")
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
        <button class=toggle-btn id=copyLinkBtn onclick="copyStateLink(this)">링크 복사</button>
      </div>
    </div>
    <div style="overflow:auto"><table id=tbl><thead id=thead></thead><tbody id=tbody></tbody></table></div>
    <div class=foot id=foot></div>
  </main>
</div>
<script>
const S={q:"",gu:new Set(),area:new Set(),decade:new Set(),ptype:new Set(),seg:new Set(),emin:null,emax:null,units_min:null,ppmin:null,ppmax:null,sort:"molit_recent_eok",dir:-1};
const SEG_ORDER=["6억 이하","6~10억","10~15억","15억 초과"];
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
  {k:"price_segment",t:"가격대",num:false,
   fmt:r=>r.price_segment?`<span class=tag>${esc(r.price_segment)}</span>`:`<span class=muted>—</span>`},
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
  {k:"gu_jeonse_ratio_pct",t:"전세가율(서울전체)",num:true,extra:true,
   fmt:r=>r.gu_jeonse_ratio_pct!=null?`${r.gu_jeonse_ratio_pct}%<span class=muted> 서울전체 R-ONE</span>`:`<span class=muted>—</span>`},
  {k:"jeonse_ratio_complex_pct",t:"전세가율(단지)",num:true,extra:true,
   fmt:r=>r.jeonse_ratio_complex_pct!=null?`${r.jeonse_ratio_complex_pct}%<sup class=sup> F</sup>`:`<span class=muted>—</span>`},
  {k:"gap_eok",t:"매매-전세 갭",num:true,extra:true,
   fmt:r=>r.gap_eok!=null?`${r.gap_eok}억<sup class=sup> F</sup><br><span class=muted>전세${r.jeonse_recent_eok}억 n${r.jeonse_n}</span>`:`<span class=muted>—</span>`},
  {k:"trade_annual",t:"연거래수",num:true,extra:true,
   fmt:r=>r.trade_annual!=null?`${r.trade_annual}건/년`:`<span class=muted>—</span>`},
  {k:"turnover_pct",t:"거래회전율",num:true,extra:true,
   fmt:r=>r.turnover_pct!=null?`${r.turnover_pct}%<sup class=sup> F</sup>`:`<span class=muted>—</span>`},
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

// 프리셋 딥링크(2026-09-05 P2) — ?gu=&band=&sort=&dir=&q= 를 첫 render() 전에 S 에 반영.
// 허브/단지페이지/다이제스트가 이 쿼리로 explorer.html 을 가리키면 그 필터가 이미 걸린 채로 뜬다.
function applyPreset(){
  const p=new URLSearchParams(location.search);
  const gu=p.get("gu"); if(gu) S.gu.add(gu);
  const band=p.get("band"); if(band) S.area.add(band);
  const q=p.get("q"); if(q) S.q=q;
  const sort=p.get("sort"); if(sort&&COLS.some(c=>c.k===sort)) S.sort=sort;
  const dir=p.get("dir"); if(dir==="1"||dir==="-1") S.dir=+dir;
}
// 현재 상태(S)를 쿼리스트링으로 클립보드에 복사 — "이 필터 그대로" 공유용.
function copyStateLink(btn){
  const p=new URLSearchParams();
  if(S.gu.size) p.set("gu",[...S.gu][0]);
  if(S.area.size) p.set("band",[...S.area][0]);
  if(S.q) p.set("q",S.q);
  p.set("sort",S.sort); p.set("dir",S.dir);
  const url=location.origin+location.pathname+"?"+p.toString();
  const done=()=>{if(btn){const t=btn.textContent;btn.textContent="복사됨";setTimeout(()=>btn.textContent=t,1200);}};
  if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done).catch(()=>{});
}

fetch("./dataset.json").then(r=>r.json()).then(d=>{DB=d;applyPreset();init();render();});

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
    +mk("가격대<span class=muted style=font-weight:400> (실거래 중위 구간·사실)</span>","seg",
        SEG_ORDER.filter(v=>cx.some(x=>x.price_segment===v)))
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
  // 프리셋(applyPreset)으로 이미 채워진 S 를 필터 UI에도 반영(체크표시·검색창 값) — 없으면 렌더는 맞는데 칩만 꺼져 보임.
  document.getElementById("q").value=S.q;
  F.querySelectorAll(".chip").forEach(c=>{
    const k=c.dataset.k,v=c.dataset.v;
    if(k==="units_min"){ if(+v===S.units_min) c.classList.add("on"); }
    else if(S[k]&&S[k].has&&S[k].has(v)) c.classList.add("on");
  });
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
    +`<br>• <b>가격대</b> = 매매 중위 기준 단순 구간화(사실)이며 대출 적격·추천을 의미하지 않습니다 — 대출 가능 여부·금리·한도는 소득 등 개별조건에 따라 다르므로 은행 등 금융기관에 직접 확인하십시오.`
    +`<br>• <b>전세가율(단지)</b>·<b>매매-전세 갭</b> = 동일평형 전세 실거래(공공 RTMS) 매칭 사실. <b>전세가율(서울전체)</b>은 개별 단지가 아닌 한국부동산원 R-ONE 서울 전체 월간 평균(참고용 거시지표)입니다.`
    +`<br>• <b>거래회전율</b> = 그 단지 12개월 전체 실거래 건수÷세대수×100(%) — 거주만족·매물희소 등 다양한 이유로 낮을 수 있어 '환금성 나쁨'의 단정적 지표가 아닙니다.`
    +`<br>• ${esc(DB.takedown)}`;
}
function passFilter(x){
  if(S.q && !x.name.toLowerCase().includes(S.q.toLowerCase())) return false;
  if(S.gu.size&&!S.gu.has(x.gu)) return false;
  if(S.area.size&&!S.area.has(x.area_band)) return false;
  if(S.decade.size&&!S.decade.has(x.decade)) return false;
  if(S.ptype.size&&!S.ptype.has(x.product_type)) return false;
  if(S.seg.size&&!S.seg.has(x.price_segment)) return false;
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
    from blog.build_site import ga4_snippet   # lazy import — build_site 가 gu_hub 경유로 본 모듈을 참조할 수 있어 순환 예방
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
        if r.get("price_segment"):                                # 가격대(사실 구간화, 2026-07-10)
            px += f'<br><span class=mut>가격대 {r["price_segment"]}</span>'
        if r.get("gap_eok") is not None:                           # 매매-전세 갭(D, 2026-07-10)
            px += f'<br><span class=mut>전세{r["jeonse_recent_eok"]}억<sup>F</sup>·갭{r["gap_eok"]}억</span>'
        tier = _tier_cell(r)                                       # ②③ 추세·52주위치
        if r.get("turnover_pct") is not None:                      # 거래회전율(C, 2026-07-10)
            tier += f'<br><span class=mut>회전율{r["turnover_pct"]}%<sup>F</sup></span>'
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
{ga4_snippet()}
</head><body>
<h1>서울 {gu} 아파트 공공 실거래 + 단지정보 <small>{today}</small></h1>
<p>{badge} · 라이선스 CC-BY-NC-4.0 · <a href="../explorer.html">전체 탐색기(내 기준 필터)</a></p>
<p class=disc style="border:0;margin:6px 0">⚖ {DISCLAIMER}</p>
<h2>한 줄 요약</h2><p>{bluf}</p>
<table><tr><th>단지명</th><th>전용</th><th>규모·연식</th><th>유형</th><th>공공 실거래(중위)</th><th>추세·52주</th><th>평단가</th></tr>{trs}</table>
<p class=mut><sup>F</sup>=국토부 실거래 사실 · n=표본수 · 평단가=실거래÷평형. 사설 시세(호가)는 게재하지 않습니다.<br>
P25·중위·P75=동일평형 실거래 분위수(협상 레인지). 추세=최근3개월 중위 vs 직전9개월 중위(과거 비교 사실 — 전망 아님). 52주=<b>최근 3개월 체결 중위</b>가 12개월(52주) 실거래 최저~최고 레인지 내 위치(%) — 헤드라인 중위(12개월)와 기준점 다름(최근 거래 없으면 —). 표본 부족 항목은 —.<br>
가격대=매매 중위 기준 단순 구간화(사실)이며 대출 적격·추천이 아닙니다 — 소득 등 개별조건에 따라 다르므로 은행 등 금융기관에 직접 확인하십시오. 갭=매매-전세 중위 차액(동일평형 전세 실거래 매칭). 회전율=12개월 전체 실거래 건수÷세대수×100(%) — 환금성 단정 아님.</p>
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
        if r.get("gap_eok") is not None:           # D(2026-07-10) 매매-전세 갭 — 양쪽 다 RTMS 실거래
            claims.append({"name": r["name"], "gu": gu, "claim": "sale_jeonse_gap_eok",
                           "value": r["gap_eok"], "jeonse_median_eok": r["jeonse_recent_eok"], "grade": "fact",
                           "source": "MOLIT_RTMS_public", "asof": asof, "n": r["jeonse_n"], "area_m2": r["area_m2"]})
        if r.get("trade_annual") is not None:      # C(2026-07-10) 연 거래건수(전 평형)
            claims.append({"name": r["name"], "gu": gu, "claim": "annual_trade_count",
                           "value": r["trade_annual"], "grade": "fact",
                           "source": "MOLIT_RTMS_public", "asof": asof})
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
