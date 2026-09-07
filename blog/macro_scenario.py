"""거시 전달 계산(L2, ScenarioTable) — 결정론 산수만(2026-09-07 S3, 브리핑 §3.1 L2).
대출금리 변화(bp) → 월 상환 Δ · 스트레스 DSR 한도 Δ · 고정/변동 손익분기 · 공시가(배율) → 보유세.
전망이 아니다: 기준금리 결정이 대출금리로 얼마나 전달되는지는 여기서 가정하지 않는다(그건 L3 과거 통계의 몫) —
"대출금리가 이만큼 움직이면 이 산수" 를 조건마다 행으로 둔다. 세금·DSR 산식은 agent_realestate.analysts.finance 와
같은 정책 파라미터(PolicyParams)를 쓴다. 기본 입력값(금리·대출금·소득)은 [가정] 이며 페이지에서 바꿀 수 있다.
"""
from __future__ import annotations

import math

from agent_realestate.analysts import finance as fin

STEPS_BP = (-50, -25, 0, 25, 50)
DEFAULT_LOAN_KRW = 300_000_000
DEFAULT_YEARS = 30
DEFAULT_RATE_PCT = 4.50            # [가정] 입력 기본값 — 변동 주담대 대표값이 아님(은행별 4.38~6.46, 2026-09-07 은행연합회 비교 기준·브리핑 §1)
DEFAULT_FIXED_PCT = 4.74           # [가정] 입력 기본값(2026-09-07 고정형 하단·브리핑 §1)
DEFAULT_INCOME_KRW = 100_000_000
DEFAULT_PRICE_KRW = 1_200_000_000  # [가정] 절대상한 판정용 매매가 입력 기본값
DSR_LIMIT = 0.40
DEFAULT_STRESS_PP = 3.0            # [정책] 수도권·규제지역 스트레스 DSR 가산 3.0%p(10.15 대책, 2025-10-16 시행 — 메모리 project-2026-loan-policy, korea.kr). PolicyParams 기본 1.5%(대략)는 비규제·구값
TERM_CAP_YEARS = 30                # [정책] 주담대 만기 상한 30년(6.27 대책) — DSR 산정·실제 만기 모두
RATE_EPS = 1e-9                    # 월리 r 이 이 값 미만이면 0금리 산식 — JS(_CALC_JS pmt/pv)도 같은 문턱(월 상환·DSR 경로 모두, S6 Codex F9)
MAX_INPUT_KRW = 10**13             # 대출금·연소득·절대상한 입력 상한(1e13원) — JS 가드·input max 동일, 연산 중 ∞ 방지(S6 Codex F8)
HOLDING_PRICES_EOK = (10, 15, 20, 30)
HOLDING_MULTIPLES = (1.49, 1.65)     # (20억 이상 밴드 중위, 발행 풀 중위) — 실측, 게이트 스크립트로 갱신
HOLDING_MULTIPLES_ASOF = "2026-09-07"   # 2026-09-07 실측: 발행 풀 중위 1.65 · 20억+ 밴드 1.49(브리핑 §2) — S4 에서 단지별 값으로 대체


def compute_monthly_payment(principal_krw: int, annual_rate_pct: float, years: int) -> int:
    """원리금균등 월 상환액 = P·r/(1−(1+r)^−n), r = 연리/12, n = 개월."""
    r, n = annual_rate_pct / 100 / 12, years * 12
    if r < RATE_EPS:                                 # 미소 금리는 0금리 산식(분모 소실 방지, S5 Codex)
        return int(principal_krw / n)
    return int(principal_krw * r / (1 - (1 + r) ** (-n)))


def build_scenario_table(loan_krw: int, years: int, rate_pct: float, steps_bp: tuple = STEPS_BP) -> list[dict]:
    """대출금리 변화(bp)별 월 상환·Δ(월·연). 0bp 행이 기준."""
    years = min(years, TERM_CAP_YEARS)                      # 6.27 대책 만기 상한 — 표·JS 모두 같은 상한(S3 Codex P1)
    base = compute_monthly_payment(loan_krw, rate_pct, years)
    rows = []
    for bp in steps_bp:
        rate = rate_pct + bp / 100
        if rate < 0:                                        # 음수 금리 행은 싣지 않는다(표시 금리와 계산 금리 불일치 방지, S6 Codex F10) — JS 동일
            continue
        m = compute_monthly_payment(loan_krw, rate, years)
        rows.append({"step_bp": bp, "rate_pct": round(rate, 2), "monthly_krw": m,
                     "delta_monthly_krw": m - base, "delta_annual_krw": (m - base) * 12})
    return rows


def build_dsr_table(annual_income_krw: int, rate_pct: float, years: int, stress_pp: float,
                    steps_bp: tuple = STEPS_BP, dsr_limit: float = DSR_LIMIT, cap_krw: int | None = None) -> list[dict]:
    """스트레스 DSR 한도 — 한도 산정 금리 = 대출금리 + 스트레스 가산(정책값, %p). cap_krw(절대상한)가 있으면 그 이하."""
    years = min(years, TERM_CAP_YEARS)                      # 6.27 대책 만기 상한(S3 Codex P1)

    def limit(rate: float) -> int:
        r = rate + stress_pp
        v = (fin.compute_dsr_loan(annual_income_krw, r / 100, years, dsr_limit) if r / 100 / 12 >= RATE_EPS
             else int(annual_income_krw * dsr_limit / 12 * years * 12))        # 미소·0금리 = 월 상환액 × 개월(JS pv 와 같은 문턱)
        return min(v, cap_krw) if cap_krw else v
    base = limit(rate_pct)
    rows = []
    for bp in steps_bp:
        rate = rate_pct + bp / 100
        if rate < 0:                                        # 음수 금리 행 제외(S6 Codex F10) — JS 동일
            continue
        lim = limit(rate)
        rows.append({"step_bp": bp, "rate_pct": round(rate, 2), "stress_rate_pct": round(rate + stress_pp, 2),
                     "limit_krw": lim, "delta_krw": lim - base, "capped": bool(cap_krw) and lim >= cap_krw})
    return rows


def compute_breakeven(fixed_pct: float, variable_pct: float) -> dict:
    """고정−변동 격차(%p)와 그 격차를 25bp 단위로 센 횟수(격차 ≤ 0 이면 0). 산수일 뿐 어느 쪽이 유리하다는 판단이 아니다.
    백분율은 소수 둘째 자리 격자만 받는다 — JS 가드 grid() 와 같은 격자. 그 밖(4.751·7.005·4.625)은 파이썬 round 와 JS Math.round 가
    갈리므로 양쪽 모두 거부한다(S6c Codex R3)."""
    for v in (fixed_pct, variable_pct):
        if round(v, 2) != v:
            raise ValueError(f"백분율은 소수 둘째 자리까지: {v}")
    gap = round(fixed_pct - variable_pct, 2)
    return {"gap_pp": gap, "steps_25bp": math.ceil(gap / 0.25 - 1e-9) if gap > 0 else 0}


def compute_holding_tax_from_official(official_krw: int, num_homes: int = 1, params=None) -> dict:
    """공시가 → 연 재산세(1주택 특례: 공정시장가액비율 시행령 109조·특례세율 111조의2) + 종부세(1주택 12억 공제).
    finance.compute_property_tax / compute_comprehensive_tax 와 같은 파라미터·산식, 입력만 공시가 직접."""
    P = params or fin._P
    if num_homes == 1:
        fmr = next(r for hi, r in P.fair_market_1home_tiers if official_krw <= hi)
        brackets = P.property_special_brackets if official_krw <= 900_000_000 else P.property_tax_brackets
    else:
        fmr, brackets = P.fair_market_ratio, P.property_tax_brackets
    base = official_krw * fmr
    prop = fin._marginal_tax(base, brackets) + base * P.property_urban_rate
    ded = P.jongbu_deduction_1home if num_homes == 1 else P.jongbu_deduction_multi
    jb_base = max(0.0, official_krw - ded) * P.fair_market_ratio
    jb = fin._marginal_tax(jb_base, P.jongbu_brackets) if jb_base > 0 else 0.0
    return {"official_krw": int(official_krw), "property_tax_krw": int(prop), "jongbu_tax_krw": int(jb), "total_krw": int(prop + jb)}


def build_holding_tax_table(prices_eok: tuple = HOLDING_PRICES_EOK, multiples: tuple = HOLDING_MULTIPLES) -> list[dict]:
    """실거래 중위(억) × 공시가 배율 → 공시가 = 중위 ÷ 배율 → 1주택 연 보유세."""
    rows = []
    for price in prices_eok:
        for mult in multiples:
            official = int(price * 1e8 / mult)
            t = compute_holding_tax_from_official(official)
            rows.append({"price_eok": price, "multiple": mult, "official_eok": round(official / 1e8, 2), **t})
    return rows




def _params_source_txt(P=None) -> str:
    """보유세 파라미터의 실제 출처 — 코드 기본값(is_default)이면 그렇게 말한다(S3 Codex P2)."""
    P = P or fin._P
    if getattr(P, "is_default", True):
        return "코드 기본값(정책 스냅샷 미확인 — 출발점일 뿐 정책 사실 아님)"
    return f"PolicyParams 캐시(확인일 {getattr(P, 'confirmed_date', None) or '미기록'})"


# ── calc.html ──────────────────────────────────────────────────────────────────
def _won(v: int, signed: bool = False) -> str:
    return f"{v:+,}원" if signed else f"{v:,}원"


def _eok(v: int) -> str:
    return f"{v / 1e8:.2f}억"


_CALC_JS = """
function pmt(P,r,n){r=r/100/12;if(r<1e-9)return P/n;return P*r/(1-Math.pow(1+r,-n));}
function pv(m,r,n){r=r/100/12;if(r<1e-9)return m*n;return m*(1-Math.pow(1+r,-n))/r;}
function won(v){return Math.round(v).toLocaleString('ko-KR')+'원';}
function sgn(v){return (v>=0?'+':'')+Math.round(v).toLocaleString('ko-KR')+'원';}
function recalc(){
  var g=function(id){var v=document.getElementById(id).value.trim();return v===''?NaN:+v;};   /* 빈칸은 0 이 아니라 NaN → 계산 보류(S6b Codex N3) */
  var t=function(id,v){var el=document.getElementById(id);if(el)el.textContent=v;};
  var grid=function(v){return Math.round(v*100)/100===v;};   /* 백분율은 0.01 격자만 — 파이썬 round(v,2)!=v 와 같은 집합(S6c Codex R3) */
  var P=Math.round(g('loan')*1e8),y=Math.min(Math.floor(g('years')),30),rate=g('rate'),inc=Math.round(g('income')*1e4),st=g('stress'),fx=g('fixed'),cap=Math.round(g('cap')*1e8);
  if(!(isFinite(P)&&isFinite(inc)&&isFinite(cap)&&P>0&&P<=1e13&&y>0&&inc>=0&&inc<=1e13&&cap>=0&&cap<=1e13&&rate>=0&&rate<=100&&st>=0&&st<=100&&fx>=0&&fx<=100&&grid(rate)&&grid(st)&&grid(fx))){t('calc-msg','입력이 비었거나 범위 밖(금액 1e13원 이하·백분율 0~100 소수 둘째 자리까지·기간 1년 이상) — 표는 마지막 유효 입력 기준.');return;}   /* 백분율 0~100 · 금액 1e13원 이하만(∞·NaN·오버플로 차단, S5/S6 Codex) */
  t('calc-msg','');
  t('d-loan',(P/1e8)+'억');t('d-years',y+'년');t('d-income',(inc/1e8)+'억');t('d-stress',st.toFixed(2)+'%p');t('d-stress2',st.toFixed(2)+'%p');
  t('d-cap',cap>0?(cap/1e8).toFixed(2)+'억(입력)':'상한 없음');t('d-fixed',fx.toFixed(2)+'%');t('d-rate',rate.toFixed(2)+'%');t('d-rate2',rate.toFixed(2)+'%');t('d-years2',y+'년');
  var steps=[-50,-25,0,25,50],base=Math.floor(pmt(P,rate,y*12)),rows='';
  steps.forEach(function(bp){var r=rate+bp/100;if(r<0)return;var m=Math.floor(pmt(P,r,y*12));   /* 음수 금리 행 제외 = 파이썬 동일 */
    rows+='<tr><td>'+(bp>0?'+':'')+bp+'bp</td><td>'+r.toFixed(2)+'%</td><td>'+won(m)+'</td><td>'+sgn(m-base)+'</td><td>'+sgn((m-base)*12)+'</td></tr>';});
  document.getElementById('pay-rows').innerHTML=rows;
  var lim=function(r){var v=Math.floor(pv(inc*0.4/12,r+st,y*12));return cap>0?Math.min(v,cap):v;};
  var bl=lim(rate);rows='';
  steps.forEach(function(bp){var r=rate+bp/100;if(r<0)return;var l=lim(r);
    rows+='<tr><td>'+(bp>0?'+':'')+bp+'bp</td><td>'+(r+st).toFixed(2)+'%</td><td>'+(l/1e8).toFixed(2)+'억'+(cap>0&&l>=cap?' (상한)':'')+'</td><td>'+(l-bl>=0?'+':'')+((l-bl)/1e8).toFixed(2)+'억</td></tr>';});
  document.getElementById('dsr-rows').innerHTML=rows;
  var gap=Math.round((fx-rate)*100)/100;   /* 파이썬 compute_breakeven 과 같이 격차를 2자리로 반올림한 뒤 25bp 횟수(S6b Codex N4) */
  document.getElementById('be').textContent=gap>0?gap.toFixed(2)+'%p = 25bp 단위 '+Math.ceil(gap/0.25-1e-9)+'회':'고정이 변동 이하(격차 '+gap.toFixed(2)+'%p)';
}
document.querySelectorAll('input').forEach(function(i){i.addEventListener('input',recalc);});
"""


def render_calc_page(macro_ctx: dict | None, today: str, stress_pp: float | None = None) -> str:
    """calc.html — 관측값(사실) 한 줄 + 입력([가정]) + 표 4개(월 상환·스트레스 DSR·손익분기·공시가 배율→보유세). JS 는 같은 산식."""
    from blog import build_explorer as be
    from blog.build_site import BASE_URL, ga4_snippet
    from blog.daily_digest import _SITE_CSS
    stress = DEFAULT_STRESS_PP if stress_pp is None else stress_pp
    cap = fin.resolve_loan_abs_cap(DEFAULT_PRICE_KRW, regulated=True)
    by = {c["code"]: c for c in (macro_ctx or {}).get("cards", [])}
    obs = " · ".join(f"{by[c]['label']} {by[c]['value_txt']}({by[c]['date']})" for c in ("bok_base", "cofix_new", "kr_govt3y") if c in by)
    obs_p = f'<p class=meta>관측값(사실): {obs}</p>' if obs else '<p class=meta>관측값 미수집 — 아래 표는 입력값(가정)만으로 계산.</p>'
    pay = build_scenario_table(DEFAULT_LOAN_KRW, DEFAULT_YEARS, DEFAULT_RATE_PCT)
    pay_rows = "".join(f'<tr><td>{r["step_bp"]:+d}bp</td><td>{r["rate_pct"]:.2f}%</td><td>{_won(r["monthly_krw"])}</td>'
                       f'<td>{_won(r["delta_monthly_krw"], True)}</td><td>{_won(r["delta_annual_krw"], True)}</td></tr>' for r in pay)
    dsr = build_dsr_table(DEFAULT_INCOME_KRW, DEFAULT_RATE_PCT, DEFAULT_YEARS, stress, cap_krw=cap)
    dsr_rows = "".join(f'<tr><td>{r["step_bp"]:+d}bp</td><td>{r["stress_rate_pct"]:.2f}%</td><td>{_eok(r["limit_krw"])}{" (상한)" if r["capped"] else ""}</td>'
                       f'<td>{r["delta_krw"] / 1e8:+.2f}억</td></tr>' for r in dsr)
    be_ = compute_breakeven(DEFAULT_FIXED_PCT, DEFAULT_RATE_PCT)
    be_txt = f'{be_["gap_pp"]:.2f}%p = 25bp 단위 {be_["steps_25bp"]}회' if be_["gap_pp"] > 0 else f'고정이 변동 이하(격차 {be_["gap_pp"]:.2f}%p)'
    hold_rows = "".join(f'<tr><td>{r["price_eok"]}억</td><td>{r["multiple"]:.2f}</td><td>{r["official_eok"]:.2f}억</td><td>{_won(r["property_tax_krw"])}</td>'
                        f'<td>{_won(r["jongbu_tax_krw"])}</td><td><b>{_won(r["total_krw"])}</b></td></tr>' for r in build_holding_tax_table())
    cap_txt = f"{_eok(cap)}(규제지역 절대상한, 매매가 {_eok(DEFAULT_PRICE_KRW)} 입력 기준)" if cap else "상한 없음"
    return f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>전달 계산기 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="대출금리 변화(bp)별 월 상환·스트레스 DSR 한도·고정/변동 격차·공시가 배율별 보유세를 결정론 산식으로 계산. 관측값과 [가정] 입력만 쓴다.">
<style>{_SITE_CSS}
label{{display:inline-block;margin:4px 10px 4px 0;font-size:13px}}input{{width:84px;padding:4px 6px;border:1px solid #cfc9bc;border-radius:6px;font:inherit}}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="index.html">구 허브</a><a href="explorer.html">탐색기</a><a href="daily/latest.html">오늘의 변화</a><a href="macro.html">거시 지표</a><a href="methodology.html">방법론</a></nav>
<div class=crumb><a href="index.html">서울</a> › <a href="macro.html">거시 지표</a> › 전달 계산기</div>
<h1>전달 계산기</h1>
<p class=meta>{today} · 결정론 산식(원리금균등·DSR 월 단위·정책 파라미터). 기준금리 결정이 대출금리로 얼마나 옮겨가는지는 여기서 가정하지 않는다 — "대출금리가 이만큼 움직이면 이 산수" 를 행으로 둔다.</p>
{obs_p}
<h2>입력 <span class=mut>(전부 [가정] — 은행별 실제 금리는 은행연합회 비교공시)</span></h2>
<p><label>대출금(억) <input id=loan type=number step=0.1 value={DEFAULT_LOAN_KRW / 1e8:g} min=0 max={MAX_INPUT_KRW // 10**8}></label><label>기간(년, 상한 {TERM_CAP_YEARS}) <input id=years type=number max={TERM_CAP_YEARS} value={DEFAULT_YEARS}></label>
<label>대출금리(%) <input id=rate type=number step=0.01 value={DEFAULT_RATE_PCT:.2f} min=0 max=100></label><label>고정금리(%) <input id=fixed type=number step=0.01 value={DEFAULT_FIXED_PCT:.2f} min=0 max=100></label>
<label>연소득(만원) <input id=income type=number value={DEFAULT_INCOME_KRW // 10000} min=0 max={MAX_INPUT_KRW // 10**4}></label><label>스트레스 가산(%p) <input id=stress type=number step=0.1 value={stress:.2f} min=0 max=100></label>
<label>절대상한(억, 0=없음) <input id=cap type=number step=0.1 value={(cap or 0) / 1e8:g} min=0 max={MAX_INPUT_KRW // 10**8}></label></p>
<p class=mut id=calc-msg></p>
<h2>1. 대출금리 변화 → 월 상환 <span class=mut>(대출금 <span id=d-loan>{DEFAULT_LOAN_KRW / 1e8:g}억</span> · <span id=d-years>{DEFAULT_YEARS}년</span> · 기준 대출금리 <span id=d-rate>{DEFAULT_RATE_PCT:.2f}%</span> [가정] · 원리금균등, 월액 원 단위 절사)</span></h2>
<div class=tblwrap><table><tr><th>변화</th><th>금리</th><th>월 상환</th><th>월 Δ</th><th>연 Δ</th></tr><tbody id=pay-rows>{pay_rows}</tbody></table></div>
<h2>2. 스트레스 DSR 한도 <span class=mut>(연소득 <span id=d-income>{DEFAULT_INCOME_KRW / 1e8:g}억</span> · DSR {DSR_LIMIT:.0%} · 가산 <span id=d-stress>{stress:.2f}%p</span> · 만기 <span id=d-years2>{DEFAULT_YEARS}년</span> · 기존부채 0 [가정])</span></h2>
<div class=tblwrap><table><tr><th>변화</th><th>한도 산정 금리</th><th>한도</th><th>Δ</th></tr><tbody id=dsr-rows>{dsr_rows}</tbody></table></div>
<p class=mut>절대상한: <span id=d-cap>{cap_txt}</span>. 스트레스 가산 <span id=d-stress2>{stress:.2f}%p</span>(입력값) · 정책 기본값 {DEFAULT_STRESS_PP:.2f}%p = 수도권·규제지역(10.15 대책, 2025-10-16 시행), 입력을 바꾸면 위 값은 [가정]. 한도 = 연소득×DSR 을 월 상환액으로 보고 (대출금리+스트레스 가산)로 현재가치 환산(finance.compute_dsr_loan 과 같은 식). 만기 상한 {TERM_CAP_YEARS}년(6.27 대책).</p>
<h2>3. 고정 − 변동 격차</h2>
<p>고정 <span id=d-fixed>{DEFAULT_FIXED_PCT:.2f}%</span> − 변동 <span id=d-rate2>{DEFAULT_RATE_PCT:.2f}%</span> [가정] = <b id=be>{be_txt}</b> <span class=mut>(격차를 25bp 단위로 센 것 — 어느 쪽이 낫다는 판단 아님)</span></p>
<h2>4. 공시가 배율 → 1주택 연 보유세 <span class=mut>(공시가 = 실거래 중위 [가정] ÷ 배율 [실측])</span></h2>
<div class=tblwrap><table><tr><th>실거래 중위 [가정]</th><th>배율 [실측]</th><th>공시가</th><th>재산세(도시지역분 포함)</th><th>종부세</th><th>합계</th></tr>{hold_rows}</table></div>
<p class=mut>배율 {HOLDING_MULTIPLES[1]:.2f} = {HOLDING_MULTIPLES_ASOF} 발행 풀 중위(공시가를 찾은 면적과 발행 면적이 일치하는 단지), {HOLDING_MULTIPLES[0]:.2f} = 같은 날 20억 이상 밴드 중위 — 실측값 그대로. 실거래 중위 행(10·15·20·30억)은 예시 가정. 단지별 배율은 탐색기의 '공시가 배율' 열. 재산세 1주택 특례(공정시장가액비율·9억 이하 특례세율)·종부세 12억 공제·공정시장가액비율 = {_params_source_txt()} — 실제 고지서와 다를 수 있다.</p>
<div class=foot>
산식: 월 상환 = P·r/(1−(1+r)^−n). DSR 한도 = (연소득×{DSR_LIMIT:.0%}/12)×[1−(1+r_m)^−n]/r_m, r_m = (대출금리+가산)/12. 전달 폭(기준금리→코픽스→대출금리)은 <a href="macro.html">거시 지표</a>의 전달시차 표와 월간결산의 과거 통계에서 다룬다.<br>
이 페이지의 수치는 입력 가정·정책 파라미터·관측 지표(기준일 표기)로 계산한 산수이며 실거래 통계가 아니다. {fin.FINANCE_CONFIRM_NOTICE} {be._takedown()}<br>
<a href="methodology.html">방법론 전문</a> · <a href="macro.html">거시 지표</a> · <a href="{BASE_URL}/calc.html">{BASE_URL}/calc.html</a>
</div>
</div>
<script>{_CALC_JS}</script>
</body></html>"""
