"""§0~§11 HTML 리포트 조립 — 레퍼런스(real_estate_report_20260519/0421_v14) 구조 고정.
모든 수치는 주입된 결정론 산출에서 옴 (LLM 재계산 금지). 출처/확인일/Provenance 표기."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..analysts.compset import (assign_sg, bucket_area,
                                has_hard_fail, lookup_band, recent_cagr)
from ..analysts.compset_signals import fitness_facts, mean_reversion_signal
from ..analysts.decision_prior import (EMP_NOTE, catalyst_flags,
                                       district_prior, employment_corridor,
                                       liquidity_tailwind)
from ..analysts.finance import FinancePlan, assess_policy_loans
from ..analysts.regime import (KEY_POLICY, current_regime,
                               jeonse_value_timing, regime_conditional_guidance,
                               regime_entry_read, regime_evidence,
                               sentiment_confirms)
from ..analysts.redev import RedevScore
from ..analysts.scoring import AXES, AxisScores, WEIGHTS, FUNDAMENTAL_AXES, PRICE_DERIVED_AXES
from ..analysts.risk import RiskFlag
from ..analysts.trend import PriceTrend
from ..domain import Candidate, ExitStrategy
from ..policy_params import PolicyParams
from .scenario import BreakEven, HoldScenario, project_networth_15yr

EOK = 100_000_000
_DEFAULT_PP = PolicyParams()   # §7 carry 대칭화용 관리비·수선 근사(가정 항목 — 캐시 override 거의 없음)


@dataclass
class Evaluated:
    candidate: Candidate
    finance: FinancePlan
    redev: RedevScore
    axis: AxisScores
    hold: HoldScenario | None
    break_even: BreakEven | None
    flags: tuple[RiskFlag, ...] = ()
    adjusted_total: float = 0.0   # axis.weighted_total × Risk Flag 페널티 (전체 10축 — 참고 표시용)
    adjusted_fundamental: float = 0.0   # ★호가무관 fundamental_total × 페널티 — *순위 기준*(2026-06-04 Wittgenstein 호가분리)
    trend: PriceTrend | None = None   # ② MOLIT 실거래 추세


def _eok(v: int | float) -> str:
    return f"{v / EOK:.2f}억"


def _row(cells, tag="td"):
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def _gu(c) -> str:
    """후보의 자치구 — district('서울 강서구')의 끝 토큰('강서구'). 미상이면 ''."""
    d = getattr(c, "district", "") or ""
    return d.split()[-1] if d else ""


def _nm(c) -> str:
    """[구]단지명 — 동명단지(두산×3·현대×2·삼환×2 등) 한눈 구분 (사용자 요청 2026-06-03, 예: [도봉구]럭키).
    표시 전용 — reviews/structural/location/trust dict 의 *조회 키*는 원본 complex_name 유지(매핑 불변)."""
    g = _gu(c)
    return f"[{g}]{c.listing.complex_name}" if g else c.listing.complex_name


def _unit(c) -> str:
    """[구]단지명 + 매물 식별자(동·전용평형·층·향). 같은 단지가 여러 행일 때 각 행이
    '별개 매물'임을 한눈에 구분 (사용자 요청 2026-05-29 / 구표기 2026-06-03). §3 외 단지반복 테이블에 사용.
    인자는 Candidate(구 추출 위해) — 내부에서 listing 파생."""
    l = c.listing
    return (f"{_nm(c)} <span class=sub>{l.dong_ho}·전용{l.area_exclusive_m2:.0f}㎡"
            f"·{l.floor}·{l.facing}</span>")


def _card(label: str, value: str, sub: str = "") -> str:
    """BLUF KPI 카드 — 값은 결정론 주입값(AGENT_CALC)만. 불확실(미래/밴드) 값 금지(관점3 falsifier)."""
    s = f"<div class=cs>{sub}</div>" if sub else ""
    return f"<div class=card><div class=k>{label}</div><div class=v>{value}</div>{s}</div>"


def _bar(score: float) -> str:
    """5축 점수(0~5) 시각 막대 + 수치. 텍스트보다 후보 간 비교 즉각(관점3)."""
    pct = max(0.0, min(100.0, score / 5 * 100))
    return f"<span class=bar><i style='width:{pct:.0f}%'></i></span> {score:.1f}"


def _thl(label: str, anchor: str) -> str:
    """클릭 가능한 컬럼 헤더 — 마스터 종합표에서 그 컬럼의 세부 섹터로 점프(사용자 요청 2026-06-03)."""
    return f"<a href='#{anchor}'>{label}</a>"


def _micro10(scores: dict, axes) -> str:
    """10축 점수를 한 칸의 미니 막대 10개로(한눈 프로파일). 높은 축은 보라 강조. title=축명·점수."""
    bars = []
    for a in axes:
        v = scores.get(a, 0.0)
        h = max(2, round(v / 5 * 22))
        cls = "hi" if v >= 4.0 else ""
        bars.append(f"<i class='{cls}' style='height:{h}px' title='{a} {v:.1f}'></i>")
    return f"<span class=m10>{''.join(bars)}</span>"


# ── 재편 A 3신호 (검증된 것만) — compset 주입 테이블에서 결정론 산출 ───────────────
# 설계 단일소스: report/audit/audit_identity-rearch_20260601.md §8/§12.
# ① 생활권 base-rate(주신호) ② within-구 mean-reversion(타이밍) ③ 적합도 facts + 하드필터.
# 10축 weighted_total 매력도 RANKING 은 Turing 게이트 미통과(ρ_oos=0.06)로 폐기 → 별지 D(deprecated).
def _legal_status(e: "Evaluated") -> str:
    """하드게이트 결과(점수 아님) — 예산초과/이주단계 = FAIL, 그 외 플래그 = 조건부, 무플래그 = PASS."""
    codes = {f.code for f in e.flags}
    if "F_OVERBUDGET" in codes:
        return "FAIL(예산초과)"
    if e.candidate.redev_stage.blocks_residence:
        return "FAIL(이주단계·거주불가)"
    if codes:
        return "조건부(" + ",".join(sorted(codes)) + ")"
    return "PASS"


def _sg_of(compset: dict | None, candidate) -> str | None:
    """후보의 생활권 — ★후보가 직접 보유한 saenghwalgwon 우선(일반명 충돌 방지),
    없으면 compset.assign 폴백."""
    return candidate.saenghwalgwon or assign_sg(compset, candidate.listing.complex_name)


def _compset_band(compset: dict | None, candidate):
    """후보의 (생활권 × 그 매물 전용band) base-rate 밴드(평형별). sg 는 후보 보유분 우선."""
    return lookup_band(compset, _sg_of(compset, candidate), candidate.listing.area_exclusive_m2)


def _compset_mr(compset: dict | None, candidate):
    """후보의 within-생활권 mean-reversion 신호 — 그 (생활권·단지·전용band) 기준. 미주입이면 None."""
    l = candidate.listing
    sg = _sg_of(compset, candidate)
    rc = recent_cagr(compset, sg, l.complex_name, l.area_exclusive_m2)
    if rc is None:
        return None
    peers = (compset.get("peers_recent") or {}).get(f"{sg}|{bucket_area(l.area_exclusive_m2)}") if compset else None
    return mean_reversion_signal(l.complex_name, rc, peers or [])


def _review_reliability(rv: dict | None) -> str:
    """후기 편향 보정 라벨(2026-06-04) — 표본크기·출처 기반 신뢰도 경고. 편향: 소유주·만족거주자 치우침,
    호갱노노 Q&A 코멘트는 정식평점 아님. n 작을수록·단일출처일수록 약함."""
    if not rv:
        return ""
    n = rv.get("n_seen", 0)
    src = (rv.get("_source") or "")
    flags = []
    if n == 0:
        return "표본없음"
    if n < 3:
        flags.append("표본 매우 약함(n&lt;3)")
    elif n < 5:
        flags.append("표본 약함")
    if "Q&A" in src or "이야기" in src or ("당근" in src and "프리뷰" in src):
        flags.append("소유주·만족 편향 가능")
    if not rv.get("themes_caution"):
        flags.append("단점 미수집(과대평가 주의)")
    return " · ".join(flags) if flags else "상대 충분"


def _hard_fail(e: "Evaluated") -> bool:
    return has_hard_fail(e.flags)


# ── 온보딩 서사 "이 리포트 읽는 법" (agent-narrative 직조, 2026-06-05) ───────────────
# 정직성 보존(ρ=0.08 노출·정직 박스·falsifier) — 수익예측기 아닌 '큰 실수 회피' 프레임 각인.
HOW_TO_READ_SECTION = '''<section id="how-to-read" class="box" style="border-left:5px solid #4A6C8C;background:#EEF1F4;line-height:1.65">
  <h2 style="margin-top:0">이 리포트 읽는 법</h2>

  <h3>1. 왜 이 리포트인가</h3>
  <p>이 리포트는 <strong>"어느 집이 제일 많이 오를까"를 맞히는 예측기가 아닙니다.</strong>
    수많은 단지를 같은 잣대로 줄세워 1등을 고르는 도구도 아닙니다. 목적은 단 하나,
    <strong>"큰 실수를 피하는 것"</strong>입니다. 내 예산·법규로 애초에 살 수 없는 매물에
    마음을 뺏기거나, 환금성이 약하고 전세 수요가 받쳐주지 않는 동네에 큰돈을 묶는 일을 줄이는 것.
    미래 가격은 누구도 점으로 맞히지 못하므로, 이 리포트는 <em>점수로 등수를 매기는 대신
    "지뢰를 밟지 않게" 돕는 의사결정 지원기</em>로 설계됐습니다.</p>

  <h3>2. 3단계로 읽으세요</h3>
  <p>아래 순서가 이 리포트의 1차 프레임입니다. 단계를 건너뛰면 정밀 점수(10축)에 먼저 휩쓸리기 쉽습니다.</p>
  <ul>
    <li><strong>① 생활권 PRIOR — "어느 동네가 구조적으로 강한가"</strong><br>
      장기 base-rate(과거 실적의 평균적 경향)와 고용 코리도(일자리 밀집 축)로 본 동네의 기초 체력.
      <em>비유: 학교를 고르기 전에 "어느 학군이 탄탄한가"부터 보는 것.</em></li>
    <li><strong>② 예산·법규 하드필터 — "내 돈으로 실제로 살 수 있나"</strong><br>
      내 자기자본과 DSR(소득 대비 원리금 상환 비율)로 통과 못 하는 매물은 점수와 무관하게 거른다.
      <em>비유: 아무리 좋은 차도 예산을 넘으면 후보가 아니다 — 먼저 가격표를 본다.</em></li>
    <li><strong>③ 10축 조정점수 — "같은 급지 안에서 정밀 검수"</strong><br>
      ①②를 통과한 비슷한 급지의 매물끼리 10개 항목으로 정밀 비교하는 tie-breaker(동점 가르기).
      <em>비유: 최종 후보 몇 곳을 두고 항목별 체크리스트로 마지막 점검하는 것.</em></li>
  </ul>

  <h3>3. 지표 사전</h3>
  <ul>
    <li><strong>생활권 PRIOR</strong> — 그 동네의 구조적 기초 체력(장기 base-rate + 고용 축). 개별 매물보다 먼저 보는 1순위.</li>
    <li><strong>조정점수(호가 무관)</strong> — 매도자 제시가인 <em>호가</em>에 흔들리지 않도록 호가를 빼고 매긴 단지의 상대 점수. 비싸게 부른다고 점수가 오르지 않는다.</li>
    <li><strong>10축</strong> — 조정점수를 구성하는 10개 항목:
      <ul>
        <li><strong>전세 수요</strong> — 전세를 찾는 사람이 꾸준한가(공실·역전세 위험의 반대 신호).</li>
        <li><strong>환금성</strong> — 팔고 싶을 때 제값에 빨리 팔리는가.</li>
        <li><strong>가격 방어</strong> — 하락장에서 시세가 덜 빠지는가.</li>
        <li><strong>상승 여력</strong> — 오를 여지가 남았는가(과거 경향 기준, 보장 아님).</li>
        <li><strong>토지 지분</strong> — 한 채에 깔린 대지 몫(재건축 시 가치의 뼈대). 단지 평균에서 추정한 값.</li>
        <li><strong>가격 메리트</strong> — 같은 급지 <em>시세</em> 대비 싼가 비싼가.</li>
        <li><strong>출퇴근</strong> — 주요 고용지로 가는 시간·환승 부담.</li>
        <li><strong>학군</strong> — 배정·통학 여건·입시학원 밀집.</li>
        <li><strong>경사</strong> — 단지까지의 언덕·도보 부담(생활 체감).</li>
        <li><strong>후기</strong> — 실거주자 정성 평가(주관적·소유주 편향 가능, 보조 참고).</li>
      </ul></li>
    <li><strong>촉매</strong> — 동네를 끌어올릴 외부 동력(고용 유입 / 교통 개통 / 재건축 진척). <strong>2개 이상(≥2)이면 "강함"</strong>. 1개짜리는 단일 변수 의존 위험.</li>
    <li><strong>base-rate</strong> — 과거 같은 조건에서 평균적으로 어땠는지의 기준선. "이번엔 다르다"는 직감보다 먼저 보는 닻.</li>
    <li><strong>⚠️ (예산/법규 제약)</strong> — 내 자기자본·LTV·DSR로 통과하지 못하는 표시. <strong>⚠️가 붙은 매물은 점수가 높아도 후보가 아니다.</strong></li>
    <li><strong>전세가율</strong> — <em>전세가</em> ÷ 매매가. 높을수록 <em>갭</em>(매매가−전세가)이 작지만, 동시에 갭주도 과열·역전세 위험 신호이기도 하다.</li>
    <li><strong>용적률</strong> — 대지 대비 건물 연면적 비율. 낮을수록 재건축 시 더 지을 여지(사업성)가 큰 경향.</li>
    <li><strong>DSR 스트레스</strong> — 현재 금리가 아니라 더 높은 가산 금리를 가정해 상환 부담을 계산하는 규제. 실제 빌릴 수 있는 <em>대출한도</em>가 체감보다 줄어든다.</li>
  </ul>

  <div style="border:2px solid #C06A6A;border-radius:8px;padding:14px 18px;margin:20px 0 4px;background:#FBEFEF">
    <h3 style="margin-top:0;color:#C06A6A">이 리포트가 못 하는 것 (정직 박스)</h3>
    <ul style="margin-bottom:0">
      <li><strong>미래 가격을 점으로 예측하지 못합니다.</strong> 백테스트에서 10축 점수와 미래 가치상승의 상관은
        <strong>ρ=0.08(거의 0)</strong>이었습니다 — 점수는 <em>실수 회피용 검수 도구</em>이지 수익 예측기가 아닙니다.</li>
      <li><strong>토지 지분(대지 몫)은 추정값입니다.</strong> 단지 평균에서 환산했으므로 동·호수별 실제 등기 지분과 다를 수 있습니다.
        재건축 의사결정 시 반드시 개별 등기로 확인하세요.</li>
      <li><strong>모든 수치는 점예측이 아니라 신호입니다.</strong> 시세·전세가율·점수는 특정 시점의 관측이며 시장이 바뀌면 함께 변합니다.</li>
      <li><strong>지금은 과열 국면입니다.</strong> 현 시장은 <strong>OVERHEATED</strong> 판정이고 전세가율은 <strong>53.4%(역사적 1퍼센타일)</strong>
        — 갭주도 과열·진입 리스크가 높고 평균 되돌림(reversion) 위험이 평소보다 큽니다. 점수가 좋아 보여도 진입 타이밍 위험을 별도로 감안하세요.</li>
    </ul>
  </div>
</section>'''


def build_report(*, profile: dict, strategy: ExitStrategy, evaluated: list[Evaluated],
                 policies: list[dict], today: date,
                 council_insight: str | None = None, council_session: str | None = None,
                 policy_meta: dict | None = None,
                 sensitivity: list | None = None, base_top: str | None = None,
                 council_models: int | None = None, trust_scores: list | None = None,
                 location: dict | None = None, reviews: dict | None = None,
                 strategy_by_name: dict | None = None, structural: dict | None = None,
                 cagr: dict | None = None, compset: dict | None = None,
                 coverage: dict | None = None) -> str:
    # 정렬 = 하드필터(예산·법규) 통과 게이트 → 10축 조정점수(adjusted_total) desc. 10축 매트릭스가 주 비교 프레임.
    # 생활권 base-rate·within-구 mean-reversion 은 §★ 보조 FACT 신호로 병치(거시 가이드·점수 폐기 아님).
    # ★순위 = 호가무관 adjusted_fundamental(2026-06-04 Wittgenstein 호가분리). 가격대비 매력(가격메리트·전세수요)은
    #   순위에서 분리해 별도 '가격 매력도'로 병치 — '많이 빠짐→싸짐→상위' merit-leakage(H1) 차단.
    ev = sorted(evaluated, key=lambda e: (not _hard_fail(e), e.adjusted_fundamental),
                reverse=True)
    # ★동명단지 충돌 가드(2026-06-07 적대감사): reviews/location 등이 complex_name(bare)로 키잉돼, 동명 단지
    #   (예 양천·구로 '서울가든')가 bare fallback 으로 *다른 단지 데이터*를 표시하던 결함. 중복 이름은 bare fallback 금지
    #   → [구]name(_nm) 키로만 해석, 없으면 '미수집'으로 정직 표기. (resolve_named 헬퍼로 일원화.)
    _dups = {n for n in (e.candidate.listing.complex_name for e in ev)
             if sum(1 for x in ev if x.candidate.listing.complex_name == n) > 1}
    def resolve_named(data, c):
        if not data:
            return None
        v = data.get(_nm(c))
        if v is not None:
            return v
        return None if c.listing.complex_name in _dups else data.get(c.listing.complex_name)
    flagged = [e for e in ev if e.flags]
    w = WEIGHTS[strategy]
    # ★색감: 눈 편한(low eye-strain) 따뜻한 종이톤 팔레트 (sage gemini-2.5-pro 추천, 2026-06-05).
    #   순백/순흑 글레어 회피 · off-white #F8F5F0 · 부드러운 다크 #413F3C · 차분한 슬레이트 accent #4A6C8C
    #   · 채도 낮춘 positive/negative · WCAG AA 본문대비 유지.
    css = """body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;color:#413F3C;line-height:1.62;background:#F8F5F0;margin:0;-webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 70px}h1{font-size:23px;margin:0 0 4px;color:#34322F}
h2{font-size:18px;border-bottom:2px solid #D8D2C8;padding-bottom:6px;margin:30px 0 10px;color:#3A4F63}
table{border-collapse:collapse;width:100%;font-size:12.7px;background:#FCFAF7;margin:8px 0}
th,td{border:1px solid #EAE6E0;padding:6px 8px;text-align:left;vertical-align:top}th{background:#F2EEE8;color:#3A4F63}
.win{background:#E4EAF0}.sub{color:#6D6A65;font-size:12px}.good{color:#5A8B74;font-weight:700}.bad{color:#C06A6A;font-weight:700}
.box{background:#FCFAF7;border:1px solid #EAE6E0;border-radius:8px;padding:13px 16px;margin:12px 0}
.tag{font-size:10.5px;color:#6D6A65}.src{font-size:11px;color:#6D6A65}.rank{font-weight:800;color:#4A6C8C}
.crit{border-left:5px solid #E6C86E;background:#FFF8E3}
.bluf{background:#EEF1F4;border:1px solid #D5DEE6;border-left:5px solid #4A6C8C;border-radius:10px;padding:16px 20px;margin:14px 0}
.kpi{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 4px}
.card{flex:1;min-width:132px;background:#FCFAF7;border:1px solid #EAE6E0;border-radius:8px;padding:10px 13px}
.card .k{font-size:11px;color:#6D6A65}.card .v{font-size:19px;font-weight:800;margin-top:2px;line-height:1.25;color:#3A4F63}.card .cs{font-size:10.5px;color:#8A867F;margin-top:2px}
.bar{display:inline-block;vertical-align:middle;width:74px;height:9px;background:#ECE7DF;border-radius:5px;overflow:hidden;margin-right:5px}
.bar>i{display:block;height:100%;background:#4A6C8C}
.note{font-size:11.5px;color:#6D6A65;margin:5px 0}
.toc{font-size:12px;color:#56534E;margin:8px 0 20px;padding:8px 0;border-top:1px solid #EAE6E0;border-bottom:1px solid #EAE6E0}
.toc a{color:#4A6C8C;text-decoration:none;margin-right:14px;white-space:nowrap}
.annexsep{border:0;border-top:2px dashed #D8D2C8;margin:42px 0 6px}
.annexlabel{font-size:12.5px;color:#8A867F;letter-spacing:.04em;margin:0 0 6px}
@media(max-width:760px){table{display:block;overflow-x:auto;white-space:nowrap}.kpi{flex-direction:column}}
details{background:#FCFAF7;border:1px solid #EAE6E0;border-radius:8px;margin:10px 0}
summary{cursor:pointer;padding:12px 16px;font-weight:700;font-size:15px;list-style:none}
summary::-webkit-details-marker{display:none}summary:before{content:'▸ ';color:#4A6C8C}
details[open] summary:before{content:'▾ '}
.dwrap{padding:0 16px 14px}
th a{color:#4A6C8C;text-decoration:none;border-bottom:1px dotted #AFC0CE}
th a:after{content:'▸';font-size:8px;color:#4A6C8C;margin-left:2px;vertical-align:super}
th a:hover{background:#E4EAF0}
.master{font-size:12px}.master td,.master th{padding:5px 7px;text-align:center}.master td:nth-child(2){text-align:left}
.m10{display:inline-flex;gap:1.5px;align-items:flex-end;height:22px}
.m10 i{width:4px;background:#BBC9D6;border-radius:1px}.m10 i.hi{background:#4A6C8C}
.hl{background:#F3EDE0;border-radius:4px;padding:1px 5px;font-weight:700;color:#9A7B3C}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px;vertical-align:baseline}
.click-hint{font-size:11px;color:#4A6C8C;margin:2px 0 6px}"""

    ts_by_name = {t.candidate_name: t for t in trust_scores} if trust_scores else {}

    H = [f"<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>"
         f"<meta name=viewport content=\"width=device-width,initial-scale=1\">"
         f"<title>부동산 리포트 {today}</title><style>{css}</style></head><body><div class=wrap>"]
    H.append(f"<h1>부동산 매수 의사결정 리포트 — {strategy.value}</h1>")
    H.append(f"<div class=sub>생성 {today} · agent_realestate v0.3 · 발견=네이버 스캔 + MOLIT 감사 union · 결정론 계산(AGENT_CALC) · 자체 학군 알고리즘(성취도+특목고 아실/학교알리미) · 재무 3중 binding(LTV·스트레스DSR30년·6억한도) · 적대검증 반영(세대수/학군 이중카운트 제거·실효가중 표기) · 눈편한 종이톤</div>")
    H.append(HOW_TO_READ_SECTION)   # 온보딩 서사(읽는 법) — 최상단 (agent-narrative, 2026-06-05)

    # ── 발견 커버리지 고지 (P0, 2026-06-03) — '전수조사' overclaim 교정. MOLIT 감사로 누락 표면화 ──
    if coverage:
        cov = coverage.get("coverage_pct")
        gap_n = coverage.get("gap")
        gaps = coverage.get("top_gap", [])
        gtxt = ", ".join(f"[{g[0]}]{g[1]}({g[2]}억·거래{g[3]})" for g in gaps[:8])
        ku = coverage.get("kapt_universe")
        nv = coverage.get("naver_scanned")
        univ = (f"<b>K-apt 권위 단지 마스터 = {ku:,}단지</b>인데 네이버 스캔은 {nv}만 열거(권위목록의 절반↓). " if ku else "")
        H.append("<div class='box crit'>"
                 f"<b>⚠️ 발견 커버리지 고지 — '전수조사' 아님</b> · 후보 발견은 <b>네이버부동산 마커 스캔</b>(현재 매물 등록 단지 위주=표출 도구)이 1차였다. "
                 + univ +
                 f"<b>MOLIT 실거래로 감사한 결과 발견 커버리지 ≈ {cov}%</b>(거래 n≥30 유동·예산적합 단지 기준 — 거래 활발하나 스캔에 안 잡힌 <b>유동단지 {gap_n}개 누락</b>, 생존편향). "
                 "본 리포트의 모집단은 <b>'네이버 스캔 + MOLIT 감사 union'</b>이며 <b>'서울 10구 전 아파트 전수'가 아니다.</b> "
                 "누락 대단지(거래순): " + gtxt + " 등. <b>관심 단지가 없으면 누락일 수 있으니 직접 추가 요청.</b> "
                 "<span class=sub>재발견 후보(kaptCode 부착)는 ↓ 별지 '발견 감사'. 세대수 자동확보는 K-apt 기본정보 API 활용신청 1회 필요.</span></div>")

    # ── Critical 배너 (tier1: 빨강 — 입력 미검증 고지 + Risk Flag 만 잔류) ──
    H.append("<div class='box crit'>"
             "<b>⚠️ 입력 미검증 고지</b> — 호가·재건축단계·대지지분은 <b>수동 주입</b>이며 코어가 진위를 "
             "검증하지 않는다(타입 가드는 '필드 존재'만 강제). 모든 수치는 매수 전 네이버부동산·등기부·"
             "국세청에서 <b>독립 확인 필수</b>. 본 리포트는 '검증된 입력 가정 하의 계산'이다.</div>")
    # ── 읽는 법 고지 (v8 의사결정 아키텍처 — 예측기 아님, definitive 백테스트 2026-06-04) ──
    H.append("<div class='box' style='border-left:5px solid #4A6C8C;background:#E4EAF0'>"
             "<b>이 리포트 읽는 법 (의사결정 아키텍처)</b> — FACT 백테스트(MOLIT 실거래) 결과 "
             "<b>10축 점수로 단지를 줄세워도 실현 가치상승을 예측하지 못한다</b>(n=84 ρ=0.08, 95%CI 상한 0.285 → 유의미 임계 ρ≥0.30 배제, "
             "상위20%−하위20% 가치상승 차 ≈0). 구 단위 forward 예측도 mean-reversion(−0.73)+검정력천장으로 불가. "
             "→ 이 도구는 <b>수익 예측기가 아니라 *큰 실수 회피* 의사결정 지원기</b>다. "
             "<b>결정 순서: ① §0 생활권 PRIOR(장기 구조 서열 + 고용 코리도, 예측 아닌 prior) → ② 하드필터(예산·법규·현금흐름) → "
             "③ 촉매 watchlist(고용·교통·재건축 Yes/No, ≥2=강함) → ④ §5 10축은 *같은 급지 내 정밀 검수표·tie-breaker*</b>(예측기 아님, 표시 유지). "
             "ρ≥0.30 없이도 15년 보유는 이긴다 — 고base-rate 급지 + 촉매 ≥2 + 하드필터로 정체만 피하면 된다.</div>")

    # ══════ §0 의사결정 아키텍처 (v8 재포지셔닝 구현 — PRIOR + 촉매 watchlist, lead 결정층) ══════
    # 근거: report/backtest/backtest_predictor-upgrade_20260604.html. 예측기 추구 종결 → 구조 PRIOR + binary 촉매.
    H.append("<h2 id=s-decision>§0 의사결정 아키텍처 — 생활권 PRIOR + 촉매 watchlist</h2>")
    H.append("<div class='box' style='border-left:5px solid #5A8B74;background:#EEF3EF'>"
             "<b>예측기 아님</b> — 아래 PRIOR은 '어느 급지가 구조적으로 강한가'의 <b>안정 prior</b>(장기 base-rate + 고용 코리도)이지 "
             "단지별 미래수익 점예측이 아니다. 촉매는 <b>binary watchlist</b>(연속 점수 아님)다. " + EMP_NOTE
             + " 공급 파이프라인 촉매는 <b>데이터 미수집(—)</b>으로 정직히 비운다.</div>")
    _passed = [e for e in ev if not _hard_fail(e)]
    # ★구조 서열(2026-06-06 적대검증) = 생활권 long-run base-rate median 의 풀-내 percentile.
    #   district_prior 가 쓰던 gu_cagr(구 15년 trailing CAGR, forward ρ=−0.69 역상관) 역신호를 대체 — calibration-free 상대 서열.
    _brvals = sorted(b.cagr_median for b in (_compset_band(compset, e.candidate) for e in _passed) if b is not None)
    def _struct_rank(c):
        b = _compset_band(compset, c)
        if b is None or len(_brvals) < 3:
            return None                              # base-rate 부재·풀<3 → 중립(employment 만으로 변별)
        return sum(1 for v in _brvals if v <= b.cagr_median) / len(_brvals)
    _dord = sorted(_passed, key=lambda e: -district_prior(e.candidate, _struct_rank(e.candidate)))
    _regime_active = current_regime().phase in ("BOTTOM", "ASCENDING")
    _allc = [e.candidate for e in ev]
    H.append("<table><tr><th>#</th><th>단지([구])</th><th>생활권 PRIOR</th><th>구 base-rate</th>"
             "<th>고용 코리도</th><th>촉매: 고용</th><th>교통</th><th>재건축조기</th><th>개발호재</th><th>공급</th><th>촉매 합</th>"
             "<th>유동성(거래량)</th></tr>")
    _emplab = {2: "강(≤4km)", 1: "중(≤8km)", 0: "약(>8km)", None: "—"}
    for i, e in enumerate(_dord, 1):
        c = e.candidate
        pr = district_prior(c, _struct_rank(c))
        cat = catalyst_flags(c)
        fl = cat["flags"]
        cg = getattr(c, "gu_cagr", None)
        yn = lambda b: "<span class=good>✓</span>" if b else "<span class=sub>✗</span>"
        strong = " <span class=hl>강함</span>" if cat["strong"] else ""
        lq = liquidity_tailwind(c, _allc, _regime_active)
        lqcell = (f"<span class=good>{lq['level']}↑tailwind</span>" if lq["tailwind"]
                  else f"{lq['level']}" + (f" <span class=sub>({lq['trade_annual']:.0f}건/yr)</span>" if lq.get("trade_annual") else ""))
        H.append(_row([str(i), _nm(c), f"{pr:.2f}{_bar(pr)}",
                       (f"+{cg:.1f}%" if cg is not None else "—"),
                       _emplab[employment_corridor(c)],
                       yn(fl["고용근접"]), yn(fl["교통"]), yn(fl["재건축조기"]), yn(fl["개발호재"]),
                       "<span class=sub>—</span>", f"{cat['yes']}/4{strong}", lqcell]))
    H.append("</table>")
    H.append(f"<div class=note>유동성(거래량) = 구내 연거래수 백분위(상/중/하). "
             f"★현 국면 <b>{current_regime().phase}</b>이라 거래량 tailwind "
             f"{'<b>활성</b>(고거래량 단지=tailwind, feature_discovery ρ≈+0.3)' if _regime_active else '<b>비활성</b>(과열/조정기엔 거래량 변별력 소멸 — 표시만, tailwind 미적용)'}. "
             "국면이 바닥·상승진입으로 전환되면 '상' 거래량 단지가 tailwind로 켜짐.</div>")
    H.append("<div class=note>★PRIOR 구조성분 = <b>생활권 long-run base-rate median 의 풀-내 서열(percentile)</b> "
             "(2026-06-06 적대검증: 구 15년 trailing gu_cagr 는 forward 역상관·mean-revert 라 PRIOR 정렬서 제거 — 아래 '구 base-rate' 컬럼은 맥락 표시용). "
             "★PRIOR 순 정렬(의사결정 1차). §A·§5는 사용자 락대로 10축 조정점수 순(같은 급지 정밀 검수). "
             "촉매 ≥2 = 구조적 tailwind 강함 — 고base-rate 급지 안에서 촉매 강·하드필터 통과 단지가 '큰 실수 회피' 최적.</div>")

    # ── §0.3 사이클 국면(regime) 맥락 — 정량(MOLIT 거래량 IC)×정성(정책·금리·심리, agent_intel) 연결 ──
    #   발견(2026-06-04): 거래량(실유동성)·상대가치 신호는 정적이면 비정상이나 *국면 조건부*로 일관.
    #   거래량 within-구 ρ: 바닥+0.28·상승진입+0.35·과열+0.08(소멸). 예측기 아님(독립 사이클 2~3개=OOS 제한).
    rc = current_regime()
    if rc:
        H.append("<h3 id=s-regime>§0.3 사이클 국면 맥락 (정량 거래량 × 정성 정책·심리)</h3>")
        _er = regime_entry_read()
        if _er:
            _rc_color = {"고": "#C06A6A", "중": "#b45309", "저": "#5A8B74"}.get(_er["risk"], "#b45309")
            _rc_bg = "#fbeeee" if _er["risk"] == "고" else "#fff7ed" if _er["risk"] == "중" else "#EEF3EF"
            H.append(f"<div class='box' style='border-left:5px solid {_rc_color};background:{_rc_bg}'>"
                     f"<b>▶ 현 진입환경 종합</b>: {_er['phase']} + 전세지지 {'있음' if _er['supported'] else '약함'}"
                     f"(전세가율 {_er['jeonse_ratio']}% {_er['jeonse_trend']}) "
                     f"→ <b>{_er['read']}</b> · 진입리스크 <b>{_er['risk']}</b>. "
                     "<span class=sub>(phase=4축 분류기, 전세가율=value-지속가능성 modifier[직교 정보, phase-driver 아님].)</span></div>")
        H.append("<div class='box' style='border-left:5px solid #b45309;background:#fff7ed'>"
                 f"<b>현재 국면 = {rc.year} {rc.phase}</b> "
                 f"(정책 {rc.policy_stance} · 공급 {rc.supply} · 금리 {rc.rate} · 심리 {rc.sentiment}). "
                 f"<br><b>국면별 활성 신호</b>(MOLIT 거래량 within-구 IC, in-sample): {regime_conditional_guidance()}. "
                 "<br>★발견: 현 10축이 안 쓰는 <b>거래량(실유동성)</b>이 *바닥·상승진입* 국면엔 forward수익을 ρ≈+0.3로 예측(현 점수 0.08의 3~4배), "
                 "*과열* 국면엔 소멸. = 정적 점수가 왜 무력한지(국면 따라 신호가 뒤집혀 상쇄) 설명. "
                 "<b>지금 과열·재긴축 국면이면 거래량 변별력 낮고 저평가 단지 reversion 주의 — 진입 신중·하드필터 우선.</b> "
                 "<br><span class=sub>★예측기 아님: regime-gating은 in-sample 거래량 IC 0.18→0.26 개선(활성국면 집중)이나, "
                 "forward-split서 test구간(2017~) 활성국면 t0=0개(상승사이클 1개뿐) → <b>OOS 검증 불가</b>(effective N=1). 그래서 국면 맥락 + 활성신호 라벨로만(정직). "
                 "regime 선행 식별자=금리방향·정책스탠스·미분양(관측가능), 심리=후행 확인(agent_intel 2026-06-04, BOK 1차·정책 molit/korea.kr).</span>"
                 "<br><span class=sub>정책 전환점: " + " · ".join(f"{d} {v}" for d, v in list(KEY_POLICY.items())[-4:]) + "</span>")
        rev = regime_evidence()
        if rev.get("unsold_latest"):
            ul = rev["unsold_latest"]; up = rev["unsold_peak"]; ut = rev["unsold_trough"]; se = rev["sentiment_latest"]
            H.append(f"<br><span class=sub>★실데이터 근거(2026-06-04 수집, e-나라지표 MOLIT·KREMAP 국토연구원): "
                     f"전국 미분양 {ul[0]} <b>{ul[1]:,}호</b>(고점 {up[0]} {up[1]:,}·저점 {ut[0]} {ut[1]:,}) · "
                     f"서울 소비심리 {se[0]} <b>{se[1]}</b>(100=중립, &gt;100 확장). "
                     f"국면 분류기는 이 4축(가격모멘텀·금리·정책·미분양)을 정량 계산 → intel 타임라인 14/14 재현(in-sample).</span>")
            sc_status, sc_val = sentiment_confirms(rc.phase, rc.year)
            if sc_val is not None:
                H.append(f"<br><span class=sub>소비심리 후행 확인(KREMAP, 진입판별 아닌 과열/공포 *확인*): "
                         f"{rc.year} 연평균 {sc_val} → <b>{sc_status}</b>. (regime driver 아님 — leading=금리·정책·미분양, 심리=coincident 확인.)</span>")
            jv = jeonse_value_timing()
            if jv:
                H.append(f"<br><span class=sub>전세가율 value-timing(부동산원 R-ONE): 서울 {jv['year']} <b>{jv['ratio']}%</b> "
                         f"(역사범위 {jv['lo']}~{jv['hi']}%, 백분위 {jv['pct']:.0%}) → <b>{jv['signal']}</b>. "
                         f"★시장 *진입 타이밍* 선행지표(高전세가율→이후 시장↑, in-sample 강): 단 사이클 2~3개 한계. "
                         f"<b>구/단지 선택 신호 아님</b>(구 횡단면 ρ−0.25·단지-level ρ+0.09 약·비정상[MOLIT 전세∩매매 n=20,692] — 둘 다 선택기 부적합).</span>")
        H.append("</div>")
    if flagged:
        # 코드별 묶음 + 접기(스크롤 압박 완화, gemini 보고서리뷰 2026-06-03). 요약 1줄 + details.
        by_code: dict = {}
        for e in flagged:
            for fl in e.flags:
                by_code.setdefault(fl.code, []).append((_nm(e.candidate), fl.message, fl.penalty))
        _CODE_DESC = {"F_OVERBUDGET": "예산초과(자기자본 부족)", "F_NORENT": "전세 호가 미확보(임대 확인 필요)"}
        summ = " · ".join(f"<b>[{code}]</b> {_CODE_DESC.get(code, code)} {len(items)}건"
                          for code, items in sorted(by_code.items()))
        H.append("<details class='box crit'><summary><b>⚠️ Risk Flag</b> — " + summ
                 + " <span class=sub>(펼쳐서 단지별 보기)</span></summary><div class=dwrap>")
        for code, items in sorted(by_code.items()):
            H.append(f"<div class=note style='margin-top:6px'><b>[{code}]</b> {_CODE_DESC.get(code, code)}</div>")
            for nm, msg, pen in items:
                H.append(f"<span class=bad>·</span> {nm}: {msg} <span class=sub>(점수 ×{pen})</span><br>")
        H.append("</div></details>")

    # ══════ §0.9 결정 종합 (synthesis — 흩어진 5랭킹을 단일 추천+위계로 reconcile, 2026-06-07 적대감사 후속) ══════
    #   sage #1: §0 PRIOR(급지)·§A/§5(단지)·§6/§7(투영)이 서로 다른 "best"를 줄 수 있어 비조정 → 위계+단일추천 명시.
    if ev:
        _t0 = ev[0]
        _t0_trust = ts_by_name.get(_nm(_t0.candidate))
        _t0_band = _compset_band(compset, _t0.candidate)
        _t0_prior = district_prior(_t0.candidate, _struct_rank(_t0.candidate))
        H.append("<h2 id=s-synth>§0.9 결정 종합 — 단일 추천 + 위계</h2>")
        H.append("<div class='box' style='border-left:5px solid #4A6C8C;background:#E4EAF0'>"
                 f"<b>같은 급지 정밀검수(§5) 1순위 = {_nm(_t0.candidate)}</b> "
                 f"(조정점수<b>호가무관</b> {_t0.adjusted_fundamental:.3f}"
                 + (" · <span class=bad>예산/법규 제약⚠️</span>" if _hard_fail(_t0) or _t0.flags else " · 예산적합")
                 + f" · 급지 PRIOR {_t0_prior:.1f}"
                 + (f" · 신뢰도 {_t0_trust.score_pct:.0f}%" if _t0_trust else "")
                 + (f" · 생활권 base-rate +{_t0_band.cagr_median*100:.1f}%/년" if _t0_band else "") + "). "
                 "<b>★결정 위계</b>: ①§0 급지 PRIOR(어느 동네가 구조적으로 강한가) → ②하드필터(예산·법규) → "
                 "③촉매 watchlist(고용·교통·재건축 ≥2=강함) → ④§5 10축 정밀검수(같은 급지 내 tie-break). "
                 "<b>§6·§7 의 15년 투영은 과거 base-rate 외삽 *가정*이지 예측이 아니다</b>(점수↔실현 ρ=0.08). "
                 "→ 점수 1순위를 '정답'이 아니라 <b>'큰 실수 회피'</b>로 읽고, 고base-rate 급지 + 촉매 + 하드필터 통과로 정체만 피한다.</div>")

    # ══════ §A 한눈 종합표 (두괄식 — 모든 특징을 한 표에, 컬럼 헤더 클릭 → 세부 섹터) ══════
    # 사용자 요청(2026-06-03): 리포트 맨 앞에 10축별점·경사·후기·학군·추세·base-rate 등을 다컬럼으로 한눈에,
    # 각 컬럼 헤더 클릭 시 그 특징의 세부 섹터로 점프. 이후 섹터(§★·§5 등)는 세부.
    _SLG = [(3, "평탄"), (6, "완경사"), (10, "보통"), (15, "가파름"), (999, "급경사")]

    def _slope_grade(sp):
        if sp is None:
            return "—", ""
        for thr, lab in _SLG:
            if sp <= thr:
                return f"{sp:.0f}% {lab}", ("good" if lab in ("평탄", "완경사") else "bad" if lab in ("가파름", "급경사") else "")
        return f"{sp:.0f}%", ""

    H.append("<h2 id=s-master>§A 한눈 종합 — 후보 전 특징 한 표 (두괄식)</h2>")
    H.append("<div class='box' style='border-left:5px solid #4A6C8C;background:#EEF1F4'>"
             "<b>이 표 하나로 모든 후보의 핵심 특징을 비교한다</b> — 정렬은 <b>하드필터(예산·법규) 통과 게이트 → §5 10축 조정점수</b>. "
             "각 <b>컬럼 제목을 클릭</b>하면 그 특징의 <b>세부 섹터로 이동</b>한다(근거·방법·전체 표). "
             "<b>정직 각주</b>: 조정점수는 '같은 급지 내 적합도·검수표'이지 미래수익 예측이 아니다(definitive 백테스트 n=84 ρ=0.08, ρ≥0.30 배제·상위−하위 가치상승차 ≈0) — 의사결정은 <b>§0 PRIOR+촉매</b>가 1차, 본 표는 같은 급지 정밀 검수.</div>")
    H.append("<div class=click-hint>↳ 파란 밑줄 컬럼 제목 = 클릭하면 세부 섹터로 이동 · "
             "<b>10축 프로파일</b> 막대 순서 = " + "·".join(AXES) + " (높을수록 막대 김, 보라=4점+)</div>")
    H.append("<table class=master>")
    H.append(_row(["#", "단지([구])",
                   _thl("10축 점수", "s5"), _thl("10축 프로파일", "s5"),
                   _thl("가격메리트", "s5"), _thl("경사", "s-signals"),
                   _thl("후기", "s16"), _thl("학군", "s-star"),
                   _thl("실거래추세", "s1"), _thl("생활권 base-rate", "s-signals"),
                   _thl("전세가율", "s-signals"), _thl("예산", "s4")], "th"))
    for i, e in enumerate(ev):
        c = e.candidate
        sc = e.axis.scores
        slope_txt, slope_cls = _slope_grade(getattr(c, "slope_pct", None))
        slope_cell = f"<span class={slope_cls}>{slope_txt}</span>" if slope_cls else slope_txt
        # 후기 정량 — n + 주의테마 수 (정성은 §1.6)
        rv = resolve_named(reviews, c)
        rn = rv.get("n_seen", 0) if rv else 0
        if rv and rn >= 3:
            ncau = len(rv.get("themes_caution", []))
            rv_cell = f"<b>{rn}</b>건 <span class=sub>주의{ncau}</span>"
        elif rn:
            rv_cell = f"<span class=sub>{rn}건(부족)</span>"
        else:
            rv_cell = "<span class=sub>—</span>"
        # 학군 — §5 학군 축 점수와 동일 소스(axis score)로 통일 (2026-06-05 §A↔§5 불일치 수정).
        # 기존 location.school_grade(희소·키 [구]단지명 한정)는 §5(hakgun_score+academy_exam 158/158)와
        # 소스가 달라 §A 만 '미수집'으로 비던 버그. academy_exam 100% 라 axis score 는 전 단지 산출됨.
        hak_score = sc.get("학군")
        if c.listing.area_exclusive_m2 < 59:   # 전용<59=소형, 가족 비대상 → 학군 0 (사용자 OVERRIDE)
            hak_cell = "<span class=bad>0</span> <span class=sub>(전용&lt;59 소형)</span>"
        elif hak_score is not None:
            hcls = "good" if hak_score >= 3.5 else ("bad" if hak_score < 2.5 else "")
            hak_cell = f"<span class={hcls}>{hak_score:.1f}</span>" if hcls else f"{hak_score:.1f}"
        else:
            hak_cell = "<span class=sub>미수집</span>"
        strength = e.trend.strength if e.trend else "—"
        b = _compset_band(compset, c)
        br_cell = (f"+{b.cagr_median*100:.1f}%" if b else "—")
        # 전세가율 — per-complex(전세 호가 기반) 우선, 없으면 구 단위 R-ONE 평균으로 폴백(라벨 '구').
        # MOLIT 전세캐시는 2021까지라 현재값 부적합 → 구 평균(gu_jeonse_ratio)이 가용한 정직 근사.
        jr = (c.jeonse_krw / c.listing.price_krw) if c.jeonse_krw else None
        if jr is not None:
            jr_cell = f"{jr*100:.0f}%"
        elif getattr(c, "gu_jeonse_ratio", None):
            jr_cell = f"~{c.gu_jeonse_ratio*100:.0f}%<span class=sub>구</span>"
        else:
            jr_cell = "—"
        budget = ("<span class=good>OK</span>" if e.finance.equity_ok and not _hard_fail(e)
                  else "<span class=bad>초과⚠️</span>")
        # ★랭킹 단일화(2026-06-06): §A 정렬이 adjusted_fundamental(호가무관)이므로 표시 점수도 그것으로 통일(이전 adjusted_total 혼용=#1 모순).
        adj = (f"<span class=hl>{e.adjusted_fundamental:.3f}</span>" if i == 0 and not e.flags
               else f"<b>{e.adjusted_fundamental:.3f}</b>" if not e.flags
               else f"<span class=bad>{e.adjusted_fundamental:.3f}⚠️</span>")
        merit_cell = f"{sc.get('가격메리트', 0):.1f}"
        rk = "🥇" if i == 0 else str(i + 1)
        cells = [rk, ("<b>" + _nm(c) + "</b>" if i == 0 else _nm(c)), adj,
                 _micro10(sc, AXES), merit_cell, slope_cell, rv_cell, hak_cell,
                 strength, br_cell, jr_cell, budget]
        H.append(("<tr class=win>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>") if i == 0
                 else _row(cells))
    H.append("</table>")
    H.append("<div class=src>[사실/추론] <b>10축 점수·프로파일</b>=§5 가중합×Risk(주 프레임) · <b>가격메리트</b>=펀더멘털(크기·중심거리·토지지분) 대비 저평가 헤도닉 잔차 · "
             "<b>경사</b>=opentopodata 실측 · <b>후기</b>=exa→zippoom n(정성 §1.6) · <b>학군</b>=§5 자체 학군 알고리즘(<b>학업성취도 보통학력↑ 0.35</b> + <b>특목고 진학률 0.30</b>[배정중학교, 아실·원자료 학교알리미 5월공시] + 입시학원800m 0.20[카카오] + 구 입시학원밀도 0.15[서울OpenAPI, 양천1421·종로96], 가용가중 재정규화) — 158단지 중학교매칭 108·구평균 50. 학교알리미 OpenAPI 진학률은 무변별(전부99%)이라 미사용·아실 5월공시 surfacing 사용 · "
             "<b>추세</b>=MOLIT 실거래 · <b>base-rate</b>=생활권 실현 CAGR median(거시·점예측 아님) · <b>전세가율</b>=per-complex 전세 호가 우선, 없으면 구 R-ONE 평균(<b>~%구</b> 표기·근사) · <b>예산</b>=자기자본≥필요(§4). "
             "컬럼 제목 클릭=세부 섹터. 단지명 <b>[구]</b> 표기로 동명단지 구분. "
             "<b>⚠️ 가격메리트 한계</b>: 헤도닉 설명력 R²≈0.5(중심거리·크기 2변수)이며 <b>토지지분은 현재 전부 추정 placeholder(11평)라 모델에서 자동 제외</b> — 등기부 실측 주입 시 활성. "
             "<b>⚠️ 학군</b>=입시학원 밀집·명문중 근접 proxy([추론], 학업성취 측정 아님). <b>점수=수익예측기 아님</b>(백테스트 ρ²1.2%).</div>")

    # ══════ §★ 보조 FACT 신호 (거시 가이드) — 주 프레임(§5 10축)을 보정 ══════
    # 10축 점수가 주 비교 프레임이고, 이 FACT 신호들은 그 위에 얹는 거시 보정이다.
    # ①생활권 base-rate(between-생활권 장기 CAGR=거시 가이드) ②within-구 mean-reversion(타이밍)
    # ③적합도 facts + 하드필터. 점수↔CAGR 약상관(ρ²1.2%)이라 이 신호와 *함께* 읽는다(점수 폐기 아님).
    H.append("<h2 id=s-signals>§★ 보조 FACT 신호 — 거시 가이드 (점수와 함께 읽기)</h2>")
    H.append("<div class='box' style='border-left:5px solid #5A8B74;background:#eafaf0'>"
             "<b>왜 이 신호를 점수와 함께 보나</b> — §5 10축 점수는 '같은 급지 내 적합도'엔 강하나 '어느 동네가 더 오를까'(거시)엔 약하다(백테스트 ρ²1.2%). "
             "그 빈틈을 FACT 신호가 메운다: <b>① 생활권 base-rate</b>(어느 동네가 장기 더 올랐나=between-생활권, <b>거시 주신호</b>) · "
             "<b>② mean-reversion</b>(추세 대비 저평가=반등여지 <b>타이밍</b>, 구조 품질 아님 — <b>점수 아닌 이 레이어에만</b>; "
             "검증 validate_reversion: 구 장기추세 대비 저평가는 시계열로 forward와 ρ=+0.68이나 <b>횡단면 '구 선택'은 regime-불안정</b>, "
             "그래서 ②는 '지금 그 동네가 싼 시점인가' 타이밍이지 '어느 동네가 더 오르나' 선택 아님. ★상승여력 축에서 gu_cagr 미래승수 제거(2026-06-04, 거시 이중계상·비정상 외삽)) · "
             "<b>③ 적합도 facts + 하드필터</b>(예산·법규·현금흐름·유동성=결정론 사실). "
             "<b>주의</b>: 어느 신호도 '이 단지가 X% 오른다' 점예측은 아니다 — 거시 방향과 타이밍의 보정일 뿐.</div>")

    # ① 생활권 base-rate 밴드 (주신호) — 생활권×전용band 중복 제거
    seen_bands: dict = {}
    for e in ev:
        b = _compset_band(compset, e.candidate)
        if b:
            seen_bands.setdefault((b.saenghwalgwon, b.area_band), b)
    H.append("<h2 style='font-size:15px;border:0;margin:18px 0 4px'>① 생활권 base-rate 밴드 (주신호)</h2>")
    if seen_bands:
        H.append("<table>" + _row(["생활권", "전용", "과거 실현 CAGR 밴드 (p25·median·p75)", "표본 n"], "th"))
        for (sg, ab), b in seen_bands.items():
            H.append(_row([sg, f"전용{ab}",
                           f"+{b.cagr_p25*100:.1f}% · <b>+{b.cagr_median*100:.1f}%</b> · +{b.cagr_p75*100:.1f}%/년",
                           f"{b.n}"]))
        H.append("</table><div class=src>[사실] MOLIT 동일단지 실현 CAGR(과거)·"
                 + ((compset or {}).get("_meta", {}).get("baseline", "장기"))
                 + ". <b>평형별(전용band) 분리 산출</b>(2026-06-02 — 84 고집 배제, 각 매물은 그 전용band 의 생활권 CAGR). "
                 "<b>과거사실이며 미래보장 아님</b>. between-생활권 격차가 가장 큰 레버(구로>수성 등). 단지별 점예측 아님 — 밴드는 생활권×전용band 단위.</div>")
    else:
        H.append("<div class=box>비교셋 base-rate 미주입 또는 표본부족(n&lt;8) — "
                 "<b>필터only</b> 모드(편향-분산 가드). <code>gen-baserate</code> 로 생활권 CAGR 표 주입 시 활성.</div>")

    # ② within-구 mean-reversion (역발상 타이밍) — 주입된 후보만
    mr_rows = [(e, _compset_mr(compset, e.candidate)) for e in ev]
    mr_rows = [(e, s) for e, s in mr_rows if s]
    if mr_rows:
        H.append("<h2 style='font-size:15px;border:0;margin:18px 0 4px'>② within-생활권 peer-reversion (단지 단위 타이밍) "
                 "<span style='font-size:11px;background:#f3e0de;color:#9a2a1f;padding:1px 7px;border-radius:4px;font-weight:700'>검증 약함 · 횡단면 regime-불안정</span></h2>")
        H.append("<table>" + _row(["단지", "생활권 평균대비 최근 CAGR", "백분위", "신호"], "th"))
        for e, s in mr_rows:
            lc = "good" if "저평가" in s.label else ("bad" if "고평가" in s.label else "")
            lab = f"<span class={lc}>{s.label}</span>" if lc else s.label
            H.append(_row([e.candidate.listing.complex_name, f"{s.rel_recent_cagr*100:+.1f}%/년",
                           f"{s.percentile*100:.0f}%", lab]))
        H.append("</table><div class=src>[추론] within-생활권 상대 최근 CAGR 백분위(<b>단지 단위</b> — §★②b 구 단위가 못 잡는 단지간 차이를 보완). "
                 "<b>저평가=최근 덜 올라 반등여지(역발상)</b>·고평가=과열주의. "
                 "<b>⚠️ 검증 등급 약함</b>: 자체 검증(validate_reversion)에서 <b>횡단면(누가 더 오르나) reversion은 regime-불안정</b>(t0별 −0.46~+0.53 부호 뒤집힘)으로 나옴 "
                 "— 그래서 <b>§★②b(시계열·검증 ρ+0.68)를 1차 타이밍으로 보고, 이 ②는 같은 생활권 내 단지 tiebreaker 보조로만</b>. 구조적 품질 아님·장기보유엔 상쇄로 약함.</div>")

    # ②b 시계열 mean-reversion — 구 장기추세 대비 저평가(검증 ρ+0.68 시계열·N≈2.5). gen-trend-gap 주입.
    tg = (compset or {}).get("trend_gap") if compset else None
    if tg:
        def _gu_short(c):
            sg = getattr(c, "saenghwalgwon", None)
            if sg and sg in tg:
                return sg
            parts = (getattr(c, "district", "") or "").split()
            g = parts[-1] if parts else ""
            return g[:-1] if g.endswith("구") else g
        seen_gu: dict = {}
        for e in ev:
            g = _gu_short(e.candidate)
            if g in tg and g not in seen_gu:
                seen_gu[g] = tg[g]
        if seen_gu:
            H.append("<h2 style='font-size:15px;border:0;margin:18px 0 4px'>②b 구 장기추세 대비 저평가 (시계열 타이밍) "
                     "<span style='font-size:11px;background:#e3f0e8;color:#1d6b3f;padding:1px 7px;border-radius:4px;font-weight:700'>검증됨 · 시계열 ρ+0.68 (1차 타이밍)</span></h2>")
            H.append("<table>" + _row(["구", "추세대비 gap", "z", "신호"], "th"))
            for g, v in seen_gu.items():
                lc = "good" if "저평가" in v["label"] else ("bad" if "고평가" in v["label"] else "")
                lab = f"<span class={lc}>{v['label']}</span>" if lc else v["label"]
                H.append(_row([g, f"{v['gap_pct']:+.1f}%", f"{v['z']:+.2f}", lab]))
            H.append("</table><div class=src>[추론] 구 84밴드 연median의 2010~ 로그선형 추세 대비 현재 잔차. "
                     "<b>양수=추세 아래=저평가(반등여지)</b>. ★검증(validate_reversion.py): 추세대비 저평가→forward 시계열 <b>ρ=+0.68</b>, "
                     "단 <b>횡단면 '구 선택'은 regime-불안정</b> → '지금 그 구가 싼 시점인가' 타이밍이지 '어느 구가 더 오르나' 선택 아님. "
                     "독립 사이클 N≈2.5 저검정력. <b>점수 미반영</b> — 2026-06-04 상승여력서 gu_cagr 모멘텀 승수 제거 후 검증된 reversion을 이 타이밍 레이어로 분리.</div>")

    # ③ 적합도 facts + 하드필터 (점수 아님, 결정론)
    H.append("<h2 style='font-size:15px;border:0;margin:18px 0 4px'>③ 적합도 facts + 하드필터 (점수 아님)</h2>")
    H.append("<table>" + _row(["단지", "예산적합", "법적실행", "유동성(세대)", "전세가율", "경사"], "th"))
    for e in ev:
        jr = (e.candidate.jeonse_krw / e.candidate.listing.price_krw) if e.candidate.jeonse_krw else None
        own_cap = profile.get("own_capital_krw")
        if own_cap is None:   # 테스트 등 profile 축약 시 finance.equity_ok 와 일치시킴
            own_cap = e.finance.equity_required_krw if e.finance.equity_ok else e.finance.equity_required_krw - 1
        ff = fitness_facts(equity_required_krw=e.finance.equity_required_krw, own_capital_krw=int(own_cap),
                           units=e.candidate.units, jeonse_ratio=jr, slope_pct=e.candidate.slope_pct,
                           legal_status=_legal_status(e))
        budget = ("<span class=good>OK</span>" if ff["예산적합"] else "<span class=bad>초과</span>")
        lcls = "good" if ff["법적실행"] == "PASS" else ("bad" if ff["법적실행"].startswith("FAIL") else "")
        legal = f"<span class={lcls}>{ff['법적실행']}</span>" if lcls else ff["법적실행"]
        jr_cell = f"{ff['전세가율']*100:.0f}%" if ff["전세가율"] is not None else "—"
        sl_cell = (f"{ff['경사_pct']}% {ff['경사_등급']}" if ff["경사_등급"] else "—")
        H.append(_row([_nm(e.candidate), budget, legal,
                       f"{ff['유동성_세대']:,} ({ff['유동성_등급']})", jr_cell, sl_cell]))
    H.append("</table><div class=src>[사실] 전부 결정론 — 예산적합=자기자본≥필요자본(§4) · 법적실행=토허·이주단계 하드게이트 · "
             "유동성=세대수(전이가능 유일 factor, 양8/음0) · 전세가율=use-value anchor(임대 leg) · 경사=opentopodata 실측. "
             "<b>매력도 점수 아님</b> — 사실 그대로.</div>")

    # ── §★ 검증신호 별점 비교 (한눈에 비교용 — 예측점수 아님, 검증된 사실/신호의 상대 별점) ──
    _own = int(profile.get("own_capital_krw") or 0)
    _GR = {"상": 5.0, "중상": 4.0, "중": 3.0, "중하": 2.0, "하": 1.0}
    _SL = {"평탄": 5.0, "완경사": 3.5, "보통": 3.0, "가파름": 1.5, "급경사": 1.0}

    def _stars(e):
        l = e.candidate.listing
        b = _compset_band(compset, e.candidate)
        # base-rate: +5%/년→0★ +10%→5★ (서울 직주권 스케일)
        base = min(5.0, max(0.0, ((b.cagr_median * 100) - 5.0))) if b else 2.5
        jr = (e.candidate.jeonse_krw / l.price_krw) if e.candidate.jeonse_krw else None
        jeonse = min(5.0, jr / 0.85 * 5) if jr else 0.0          # 전세가율 85%→5★
        units = min(5.0, e.candidate.units / 2400 * 5)           # 2400세대→5★
        lf = resolve_named(location, e.candidate) or {}
        sch = _GR.get(lf.get("school_grade"), 0.0)
        slope = _SL.get(lf.get("slope_grade"), 2.5)
        budget = min(5.0, max(0.0, (_own - e.finance.equity_required_krw) / max(_own, 1) * 5)) if _own else 2.5
        return {"생활권상승": base, "임대(전세가율)": jeonse, "유동성(세대)": units,
                "학군": sch, "경사(평탄↑)": slope, "예산여유": budget}

    STAR_AXES = ["생활권상승", "임대(전세가율)", "유동성(세대)", "학군", "경사(평탄↑)", "예산여유"]
    H.append("<h2 id=s-star>§★ 보조신호 별점 비교 (한눈에)</h2>")
    H.append("<div class=note>각 별점은 <b>보조 FACT 신호의 상대 비교</b>(생활권 base-rate·전세가율·세대수·학군등급·경사·예산여유)로, "
             "<b>주 프레임인 §5 10축 가중점수를 거시 관점에서 보완</b>한다. 미래 수익 신탁이 아니라 '관찰된 조건'의 시각 비교.</div>")
    H.append("<table>" + _row(["단지/평형"] + STAR_AXES, "th"))
    for i, e in enumerate(ev):
        s = _stars(e)
        cells = [("🥇 " if i == 0 else "") + _unit(e.candidate)] + [_bar(s[a]) for a in STAR_AXES]
        H.append("<tr class=win>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>" if i == 0 else _row(cells))
    H.append("</table><div class=src>[사실/추론] 별점 기준: 생활권상승=base-rate +5%→0★·+10%→5★ / 임대=전세가율 85%→5★ / "
             "유동성=세대 2400→5★ / 학군=최근접중 등급(상5~하1) / 경사=평탄5·가파름1.5 / 예산여유=(자본−필요자기자본)/자본. "
             "정렬은 §5 10축 조정점수 기준(하드필터 통과 게이트 후).</div>")

    # ── BLUF (10축 종합점수 1순위 — 하드필터 통과 게이트 후). 뒤집는 조건 동반 ──
    if ev:
        top = ev[0]
        tl = top.candidate.listing
        tf = top.finance
        tt = ts_by_name.get(_nm(top.candidate))   # [구]name 키(동명단지 충돌 방지, 2026-06-07)
        top_band = _compset_band(compset, top.candidate)
        H.append("<div class=bluf>")
        H.append(f"<div class=note style='margin:0 0 3px'>§5 10축 가중점수 종합 1순위 ({strategy.value}) "
                 "— 하드필터(예산·법규) 통과 게이트 후 조정점수 desc. '뒤집는 조건'과 함께 읽을 것</div>")
        H.append(f"<span class=rank style='font-size:18px'>🥇 {_nm(top.candidate)}</span> "
                 f"<span class=sub>{tl.dong_ho}·전용{tl.area_exclusive_m2:.0f}㎡({tl.pyeong}평)·{tl.floor}·{tl.facing} · {_eok(tl.price_krw)}</span>"
                 f"<span class=sub> — {top.candidate.transit}</span>")
        cards = [
            _card("조정점수(호가무관)", f"{top.adjusted_fundamental:.3f}", "5점만점 호가무관×Risk"),
            _card("대출 가능(min)", _eok(tf.loan_krw), tf.loan_binding),
            _card("자기자본 필요", _eok(tf.equity_required_krw),
                  "<span class=good>여유 OK</span>" if tf.equity_ok else "<span class=bad>부족</span>"),
            (_card("생활권 base-rate", f"+{top_band.cagr_median*100:.1f}%/년",
                   f"{top_band.saenghwalgwon} median · 과거사실")
             if top_band else _card("생활권 base-rate", "—", "비교셋 미주입·필터only")),
        ]
        if tt:
            tg = "good" if tt.score_pct >= 85 else ("" if tt.score_pct >= 60 else "bad")
            cards.append(_card("결정신뢰도", f"<span class={tg}>{tt.score_pct:.0f}%</span>" if tg else f"{tt.score_pct:.0f}%",
                               f"{tt.grade} · 입력 검증 완성도"))
        H.append("<div class=kpi>" + "".join(cards) + "</div>")
        # 뒤집는 조건 = 10축 조정점수 근소차(가중치 민감, 별지 A 민감도) + base-rate 거시역전 + 하드필터 입력 변동.
        runners = [e for e in ev[1:] if not _hard_fail(e)
                   and abs(e.adjusted_fundamental - top.adjusted_fundamental) < 0.15]
        close = (" · 조정점수 근소 경합: "
                 + ", ".join(f"{_nm(e.candidate)}({e.adjusted_fundamental:.3f})" for e in runners)) if runners else ""
        if top_band:
            base_note = (f"1순위 근거 = 조정점수(호가무관) <b>{top.adjusted_fundamental:.3f}</b> · 생활권 base-rate 거시 보정 "
                         f"+{top_band.cagr_median*100:.1f}%/년({top_band.saenghwalgwon})")
        else:
            base_note = f"1순위 근거 = 조정점수(호가무관) <b>{top.adjusted_fundamental:.3f}</b>(생활권 base-rate 미주입 — 거시 보정 없음)"
        # 1순위의 약점(낮은 축) 트레이드오프 명시 — 장점만 보여주지 않기(gemini 리뷰 2026-06-03)
        weak = sorted(((a, top.axis.scores[a]) for a in AXES if top.axis.scores[a] <= 2.6),
                      key=lambda x: x[1])[:3]
        weak_note = ("<br><b>↳ 1순위 약점(트레이드오프)</b>: "
                     + ", ".join(f"{a} {v:.1f}/5" for a, v in weak)
                     + " — 강점이 이를 상쇄하나 매수 전 직접 확인.") if weak else ""
        # ★별지 A 판정 BLUF 승격(3차 감사 UX#3·19): 비robust·신뢰도·추세부재 같은 결정적 반대 정보가
        #   5천 줄 아래 접힌 별지에만 있으면 헤드라인 🥇 가 확정 판정처럼 읽힌다 — 1줄로 끌어올림.
        _sens_rows = [r for r in (sensitivity or []) if "가중치" in r.label]
        _flips = [r for r in _sens_rows if r.flipped]
        if any("검정 무의미" in r.label for r in (sensitivity or [])):
            sens_note = " · <b>민감도: 검정 무의미(유효 경쟁자 없음 — robust 아님)</b>"
        elif _flips:
            sens_note = (f" · <span class=bad><b>민감도: 비robust(가중치 교란 {len(_flips)}/{len(_sens_rows)}건 1위 역전"
                         f" — 별지 A)</b></span>")
        elif _sens_rows:
            sens_note = f" · 민감도: 가중치 ±30% {len(_sens_rows)}건 모두 1위 유지(별지 A)"
        else:
            sens_note = ""
        trend_note = ("" if top.trend is not None
                      else " · <span class=bad>실거래 추세 데이터 없음(§6 보수밴드)</span>")
        H.append(f"<div class=note><b>이 우선순위를 뒤집는 조건</b> — {base_note}{close}{sens_note}{trend_note}. "
                 "근소차는 동급(가중치 ±30% 민감도=별지 A). 점수는 같은 급지 내 적합도 비교이며 "
                 "<b>미래 CAGR 점예측이 아니다</b>(§6 밴드·base-rate는 생활권 단위). 매수 전 등기부·DSR·전세 실호가 독립 확인 필수."
                 + weak_note + "</div>")
        H.append("</div>")

    # ── 목차 (앵커) — 조건부 섹션(§1.5 location·§1.6 reviews·§1.7 structural)은 주입 시에만 링크(dead anchor 방지) ──
    _opt = ("<a href='#s15'>§1.5 입지정량</a>" if location else "") \
        + ("<a href='#s16'>§1.6 후기</a>" if reviews else "") \
        + ("<a href='#s17'>§1.7 구조동력</a>" if structural else "")
    H.append("<div class=toc>"
             "<a href='#s-master'>§A 한눈 종합</a><a href='#s5'>§5 10축 점수(주)</a><a href='#s-signals'>§★ 보조신호</a><a href='#s-star'>별점비교</a><a href='#s-compare'>비교</a><a href='#s1'>§1 추세</a>" + _opt + "<a href='#s3'>§3 호가</a>"
             "<a href='#s4'>§4 자본</a><a href='#s52'>§5.2 재개발vs준공</a>"
             "<a href='#s6'>§6 시나리오</a><a href='#s7'>§7 A′vsB 순자산</a><a href='#annex'>별지·참조</a></div>")

    # ── 후보 비교 요약 매트릭스 (NEW) — 흩어진 결정론 값 단일 테이블 재배치 (새 계산 0) ──
    H.append("<h2 id=s-compare>후보 비교 (정렬: 하드필터 통과 게이트 → 10축 조정점수)</h2><table>")
    H.append(_row(["단지/평형", "조정점수(호가무관)", "호가", "대출(min)", "자기자본필요", "취득세", "예산적합", "생활권 base-rate", "추세강도", "신뢰도", "재건축단계"], "th"))
    for i, e in enumerate(ev):
        l = e.candidate.listing
        tt = ts_by_name.get(_nm(e.candidate))   # [구]name 키(동명단지 충돌 방지)
        trust_cell = f"{tt.score_pct:.0f}%" if tt else "—"
        strength = e.trend.strength if e.trend else "—"
        budget = ("<span class=good>OK</span>" if e.finance.equity_ok and not _hard_fail(e)
                  else "<span class=bad>초과/제약⚠️</span>")
        b = _compset_band(compset, e.candidate)
        br_cell = (f"+{b.cagr_median*100:.1f}% <span class=sub>{b.saenghwalgwon}·전용{b.area_band}</span>" if b else "—")
        # ★랭킹 단일화 누락분 수정(2026-06-07 적대감사): 이 표는 adjusted_fundamental 로 정렬(ev)되는데 adjusted_total 을
        #   표시해 🥇(fund 최고)가 #2보다 낮은 점수로 보이는 모순이 있었다(§A·§5·§0.6 은 이미 통일, 이 표만 누락). → fundamental 로 통일.
        adj_cell = (f"<b>{e.adjusted_fundamental:.3f}</b>" if not e.flags else f"<span class=bad><b>{e.adjusted_fundamental:.3f}</b>⚠️</span>")
        name = ("🥇 " if i == 0 else f"{i+1}. ") + _unit(e.candidate)
        cells = [name, adj_cell, _eok(l.price_krw), _eok(e.finance.loan_krw), _eok(e.finance.equity_required_krw),
                 _eok(e.finance.acquisition_tax_krw), budget, br_cell, strength, trust_cell, e.redev.stage_label]
        H.append(("<tr class=win>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>") if i == 0 else _row(cells))
    H.append("</table><div class=src>[사실] 호가=NAVER_LIVE / 대출·자본·세금=AGENT_CALC 결정론 / 예산적합=자기자본≥필요(§4)·법규 하드필터 / "
             "생활권 base-rate=MOLIT 동일단지 실현 CAGR median(생활권 단위 거시 보조·점예측 아님) / 신뢰도=입력 검증(별지 A) / 추세=MOLIT 실거래. "
             "<b>🥇=하드필터 통과 게이트 후 조정점수(호가무관) 1순위.</b> 조정점수=fundamental(가격메리트·전세수요 제외)×Risk Flag 페널티(상세=§5). 단지별 미래 CAGR은 본 표에 없음(§6 밴드).</div>")

    # ── §0 프로필 (축소: 1줄) ──
    H.append("<div class=note style='margin-top:18px'><b>모드</b> "
             + ("장기보유 전세수익·재건축·보유세 활성(양도세 비활성)" if strategy is ExitStrategy.HOLD_AND_RENT
                else "매도 손익분기·양도세 활성" if strategy is ExitStrategy.LIVE_THEN_SELL else "실거주 품질 위주")
             + " · " + " · ".join(f"{k}={v}" for k, v in profile.items()) + "</div>")

    # ── §1 후보 기본정보 + 실거래 추세 ──
    H.append("<h2 id=s1>§1. 후보 기본정보 + 실거래 추세</h2><table>")
    H.append(_row(["단지", "세대·준공·용적", "입지", "실거래 추세(MOLIT)"], "th"))
    for e in ev:
        c = e.candidate
        tr = f"[사실] {e.trend.note}" if e.trend else "<span class=sub>실거래 시계열 미보유 (update-prices 로 적재)</span>"
        if e.trend and c.listing.price_krw > e.trend.last_price_krw * 1.15:
            gap = (c.listing.price_krw / e.trend.last_price_krw - 1) * 100
            tr += f" <span class=bad>· 호가가 최근 실거래 대비 +{gap:.0f}% (괴리 주의)</span>"
        H.append(_row([_nm(c), f"{c.units}세대·{c.built_year}·{c.far_pct:.0f}%",
                       c.transit, tr]))
    H.append("</table>")

    # ── §1.5 입지 정량 (역·학교 거리·경사도) — Kakao Local + SRTM30m (사용자 요청 2026-05-30) ──
    if location:
        H.append("<h2 id=s15>§1.5 입지 정량 — 출퇴근·학군·경사도</h2><table>")
        H.append(_row(["단지", "업무지구(출퇴근)", "최근접 학교(배정 아님)", "학군 신호", "경사도(근사)"], "th"))
        for e in ev:
            lf = resolve_named(location, e.candidate)
            if not lf:
                H.append(_row([_nm(e.candidate),
                               "<span class=sub>위치 데이터 없음</span>", "—", "—", "—"]))
                continue
            sg = lf.get("slope_grade", "")
            scls = "bad" if sg == "가파름" else ("" if sg == "완경사" else "good")
            slope = f"{lf.get('slope_pct')}% {sg}"
            _elev = lf.get("center_elev_m")
            slope_cell = (f"<span class={scls}>{slope}</span>" if scls else slope) + \
                         (f" <span class=sub>고도 {_elev}m</span>" if _elev is not None else "")
            km = e.candidate.cbd_km
            cbd_cell = (f"{e.candidate.cbd_name} <b>{km}km</b>" if km is not None else "—")
            sch_cell = (f"{lf.get('school_elem', '-')}<br>{lf.get('school_mid', '-')}<br>{lf.get('school_high', '-')}")
            # 학군 신호: 명문중·학원가 근접(대구) 또는 입시학원수
            em = lf.get("elite_mid_m")
            acx = lf.get("academy_exam_800m")
            ac = lf.get("academy_count_500m")
            if em is not None:
                hak = (f"명문중 <b>{em}m</b> · 학원가(범어네거리) <b>{lf.get('academy_st_m')}m</b>"
                       f"<br><span class=sub>입시학원 {acx}·전체 {ac}</span>")
            elif acx is not None:
                hak = f"입시학원 <b>{acx}</b> <span class=sub>/전체 {ac}</span>"
            else:
                hak = "—"
            # ★최근접 중학교 등급(학교알리미 학업성취/진학률 기반 [추론], agent-intel 수집 2026-06-02)
            sg_grade = lf.get("school_grade")
            if sg_grade:
                gcls = "good" if sg_grade in ("상", "중상") else ("bad" if sg_grade in ("중하", "하") else "")
                gtag = f"<span class={gcls}><b>{sg_grade}</b></span>" if gcls else f"<b>{sg_grade}</b>"
                hak = (f"최근접중 {gtag} <span class=sub>{lf.get('school_grade_basis', '')}</span><br>" + hak)
            H.append(_row([_nm(e.candidate), cbd_cell, sch_cell, hak, slope_cell]))
        H.append("</table><div class=src>[사실] 출퇴근=주요 업무지구 직선거리(서울 강남/시청/여의도·대구 동대구/반월당 최단). "
                 "역·학교·학원=KAKAO_LOCAL. <b>★학군 정정(2026-05-30)</b>: '최근접 학교'는 <b>배정학교가 아니며 명문 여부와 무관</b>"
                 "(예: 만촌삼정에듀파크 최근접 고는 영남공고). 대구 학군은 <b>수성구 명문중(정화·동도·경신)·범어네거리 학원가 근접거리</b>가 진짜 지표 — "
                 "범어롯데캐슬·e편한세상범어가 학원가 60~180m로 핵심, 수성롯데캐슬·메트로팔레스는 1.2km+로 외곽. "
                 "[추론] 학원수≠학업성취(quality 단정 아님). 경사도=opentopodata SRTM30m 근사. "
                 "<b>경사·학원수는 §5 점수 미반영(표시전용). 출퇴근·학군은 §5 축으로 반영</b> — "
                 "단 학군 점수는 명문중·학원가 근접+intel 기반 <b>[추론] proxy(학업성취 측정 아님)</b>, "
                 "PRIMARY_ONLY(대구 실거주) 0.15·HOLD_AND_RENT(서울 임대) 0.05 가중(사용자 (b) 선택).</div>")

    # ── §1.6 커뮤니티 후기 정성 분석 (호갱노노) — 점수 미산출(RDU-021·council 권고) ──
    if reviews:
        meta = reviews.get("_meta", {})
        H.append("<h2 id=s16>§1.6 커뮤니티 후기 — 정성 테마(점수 아님)</h2>")
        H.append("<div class='box crit'><b>⚠️ 후기 해석 주의</b> — " + meta.get("bias_warning", "")
                 + " <b>단일 점수로 환산하지 않는다</b>(주관·편향·조작·소표본을 점수가 은폐하므로). "
                 "아래는 인용 수준의 정성 보조이며 §5 순위에 미반영.</div>")
        H.append("<table>" + _row(["단지", "관측 n", "긍정 테마", "주의 테마", "신뢰도(편향 보정)", "특기"], "th"))
        for e in ev:
            rv = resolve_named(reviews, e.candidate)
            n = rv.get("n_seen", 0) if rv else 0
            if not rv or n == 0:
                H.append(_row([_nm(e.candidate), "0",
                               "<span class=sub>표본 미수집</span>", "—", "—", "—"]))
                continue
            cau = "<br>".join("· " + t for t in rv.get("themes_caution", [])) or "—"
            if n < 3:
                pos = "<span class=sub>표본 부족(n&lt;3, 통계 무의미)</span>"
            else:
                pos = "<br>".join("· " + t for t in rv.get("themes_pos", [])) or "—"
            rel = _review_reliability(rv)
            relcell = (f"<span class=bad>⚠ {rel}</span>" if ("약함" in rel or "편향" in rel or "미수집" in rel)
                       else f"<span class=sub>{rel}</span>")
            H.append(_row([_nm(e.candidate), f"{n}건", pos,
                           (f"<span class=bad>{cau}</span>" if cau != "—" else "—"),
                           relcell, f"<span class=sub>{rv.get('notable', '')}</span>"]))
        H.append("</table><div class=src>[추론] 출처=호갱노노 단지 후기 DOM(확인 " + meta.get("confirmed_date", "")
                 + "). Claude aspect 분류(오프라인 1회→고정 JSON, G3 결정론). 테마는 관측 스니펫 기반으로 "
                 "<b>n 작아 통계 아님</b>(정성 인용). <b>점수·순위 미반영</b>.</div>")

    # ── §1.7 구조적 동력 (공급·호재·인구) — 회귀 추세 보완, coarse ordinal [추론] (사용자 요청 2026-05-31) ──
    if structural:
        smeta = structural.get("_meta", {})
        H.append("<h2 id=s17>§1.7 구조적 동력 — 공급·호재·인구 (15년 가치의 진짜 driver)</h2>")
        H.append("<div class='box' style='border-left:5px solid #4A6C8C;background:#E4EAF0'>"
                 "<b>회귀 추세(§6)는 '단기 모멘텀'일 뿐</b> — 15년 가치는 정책·공급·호재·인구가 주도한다. "
                 "아래 등급은 <b>관찰 가능한 현재 구조적 위치(강/중/약)</b>이며 <b>미래 가격 예측이 아니다</b>(RDU-021). "
                 + smeta.get("caveat", "") + "</div>")
        H.append("<table>" + _row(["단지", "공급(입주)", "확정 호재", "인구 추세", "구조적 동력", "출처"], "th"))

        def _scell(sd, k):
            v = sd.get(k, ["", ""])
            return f"<b>{v[0]}</b> <span class=sub>{v[1]}</span>" if isinstance(v, list) else str(v)

        for e in ev:
            sd = structural.get(e.candidate.listing.complex_name)
            if not sd:
                H.append(_row([_nm(e.candidate), "—", "—", "—", "—", "—"]))
                continue
            tot = sd.get("등급", "")
            tcls = "good" if tot in ("상", "중상") else ("bad" if tot in ("약", "중하") else "")
            tot_cell = (f"<span class={tcls}><b>{tot}</b></span>" if tcls else f"<b>{tot}</b>")
            H.append(_row([_nm(e.candidate), _scell(sd, "공급"), _scell(sd, "호재"),
                           _scell(sd, "인구"), tot_cell, f"<span class=sub>{sd.get('출처', '')}</span>"]))
        H.append("</table><div class=src>[추론] 전 항목 <b>단일출처 INFERENCE</b>(agent-intel, " + smeta.get("as_of", "")
                 + "). 공급 '약'=입주물량 적음(방어+)/'강'=공급압박. 인구 '감'=장기 하방. "
                 "<b>★대구는 공급방어 ↔ 인구 2년연속 구조적 감소가 상충(15년 시계 장기 역풍)</b>. "
                 "<b>coarse 등급이며 §5 조정점수 미편입</b>(편입은 사용자 합의 — 단일출처라 과신 금지). 미래 가격 예측 아님.</div>")

    # ── §3 라이브 매물 4요소 ──
    H.append("<h2 id=s3>§3. 라이브 매물 4요소 매트릭스 (네이버부동산)</h2><table>")
    H.append(_row(["단지/평형", "호가", "동·층·향", "중개사", "확인일", "전세"], "th"))
    for e in ev:
        l = e.candidate.listing
        stale = " <span class=bad>(stale)</span>" if l.is_stale(today) else ""
        bc = e.candidate.broker_count
        agent_cell = f"{l.agent_name}" + (f" <span class=good>외 {bc-1}곳(진위강)</span>" if bc > 1
                                          else " <span class=sub>(단독·진위약)</span>")
        H.append(_row([f"{_nm(e.candidate)} 전용{l.area_exclusive_m2:.0f}㎡({l.pyeong}평)",
                       _eok(l.price_krw), f"{l.dong_ho} {l.floor} {l.facing}", agent_cell,
                       f"{l.confirmed_date:%y.%m.%d}{stale}",
                       _eok(e.candidate.jeonse_krw) if e.candidate.jeonse_krw else "—"]))
    H.append("</table><div class=src>[사실] 전 매물 source=NAVER_LIVE_CHROME·price_kind=ASKING_LIVE (G1 통과). 추정·웹검색 호가는 본 표 진입 불가.</div>")

    # ── §4 자본 시뮬 ──
    H.append("<h2 id=s4>§4. 자본 시뮬 (LTV·DSR·취득세·자기자본)</h2><table>")
    H.append(_row(["단지", "지역·LTV", "대출(min)", "구속", "취득세", "자기자본 필요", "여유?"], "th"))
    for e in ev:
        f = e.finance
        ok = "<span class=good>OK</span>" if f.equity_ok else "<span class=bad>부족</span>"
        reg = getattr(e.candidate, "regulated", True)
        region_cell = (f"{e.candidate.district} · <b class=good>비규제 80%</b>" if not reg
                       else f"{e.candidate.district} · 규제 70%")
        H.append(_row([_unit(e.candidate), region_cell, _eok(f.loan_krw), f.loan_binding,
                       _eok(f.acquisition_tax_krw), _eok(f.equity_required_krw), ok]))
    H.append("</table><div class=src>[사실] 결정론 계산(AGENT_CALC). 생애최초 LTV — <b>규제(서울) 70% / 비규제(대구 수성구) 80%</b> "
             "후보별 적용(정책캐시 ltv_first_regulated/nonreg). DSR(스트레스) 40% 공통. "
             "<b>★대출 3중 binding = min(LTV, 스트레스DSR, 절대한도)</b>: 수도권/규제 주담대는 <b>① DSR 만기 30년 cap(6.27, 장기만기 우회차단) "
             "② 절대한도 6억(6.27·10.15)</b> 적용 — 연소득 1억 기준 DSR(30년·스트레스3%) 한도 <b>≈4.82억</b>이 LTV·6억한도보다 먼저 묶임(binding=DSR). "
             "(40년 가정 시 5.15억으로 과대 → 30년 cap 으로 교정, 2026-06-05 wolfram 검증). "
             "<b>★예산선 역산(자본 4.0억): 서울·대구 모두 ~9.65억</b> — DSR 스트레스가 대출을 ~6억에 먼저 묶어 "
             "<b>대구 80% LTV가 고가구간에선 무력화</b>(범어롯데캐슬 10.4억은 자기자본 4.78억 필요=예산 초과 F_OVERBUDGET). "
             "단 <b>DSR 미적용 저가구간(대구 메트로팔레스·e편한범어 ~6억대)은 80% LTV 작동 → 자기자본 1.0~1.4억으로 여유 大</b>.</div>")

    # ── §4.1 정책대출 적격 + 토지거래허가구역 게이트 (사용자 맞춤 법규, 2026-06-05) ──
    _inc = int(profile.get("annual_income_krw", 0))
    _prices = sorted(e.candidate.listing.price_krw for e in ev)
    _repp = _prices[len(_prices) // 2] if _prices else 0   # 대표가=중앙값(최저가 아웃라이어 회피)
    _first = profile.get("first_time", True)
    _nh = int(profile.get("num_homes", 1))
    _newborn = bool(profile.get("has_newborn", False))
    ploans = assess_policy_loans(annual_income_krw=_inc, price_krw=_repp, first_time=_first,
                                 num_homes=_nh, has_newborn=_newborn)
    _any_elig = any(pl.eligible for pl in ploans)
    pl_rows = "".join(
        f"<tr><td>{pl.program}</td><td>{'<span class=good>적격</span>' if pl.eligible else '<span class=sub>적용대상 아님</span>'}</td>"
        f"<td class=sub>{pl.reason}</td></tr>" for pl in ploans)
    H.append("<h2 id=s41>§4.1 정책대출 적격 + 법규 게이트 (사용자 맞춤)</h2>")
    H.append("<div class='box' style='border-left:5px solid #5A8B74;background:#EEF3EF'>"
             "<b>정책대출(저리) 적격 판정</b> — 대표가(후보 중앙값 " + _eok(_repp) + ") 기준. "
             + ("<b class=good>일부 적격</b> — 해당 프로그램 금리로 재계산 권장." if _any_elig
                else "<b class=sub>전부 적용대상 아님(N/A)</b> — 소득/주택가 요건 초과로 본 시나리오(소득 " + _eok(_inc) + ") 비대상. 시장금리(프로필 mortgage_rate) 적용이 맞다.")
             + "</div>")
    H.append("<table>" + _row(["정책대출", "적격", "사유(2026 요건)"], "th") + pl_rows + "</table>")
    H.append("<div class=src>[사실] 출처: 주택도시기금 디딤돌/신생아특례 · 한국주택금융공사 보금자리론(확인 2026-06-05). "
             "요건(2026-06-06 적대감사 갱신): 디딤돌 생애최초 소득≤0.7억·주택≤5억(신혼 8.5천 별도) / 신생아특례 ≤1.3억·주택≤9억 / 보금자리론 ≤0.7억·≤6억. "
             "적격이면 시장금리 대신 정책금리로 §4 대출·자기자본 재계산.</div>")
    # 토지거래허가구역 (서울 전역, 10.15 대책) — 모든 서울 후보 공통 법규
    _seoul = [e for e in ev if "서울" in (e.candidate.district or "")]
    if _seoul:
        H.append("<div class='box crit'>"
                 "<b>⚠️ 토지거래허가구역 게이트 (서울 전역, 2025.10.15~)</b> — 서울 후보 "
                 f"{len(_seoul)}건 전부 해당. <b>① 매수 시 허가 취득 필요</b>(거래 자체 게이트) · "
                 "<b>② 취득일로부터 2년 실거주의무</b>(이 기간 매도·임대 금지) · <b>③ 자금조달계획서 의무</b>. "
                 "<b>HOLD_AND_RENT 영향</b>: '2년 실거주 후 임대' 전략과 <b>타이밍은 정합</b>(2년 채운 뒤 임대 가능)하나, "
                 "<b>2년간 임대 전환·전세 끼고 매수(갭) 불가 → 자기자본 잠김</b>. 소유권이전조건부 전세대출 금지(6.27). "
                 "<span class=sub>출처: 정책브리핑 10.15 · 서울부동산정보광장 land.seoul.go.kr (확인 2026-06-05). "
                 "비과세 2년거주 리스크는 사용자 지시로 분석 제외, 단 임대 차단은 전략 실행 제약이라 표시.</span></div>")

    # ── §5 10축 가중점수 (주 비교 프레임, 본문) ──
    H.append("<h2 id=s5>§5. 10축 가중점수 — 주 비교 프레임</h2>")
    H.append("<div class='box' style='border-left:5px solid #4A6C8C;background:#EEF1F4'>"
             "<b>이 표가 주 비교 프레임이다</b> — 10개 축(전세수요·환금성·가격방어·상승여력·토지지분·가격메리트·출퇴근·학군·경사·후기)을 "
             "전략 가중치로 합산한 <b>같은 급지 내 적합도 종합</b>. <b>정직 각주</b>: 점수 단독은 미래 CAGR을 잘 못 맞힌다(백테스트 ρ²≈1.2%) — "
             "그래서 §★ 보조 FACT 신호(생활권 base-rate·mean-reversion)와 *함께* 본다. 가중치는 gemma4 재검토 반영(상승여력↑·환금성↓, 2026-06-03).</div>")
    _title = ("전략별 가중(혼합)" if strategy_by_name else f"{strategy.value} 가중치")
    H.append(f"<h2 style='font-size:15px;border:0'>10축 별점 ({_title})</h2><table>")
    _hdr = ["단지"] + (["전략"] if strategy_by_name else []) + \
           [a for a in AXES] + ["가중합(가격포함·참고)", "조정점수(호가무관·순위)", "플래그"]
    H.append(_row(_hdr, "th"))
    for i, e in enumerate(ev):
        mark = "⚠️" if e.flags else ""
        adj = (f"<b>{e.adjusted_fundamental:.3f}</b>" if not e.flags
               else f"<span class=bad><b>{e.adjusted_fundamental:.3f}</b></span>")
        sv = strategy_by_name.get(e.candidate.listing.complex_name, "") if strategy_by_name else ""
        strat_cell = [f"<span class=sub>{sv}</span>"] if strategy_by_name else []
        cells = [("🥇 " if i == 0 else "") + _unit(e.candidate)] + strat_cell + \
                [_bar(e.axis.scores[a]) + ("<sup class=bad>ⁱ</sup>" if a in e.axis.imputed else "") for a in AXES] + \
                [f"{e.axis.weighted_total:.3f}", adj, mark]
        H.append("<tr class=win>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>" if i == 0
                 else _row(cells))
    if strategy_by_name:
        _ws = " / ".join(f"<b>{s.value}</b>: " + "·".join(f"{a} {WEIGHTS[s][a]:.2f}" for a in AXES)
                         for s in dict.fromkeys(WEIGHTS))
        wnote = ("<b>혼합 평가</b> — 후보별 전략 가중치로 조정점수 산출(서울=임대/대구=실거주). 가중치: " + _ws + ". ")
    else:
        # ★적대검증(2026-06-05 gemma4·gpt-oss·agent-council): 명목가중(전세수요0.17·가격메리트0.12)만 표기하면
        #   '최고가중인데 순위 0 반영' 오도 → 실효 랭킹가중(8축 재정규화) + 랭킹무관 2축을 명시 분리.
        _fw = sum(w[a] for a in FUNDAMENTAL_AXES) or 1.0
        _eff = "·".join(f"{a} {w[a] / _fw:.2f}" for a in FUNDAMENTAL_AXES)
        _prc = "·".join(f"{a} {w[a]:.2f}" for a in PRICE_DERIVED_AXES)
        wnote = (f"<b>순위결정 실효가중({strategy.value}, 8축 재정규화 합1.0)</b>: {_eff}. "
                 f"<b>⚠️ 참고·랭킹무관 2축</b>(호가분리): {_prc} <span class=sub>— 명목가중이나 순위(fundamental)엔 "
                 f"0 반영. '최고가중'이 아니라 '가격대비 매력 별도 확인용'</span>. ")
    H.append("</table><div class=note><b class=bad>ⁱ</b> = <b>표본평균 대체</b>(입력 미수집 — 측정값 아님·변별력 없음. 전세수요·학군·경사·후기 해당). "
             "축 점수는 <b>[추론]</b> 순서형 휴리스틱(계단 임계)이며 결과로 보정된 객관 측정이 아니다. "
             + wnote + "<b>★순위 = 조정점수(호가무관)</b> = fundamental_total(가격메리트·전세수요 <b>제외</b>) × Risk Flag 페널티 — "
             "'많이 빠짐→싸짐→상위' 누수(H1) 차단(2026-06-04 Wittgenstein 호가분리). "
             "<b>가격 매력도</b>(이 호가가 싼가)는 위 <b>가격메리트·전세수요</b> 축 칸으로 <b>별도</b> 확인 — 펀더멘털 좋고+가격메리트 높으면 매수적기. "
             "근소차는 동급 — <b>방법론 상세는 ↓ 별지 B</b>.</div>")

    # ── §5.x 가중 기여도 분해 (각 축 점수×가중치 = 가중합 구성) ──
    _cw = w   # 단일전략 가중치(트랙별 리포트). 각 칸 = 축점수 × 가중치.
    H.append("<h2>§5 가중 기여도 분해 — 각 축 (점수×가중치) = 가중합</h2>"
             "<div class=note>아래 각 칸은 <b>축점수(0~5) × 전략가중치</b>(괄호). 한 행을 가로로 더하면 그 단지 <b>가중합</b>이 되고, "
             "가장 큰 칸이 점수를 끌어올린 핵심 축이다. 가중치 합 = " + f"{sum(_cw.values()):.2f}" + ".</div><table>")
    H.append(_row(["단지"] + [f"{a}({_cw[a]:.2f})" for a in AXES] + ["가중합"], "th"))
    for i, e in enumerate(ev):
        contribs = [f"{e.axis.scores[a] * _cw[a]:.3f}" for a in AXES]
        cells = [("🥇 " if i == 0 else "") + _nm(e.candidate)] + contribs + [f"<b>{e.axis.weighted_total:.3f}</b>"]
        H.append("<tr class=win>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>" if i == 0 else _row(cells))
    H.append("</table>")
    # ── §5 끝 (주 프레임 — 별지 아님) ──

    # ── §5.1 재건축·토지지분 (compact, 본문 잔류 — 1순위 가치축) ──
    H.append("<h2>§5.1 재건축·토지지분 (1순위 가치축)</h2><table>")
    H.append(_row(["단지", "단계", "용적률", "대지지분", "토지 평당가", "사업성"], "th"))
    for e in ev:
        r = e.redev
        H.append(_row([_unit(e.candidate), r.stage_label, f"{r.far_pct:.0f}%",
                       f"{r.land_share_pyeong:.1f}평{'(추정)' if e.candidate.land_share_is_estimate else ''}",
                       f"{r.land_value_per_pyeong_krw/EOK*10000:.0f}만/평", f"{r.feasibility_0to5:.2f}/5"]))
    H.append("</table>")

    # ── §5.2 재개발(15년) vs 준공 실거주 — 15년 보유 시계 비교 (사용자 요청 2026-05-30) ──
    # Thesis A(재개발, 신통기획 신청후=정비구역지정 level≥2) vs Thesis B(준공, redev=NONE, 즉시 거주가능).
    # 같은 15년 보유 시계에서 가격상승 경로 비교. 미래값은 [가정] 밴드(점예측 아님, §6 철학 일관).
    HORIZON = 15

    def _grow(price: float, pct: float) -> float:
        return price * (1 + pct / 100) ** HORIZON

    thesis_a = [e for e in ev if e.candidate.redev_stage.level >= 2]   # 신통기획후(정비구역지정+)
    thesis_b = [e for e in ev if e.candidate.redev_stage.level == 0]   # 준공(redev 없음)
    excluded = [e for e in ev if e.candidate.redev_stage.level == 1]   # 안전진단통과만 = 신통기획 미달
    H.append("<h2 id=s52>§5.2 재개발(15년) vs 준공 실거주 — 15년 보유 시계 비교</h2>")
    H.append("<div class='box' style='border-left:5px solid #d39e00;background:#fffbe6'>"
             "<b>읽는 법</b> — 같은 <b>15년 보유</b> 가정으로 두 전략의 가격상승 경로를 비교한다. "
             "미래 가치는 <b>[가정] 밴드</b>(점예측 아님): 시장추세 밴드는 실거래 회귀 95%CI, 추세 약함/없음이면 "
             "보수중립(−2/0/+2%/년). <b>⚠️ 15년 외삽 댐핑</b>: 단기(1~2년) 실거래 추세를 15년 복리하면 비현실적이라"
             "(예: +8%/년×15년=3.3배) <b>15년 후 가치는 장기 지속가능 상한 ±4%/년으로 캡</b>(밴드 컬럼은 단기 원본). "
             "<b>⚠️ 재개발(A) '신축전환 프리미엄' = 자유 알파 아닌 리스크 프리미엄</b> — 자체 백테스트(2026-06-04 H3): 단계 진입 "
             "marginal alpha ≈ NULL(Δexcess +0.9%/yr, p=0.51), 코호트 추적가능 9%(나머지 멸실=생존편향). "
             "<b>시장밴드가 재개발 상방을 체계적으로 과소평가하지 않는다</b>(겉보기 초과는 생존자·연속궤적 artifact). A의 대가=추가분담금·이주·15년 락·무산 리스크. "
             "준공(B)은 즉시 거주가능+전세수요. <b>'준공 10~15년'(연식 컬럼 확인)은 대구 신축(e편한범어·수성롯데캐슬 11년·만촌삼정 7년); "
             "서울 준공(1989~2005)은 노후</b>라 같은 B라도 거주효용 다름. <b>실거주 만족도 정성은 §1.6</b>. 재개발은 신통기획후(정비구역지정+) 한정.<br>"
             "<b>★개선(2026-05-31, P0+P1)</b>: 단기 회귀선 외삽을 폐기. 15년 baseline = <b>동일단지 실거래 장기 명목 CAGR</b>(과거 컬럼=[사실]). "
             "미래는 <b>3시나리오</b> — <b>보수</b> 명목 0%/년(하한 floor, 실질 마이너스) · <b>중립</b> +3%/년(물가 수준, 실질≈0) · "
             "<b>낙관</b> 과거 장기 실측 CAGR 반복(≤8% 캡). <b>⚠️ 과거 CAGR을 그대로 미래 외삽하면 역방향 과적합</b>이라 낙관 시나리오로만 쓰고, "
             "중립은 물가 수준으로 보수화. 장기표본 부족(2년)이면 낙관 +4% 보수 적용. 단기회귀는 '최근 모멘텀' 참고로 강등. "
             "<b>거시·지역 정세(인구·수급)는 §1.7</b>이 어느 시나리오가 무거운지 가리킴(대구는 인구역풍→보수 가중).</div>")
    H.append("<table>" + _row(["Thesis·단지", "매수가", "단계 / 연식", "15년 시나리오(보수·중립·낙관 %/년)",
                               "15년 후 가치(보수·중립·낙관, 명목)", "추가 가치 / 리스크"], "th"))

    def _thesis_rows(group, tag):
        for e in group:
            c = e.candidate
            l = c.listing
            # ★P1(2026-05-31): 15년 가치 baseline = 단기회귀 외삽 폐기 → 실거래 장기 명목 CAGR 시나리오.
            # 보수=명목 0%/년(floor) · 중립=+3%(물가 수준) · 낙관=과거 장기 실측 CAGR(≤8% 캡, '직전기간 반복 시').
            # 과거 CAGR 을 그대로 미래 외삽하면 역방향 과적합 → 낙관 시나리오로만 사용. 단기회귀(tr)는 '최근 모멘텀' 참고로 강등.
            CPI = 2.3
            cg = (cagr or {}).get(c.listing.complex_name)
            longrun = bool(cg and cg.get("years", 0) >= 10)
            g = cg["cagr"] if cg else None
            lo, mid = 0.0, 3.0                                  # 보수(명목 floor)·중립(물가)
            # ★적대검증(2026-06-06): 캡 8%↔박스문구 '±4%/지속가능상한' 자기모순(1.08^15=3.17배=박스가
            #   '비현실'이라 제거 선언한 바로 그 케이스). 박스 의도대로 명목 4% 상한으로 통일(1.04^15=1.80배,
            #   실질≈+1.7% 한국 장기 주택 추세 대역). 15년 외삽은 지속가능 상한이 핵심.
            hi = (min(g, 4.0) if longrun else 4.0)              # 낙관: 장기표본 CAGR 캡 4%(지속가능 상한)
            v_lo, v_hi = _grow(l.price_krw, lo), _grow(l.price_krw, hi)            # 명목 (보수~낙관)
            v_mid = _grow(l.price_krw, mid)
            r_lo, r_hi = _grow(l.price_krw, lo - CPI), _grow(l.price_krw, hi - CPI)  # 실질
            tr = e.trend
            mom = (f"+{tr.band[1]*100:.1f}%" if (tr and getattr(tr, "band", None)) else "약")
            band_src = (f"실측 장기 {cg['years']}년 CAGR +{g:.1f}%/년" if longrun
                        else (f"장기표본 부족(가용 {cg['years']}년)" if cg else "실측 CAGR 미산출"))
            age = (today.year - c.built_year) if c.built_year else None
            stage_age = f"{e.redev.stage_label} / {age}년" if age is not None else e.redev.stage_label
            if tag == "A":
                extra = (f"재개발 사업성 {e.redev.feasibility_0to5:.1f}/5 · "
                         "<b class=bad>리스크 프리미엄(자유 알파 아님)</b><br>"
                         "<span class=sub>[백테스트 H3, 2026-06-04] 단계 진입 marginal alpha ≈ NULL(Δexcess +0.9%/yr, p=0.51), "
                         "추적가능 9%(멸실 생존편향). 둔촌주공 2.5배류 일화는 <b class=bad>생존자 cherry-pick</b>(무산·지연 단지는 표본에서 소멸 → 중앙값 아님). "
                         "재개발의 대가 = <b class=bad>추가분담금 5~7억·이주·15년 락·무산 리스크</b>를 진 보상이지 공짜 상방이 아니다.</span>")
            else:
                jeonse = (_eok(c.jeonse_krw) if c.jeonse_krw else "—")
                aged = " <span class=bad>(노후)</span>" if (age is not None and age >= 25) else ""
                extra = f"<b>즉시 거주가능</b>(주거효용) · 전세수요 {jeonse}{aged}"
            # P3: §1.7 구조동력 → 시나리오 분기 가중 화살표(점수 미반영, 곱셈 아님)
            sd = (structural or {}).get(c.listing.complex_name)
            if sd:
                tot = sd.get("등급", "")
                pop = sd.get("인구", ["", ""])
                pop_g = pop[0] if isinstance(pop, list) else ""
                lean = ("낙관 가중(구조 상)" if tot in ("상", "중상") else
                        "보수 가중(구조 약·인구 하방)" if (tot in ("약", "중하") or pop_g == "감") else "중립")
                lcls = "good" if "낙관" in lean else ("bad" if "보수" in lean else "")
                extra += f"<br><span class={lcls}>↳ 거시(§1.7): {lean}</span>"
            past = (f"[사실] 지난 {cg['years']}년 {cg['p0']}→{cg['p1']}억 (+{g:.1f}%/년)" if cg
                    else "[사실] 장기 실거래 미확보")
            band_cell = (f"보수 0% · 중립 +3% · 낙관 +{hi:.1f}%/년"
                         f"<br><span class=sub>{past} · 최근모멘텀 {mom} · {band_src}</span>")
            val_cell = (f"보수 {_eok(v_lo)} · 중립 <b>{_eok(v_mid)}</b> · 낙관 {_eok(v_hi)}"
                        f"<br><span class=sub>실질(CPI {CPI}%) 보수 {_eok(r_lo)} ~ 낙관 {_eok(r_hi)}</span>")
            H.append(_row([f"<b>[{tag}]</b> " + _unit(c), _eok(l.price_krw), stage_age,
                           band_cell, val_cell, extra]))

    _thesis_rows(thesis_a, "A")
    _thesis_rows(thesis_b, "B")
    H.append("</table>")
    if excluded:
        H.append("<div class=note>제외(신통기획 미달 — 안전진단통과 단계뿐): "
                 + ", ".join(_nm(e.candidate) for e in excluded) + " — 사용자 필터 적용.</div>")
    # 우선순위 종합 — 단정 아닌 조건부(시스템 철학)
    a_names = ", ".join(_nm(e.candidate) for e in thesis_a) or "없음"
    b_names = ", ".join(_nm(e.candidate) for e in thesis_b) or "없음"
    H.append("<div class=box style='border-left:5px solid #4A6C8C'>"
             "<b>우선순위 종합 [추론]</b> (HOLD_AND_RENT · 5년내 비처분)<br>"
             f"· <b>재개발(A)</b> {a_names}: 토지지분 실현 잠재는 있으나 <b>자유 알파 아님</b> — 자체 백테스트(H3)상 단계 alpha NULL·생존편향. "
             "추가분담금·이주·15년 락·무산 리스크를 진 데 대한 <b>리스크 프리미엄</b>으로만 정당화되며, 시장밴드가 A를 체계적으로 과소평가한다는 증거는 없다.<br>"
             f"· <b>준공(B)</b> {b_names}: 즉시 거주가능(주거효용)·전세수요로 <b>중간 현금흐름·환금성</b> 우위, "
             "연식 노후일수록 거주효용·임대경쟁력 감소.<br>"
             "→ <b>임대수익·환금·중기 안정</b>을 중시하면 B, <b>토지지분 실현 가능성(불확실)에 리스크 프리미엄</b>을 걸고 락을 감내하면 A. "
             "단일 승자 단정 불가 — 두 축은 서로 다른 시계의 베팅이다. 15년 후 가치는 모두 [가정]이며 "
             "재개발 프리미엄·추가분담금 실측 전까지 액면 신뢰 말 것(H3: 단계 alpha NULL).</div>")

    # ── §6 시나리오 — 밴드 헤드라인 본문 잔류(정직 프레이밍 lock). 관측=[사실]/외삽=[가정] 분리 ──
    H.append("<h2 id=s6>§6. 시나리오 (미래 가치 = 밴드, 점예측 아님)</h2>")
    H.append("<div class='box' style='border-left:5px solid #d39e00;background:#fffbe6'>"
             "<b>읽는 법</b> — <b>이 회귀밴드는 '단기 모멘텀'일 뿐</b>(1~2년 실거래 추세)이며 <b>15년 가치의 driver가 아니다</b> — "
             "정책·공급·호재·인구 구조는 <b>§1.7</b> 참조. 미래 가치/차익은 <b>[가정] 조건부</b>('추세 지속 시')·<b>점예측 아님</b>. "
             "세 줄=실거래 회귀 95%CI. <b>밴드 넓을수록·연수 길수록 '모른다'</b>. 잔여대출·보유비용만 [사실]. "
             "<b>10/15년 행의 상승밴드는 ±4%/년 지속가능 캡</b>(§5.2 와 동일 원칙 — 단기 모멘텀의 장기 무캡 복리 차단, 3차 감사 2026-06-11). 5년 행은 단기 원본.</div>")
    # ★스크롤 벽 해소(3차 감사 UX#13): 시나리오 박스는 상위 10개만 본문에 펴고 나머지는 접기.
    _S6_OPEN = 10
    for _i6, e in enumerate(ev):
        if _i6 == _S6_OPEN:
            H.append(f"<details><summary>나머지 {len(ev) - _S6_OPEN}개 단지 시나리오 펼치기</summary>")
        H.append(f"<div class=box><b>{_nm(e.candidate)}</b><br>")
        if e.hold:
            h = e.hold
            extra = ("전세<대출 → 전환 시 추가현금 " + _eok(h.extra_cash_to_convert_krw)
                     if h.extra_cash_to_convert_krw > 0 else "전세금이 대출 이상 — 추가현금 불필요")
            H.append(f"[사실] 전세 전환 후 잔여대출 {_eok(h.residual_loan_krw)} · 연 보유비용(이자+재산세+종부세) {_eok(h.annual_carry_krw)} · {extra}<br>")
            # 밴드 출처 + 강도 + §5↔§6 화해 (laozi: 약한 추세 → 넓은 밴드 = 정직한 불확실성)
            strength = e.trend.strength if e.trend else None
            if h.band_kind == "회귀밴드":
                src = (f"[가정] 미래 상승 밴드 = 실거래 회귀 95%CI <b>{h.band_pct[0]:+.1f}~{h.band_pct[2]:+.1f}%/년</b>"
                       f" (중앙 {h.band_pct[1]:+.1f}%, 추세강도 '{strength}')")
                if strength == "중":
                    src += " <span class=bad>— 표본/구간 보통, 의사결정 보조로만</span>"
            else:
                src = ("<span class=bad>[가정] 실거래 추세가 약함/없음(n<5·구간넓음·LOO불안정·과열) → "
                       "외삽 근거 부족. 보수 중립 밴드 −2/0/+2% 적용 — 미래 가치는 사실상 '모름'.</span>")
            up = e.axis.scores.get("상승여력")
            if up is not None and up <= 2.0 and h.band_kind == "회귀밴드" and (strength != "약"):
                src += (f"<br><span class=bad>⚠️ §5 상승여력 점수 {up:.1f}/5(낮음, 재건축단계 기반)와 "
                        "회귀 추세가 충돌 — 단기 거래추세는 상방이나 구조적 상승동력은 약함. 밴드를 액면 신뢰 말 것.</span>")
            H.append(src + "<br>")
            H.append("<table>" + _row(["보유연수", "상승밴드", "평가가치", "평가차익", "누적보유비용", "순(미실현)"], "th"))
            band_label = {0: "하한", 1: "중앙", 2: "상한"}
            for idx, row in enumerate(h.rows):
                lbl = band_label[idx % 3]
                H.append(_row([f"{row.years}년", f"{lbl} {row.appreciation*100:+.1f}%/년", _eok(row.value_krw),
                               _eok(row.unrealized_gain_krw), _eok(row.cumulative_carry_krw),
                               ("<span class=good>" if row.net_krw >= 0 else "<span class=bad>") + _eok(row.net_krw) + "</span>"]))
            H.append("</table>")
        if e.break_even:
            b = e.break_even
            cgt = (f" · 예상 양도세(+5% 매도, 1세대1주택 거주2년+): {_eok(b.capital_gains_tax_5pct_krw)}"
                   if b.capital_gains_tax_5pct_krw > 0 else " · 양도세 0(1세대1주택 12억 이하 비과세)")
            H.append(f"[사실] 1년 손익분기 매도가 {_eok(b.break_even_price_krw)} (상승률 {b.break_even_rate*100:.1f}%), 거래비용 {_eok(b.costs_krw)}{cgt}<br>")
        H.append("</div>")
    if len(ev) > _S6_OPEN:
        H.append("</details>")

    # ── §7 A′ vs B — 15년 순자산 단일화폐 비교 (비교셋-초월, 설계 §6) ──
    # 상대노출(§★)은 *같은 비교셋 안*에서만 유효 → 서로 다른 생활권 비교는 결정론 재무 투영으로만.
    # 각 path 낙관 g = *그 생활권* base-rate median(전이 금지). 미주입이면 중립(CPI)으로 캡.
    HORIZONS = (10, 15, 20)   # 보유기간 심화(2026-06-02) — 매도시점별 순자산 진화
    H.append("<h2 id=s7>§7. A′ vs B — 보유기간별 15년(±) 순자산 단일화폐 비교 (비교셋-초월)</h2>")
    H.append("<div class='box' style='border-left:5px solid #4A6C8C'>"
             "<b>왜 이 표인가</b> — §★ 상대노출·base-rate 밴드는 <b>같은 생활권 안에서만</b> 유효하다(공간 비정상성). "
             "서로 다른 생활권(서울 보유+임대 A′ vs 대구 수성 실거주 B)은 <b>순자산 단일화폐</b>로만 비교 가능. "
             "순자산 = 가정 매각가치 − 잔여대출 − 누적 순보유비용 − 기회비용(자기자본). "
             "g(상승률)는 점추정 아닌 <b>3-시나리오</b>(보수 0% · 중립 물가 2.3% · 낙관 <b>그 생활권 base-rate</b>). "
             "<b>★보유기간 10·15·20년</b>으로 분리 — <b>base-rate 격차는 복리라 보유기간이 길수록 A′(고base-rate)와 B 의 순자산 차이가 벌어진다</b>(매도시점 가정). "
             "<b>⚠️ 성장률 g = 각 생활권 과거 base-rate median 외삽 *가정*이며 미래 예측이 아니다</b>(§6 밴드와 동일 한계 — 점수↔실현 ρ=0.08, 점예측 금지). "
             "<b>⚠️ 미실현·가정 매각가치 기준</b>·낙관 g 는 그 생활권 것만(전이 금지)·법적 게이트 동반.<br>"
             "<b>⚠️★ A′(임대) ↔ B(실거주) 비대칭 — 이 순자산은 '투자수익' 렌즈다</b>: A′(보유+임대)는 전세/월세가 보유비용을 상쇄(임대수익 net)하지만, "
             "<b>B(실거주)는 임대수익 0 + 기회비용 부과로 순자산이 음수로 나온다 — 이는 '나쁜 결정'이 아니라 이 지표가 B 의 핵심가치인 "
             "거주효용(imputed rent=남의 집에 안 내는 월세)을 누락하기 때문</b>. B 는 '15년 거주하며 자산이 얼마나 보존되나'로, A′ 는 '순자산이 얼마나 불어나나'로 읽어야 공정하다.</div>")
    nv = (lambda v: ("<span class=good>" if v >= 0 else "<span class=bad>") + _eok(v) + "</span>")
    H.append("<table>" + _row(["단지 / 생활권", "낙관 g(base-rate)"]
                              + [f"순자산 {h}년 (보수·중립·낙관)" for h in HORIZONS]
                              + ["법적 게이트"], "th"))
    for e in ev:
        l = e.candidate.listing
        sg = _sg_of(compset, e.candidate) or (e.candidate.district or "—")
        b = _compset_band(compset, e.candidate)
        residual = e.hold.residual_loan_krw if e.hold else e.finance.loan_krw
        # ★carry 대칭화(3차 감사 신규 #3): HOLD leg 만 관리비·수선 포함하고 비-HOLD leg 은 이자+세금만이면
        #   한 표에서 서로 다른 carry 정의로 비교(실거주 leg 보유비용 과소). 비-HOLD 도 관리비·수선 포함
        #   (PolicyParams 기본 근사 — 관리비·수선은 '가정' 항목이라 캐시 override 거의 없음).
        _pp = _DEFAULT_PP
        _extras = (int(_pp.management_fee_per_pyeong_month * 12 * e.candidate.listing.pyeong)
                   + int(l.price_krw * _pp.maintenance_pct))
        carry = (e.hold.annual_carry_krw if e.hold
                 else e.finance.annual_interest_krw + e.finance.property_tax_krw
                 + e.finance.comprehensive_tax_krw + _extras)

        def _nw(h):
            return project_networth_15yr(
                name=l.complex_name, saenghwalgwon=sg, price_krw=l.price_krw,
                residual_debt_krw=residual, annual_carry_krw=carry,
                equity_krw=e.finance.equity_required_krw,
                base_rate_median=(b.cagr_median if b else None),
                legal_status=_legal_status(e), horizon=h)

        nws = {h: _nw(h) for h in HORIZONS}
        ref = nws[15]
        g_cell = (f"+{ref.g_band_pct[2]:.1f}%/년 <span class=sub>{sg}·전용{b.area_band}</span>" if (b and ref.baserate_injected)
                  else f"<span class=bad>미주입→중립 캡 +{ref.g_band_pct[1]:.1f}%</span>")
        hz_cells = [f"{nv(nws[h].net_lo_krw)} · <b>{nv(nws[h].net_mid_krw)}</b> · {nv(nws[h].net_hi_krw)}"
                    for h in HORIZONS]
        lcls = "good" if ref.legal_status == "PASS" else ("bad" if ref.legal_status.startswith("FAIL") else "")
        legal = f"<span class={lcls}>{ref.legal_status}</span>" if lcls else ref.legal_status
        H.append(_row([f"{_nm(e.candidate)} <span class=sub>{sg}</span>", g_cell] + hz_cells + [legal]))
    H.append("</table><div class=src>[가정] <b>미실현</b> 순자산(가정 매각가치 기준, 매도시점=보유기간) — 보수 0%·중립 물가 2.3%·낙관 그 생활권 전용band base-rate median. "
             "순자산 = 매각가치 − 잔여대출(임대 leg은 전세 차감 후) − 누적보유비용(annual_carry×보유년, 임대수익 net, 관리비·수선 양 leg 공통 포함) − 기회비용(자기자본×(1.035^보유년−1), <b>복리</b> — 매각가치와 동일 기하). "
             "<b>비교셋-초월 비교의 유일한 정직한 단위</b>. 낙관 g 전이 금지(미주입→중립 캡). "
             "<b>★해석</b>: 10→20년으로 갈수록 高base-rate(서울)와 低(대구 수성)의 낙관 순자산 격차가 복리로 확대 — 장기보유일수록 생활권 선택의 무게 ↑.</div>")

    # ── Council 헤드라인 (본문 1줄, 원문은 참조 2) ──
    if council_insight:
        cm = (f" <span class=bad>⚠️ model diversity={council_models}(단일) — 합의 신뢰 낮음, §1·§4·§6 교차검증</span>"
              if council_models is not None and council_models < 2 else "")
        H.append("<div class=note style='margin-top:14px'><b>Council 통찰</b> — 정성 보조이며 load-bearing 수치는 "
                 "§1·§4·§6 결정론 우선." + cm + " 원문 인용 → ↓ 참조 2.</div>")

    # ── §10 한계 헤드라인 (본문 1문장, 전문은 별지 C) — 정직 프레이밍 lock ──
    H.append("<div class='box crit' style='margin-top:16px'>"
             "<b>이 리포트의 '신뢰'</b> — 보장하는 것은 (a) 결정론 계산의 산술 정확성·재현성과 "
             "(b) 사실/추론/가정의 정직한 분리다. 이것은 <b>재현성</b>이지 <b>미래 적중률</b>이 아니다 "
             "(혼동하면 '계산기의 일관성'을 '예측의 정확성'으로 착각). <b>한계 전문 → ↓ 별지 C</b>.</div>")

    # ════════ 별지 (방법론·검증 상세) / 참조 (근거 원문) ════════
    H.append("<hr class=annexsep id=annex><div class=annexlabel>별지 (방법론·검증 상세) · 참조 (근거 원문) — 본문 의사결정에서 분리</div>")

    # 별지 A — 결정신뢰도 + 민감도/반증 임계
    a_parts: list[str] = []
    if trust_scores:
        a_parts.append("<h2 style='margin-top:6px'>§0.6 결정신뢰도 (입력 검증 완성도)</h2>")
        a_parts.append("<div class='box' style='border-left:5px solid #4A6C8C;background:#E4EAF0'>"
                       "<b>이 표가 '얼마나 믿을 수 있나'에 답한다.</b> 각 입력이 <b>교차검증</b>(API·실거래 대조)/"
                       "<b>검증</b>/<b>수동</b>(주입, 미검증)/<b>추정</b> 중 무엇인지 결정론으로 분류해 의사결정 영향 "
                       "가중치로 합산한 값이다. 이것은 '미래 적중률'이 아니라 <b>입력이 사실로 뒷받침되는 정도</b>다. "
                       "85%+ = 의사결정 가능, 60–85% = 독립확인 후, 60%↓ = 참고만.</div>")
        a_parts.append("<table>" + _row(["단지", "결정신뢰도", "등급", "확인 필요(구속 입력)"], "th"))
        _seen_trust = set()   # 신뢰도는 단지 단위 → 단지당 1행만
        for e in ev:
            cn = e.candidate.listing.complex_name
            dk = _nm(e.candidate)               # 동명단지 구분 dedup 키(구표기)
            if dk in _seen_trust:
                continue
            t = ts_by_name.get(dk)
            if not t:
                continue
            _seen_trust.add(dk)
            color = "good" if t.score_pct >= 85 else ("" if t.score_pct >= 60 else "bad")
            block = ", ".join(t.blocking) if t.blocking else "—"
            a_parts.append(_row([dk,
                                 f"<span class={color}><b>{t.score_pct:.0f}%</b></span>" if color else f"<b>{t.score_pct:.0f}%</b>",
                                 t.grade, f"<span class=sub>{block}</span>"]))
        a_parts.append("</table>")
        rep = ts_by_name.get(_nm(ev[0].candidate))
        if rep:
            a_parts.append("<div class=src>입력별 분해(" + rep.candidate_name + "): "
                           + " · ".join(f"{k.field}=<b>{k.level}</b>" for k in rep.components) + "</div>")
    if sensitivity:
        flipped = [r for r in sensitivity if r.flipped]
        no_test = any("검정 무의미" in r.label for r in sensitivity)
        a_parts.append("<h2>§6.5 민감도 / 반증 임계 <span class=sub>(지표 = 호가무관 조정점수×Risk — §A 정렬키와 동일, 3차 감사 A 통일)</span></h2><table>")
        a_parts.append(_row(["섭동", "교란 후 1위", "조정점수", "역전?"], "th"))
        for r in sensitivity:
            mark = ("<span class=bad>역전 ⚠️</span>" if r.flipped
                    else "—" if "검정 무의미" in r.label else "<span class=good>유지 ✓</span>")
            a_parts.append(_row([r.label, r.new_top, f"{r.new_top_adjusted:.3f}", mark]))
        a_parts.append("</table><div class=src>"
                       + (f"[추론] <b>검정 무의미</b> — 하드페일 제외 유효 경쟁자가 없어 어떤 교란도 1위를 못 바꾼다. "
                          f"'유지'는 robust 의 증거가 아님(경쟁자 부재의 동어반복)."
                          if no_test else
                          f"[추론] 모든 perturbation 에서 1위 ({base_top}) 유지 → <b>robust</b>."
                          if not flipped else
                          f"[추론] {len(flipped)}건 perturbation 에서 1위 역전 → <b>비robust</b>, 반증 임계 가까움.")
                       + " 매매가/전세 교란 행은 제거 — 순위가 호가무관이라 구조상 무변동(가짜 robust 신호 방지).</div>")
    if a_parts:
        H.append("<details open><summary>별지 A — 결정신뢰도 · 민감도/반증 임계</summary><div class=dwrap>"
                 + "".join(a_parts) + "</div></details>")

    # 별지 B — 5축 방법론 · 가중치
    H.append("<details><summary>별지 B — 5축 방법론 · 가중치 · 임계</summary><div class=dwrap>"
             "<div class=src><b>[추론]</b> 축 점수는 <b>순서형 휴리스틱</b>(세대수≥2000→4.0 식 계단 임계)이며 "
             "실제 결과로 보정된(calibrated) 객관 측정이 아니다. 결정론(재현가능)이라고 객관적인 것은 아니다 — "
             "임계·가중치는 설계자 판단. 조정점수 = 가중합 × Risk Flag 페널티. 순위는 조정점수 기준이되, "
             "근소차(별지 A 가중치 민감도 참조)는 동급으로 볼 것.</div>"
             "<div class=src>가중치(" + strategy.value + "): "
             + " · ".join(f"{a}={w[a]:.2f}" for a in AXES) + "</div>"
             "<div class=src><b>가격메리트(헤도닉)</b>: 후보집합 OLS 호가(억) ~ 1 + 평수 + 중심거리(cbd_km)[+토지지분 실측 변량시]의 "
             "잔차를 [2,5] range-정규화(저평가=5·고평가=2). 2변수 설명력 <b>R²≈0.5</b>(표본 의존)이며 <b>토지지분은 현재 전부 추정 11평 placeholder라 "
             "공선성으로 자동 제외</b>(등기부 실측 주입 시 활성). 표본&lt;5·특이행렬이면 평당가 상대순위로 폴백. '싼값=좋음'(국면반전, 백테스트) 폐기 후 *상대 저평가* 재정의(2026-06-03 사용자 OVERRIDE).</div>"
             "<div class=src>재건축·토지지분(§5.1)은 본문 표 참조 — 대지지분 '(추정)' 표기 항목은 등기부 실측 전까지 미검증.</div>"
             "<div class=src><b>상승여력·가격방어·토지지분(2026-06-04 재설계)</b>: 변별력 부족(σ≈0)을 정량 입력으로 해소 — 상세 산출근거는 <b>↓ 별지 E</b>.</div>"
             "</div></details>")

    # 별지 E — 10축 정량화근거 / 산출근거 (메타정보, 2026-06-04 사용자 요청)
    _AXIS_META = [
        ("전세수요", "전세가율(전세호가÷매매호가)", "구간화: ≥0.70→5.0 / 0.60→4.5 / 0.50→3.8 / 0.40→3.0 / &lt;0.40→2.0. 공백=표본평균 대체",
         "[사실] 전세호가=NAVER_LIVE. 레버리지·임대 현금흐름 핵심"),
        ("환금성", "세대수 + 노선수·도보(파싱)", "세대수 ≥2000→4.0…≥500→2.8 base + 노선≥2 +0.5·도보≤10분 +0.2",
         "[추론] 매도속도 proxy. HOLD_AND_RENT 는 '수요 두께'로 재정의(가중↓0.10)"),
        ("가격방어", "세대수0.30 + 준공신축0.25 + 연간거래수0.25 + 전세가율(구상대)0.20",
         "세대수(≥2500→5.0…) · 신축(age≤5→5.0…&gt;30→2.4) · MOLIT 연거래(≥80→5.0…) · 전세가율 절대+구중위대비 ±0.5",
         "[사실] 거래수=MOLIT 실거래(2024-25 연평균). <b>매매급락 보정 미적용</b>(매매 시계열 API 미구독)→구상대로 부분보완"),
        ("상승여력", "(인프라성분 + 재건축잠재성분)/2 × 구승수",
         "인프라=KAKAO(지하철거리 교통차등↑↑·마트·병원·공원·백화점) · 재건축잠재=용적률상향Gap(정책상한 300%/역세권500%−현재)+준공30년↑+세대수+<b>건폐율</b>(Gap 있을 때만 낮을수록 증축여지↑) · 구승수=15년 base-rate CAGR 6.4~10%→[0.85,1.15]",
         "[사실] 인프라=KAKAO API · 건폐율=네이버 단지정보 DOM · CAGR=MOLIT 동일단지 실현. 정책상한=brave 확인(2026 서울 3종 250~300·역세권 종상향 500)"),
        ("토지지분", "용적률(주동인) + 건폐율(보조)", "용적률 ≤180→5.0…&gt;450→2.0 + 건폐율 ≤18%→+0.4 / ≤25→+0.2 / ≤40→0 / ≤55→-0.2 / &gt;55→-0.4",
         "[사실] 용적률·건폐율=네이버 단지정보(DOM). 용적률=현재 토지밀도(주동인), 건폐율=대지 대비 건물 바닥점유(낮을수록 개방·타워형 토지가치↑). 대지지분(평) 등기부 실측은 미반영"),
        ("가격메리트", "헤도닉 OLS 잔차", "호가(억)~1+평수+중심거리[+토지지분 변량시] 잔차를 [2,5] 정규화(저평가=5). 고정 reference 집합 산출",
         "[추론] R²≈0.5(표본의존). '싼값=좋음' 폐기 후 *상대 저평가* 재정의(2026-06-03)"),
        ("출퇴근", "업무지구 최단거리(km)", "≤2→5.0 / 4→4.3 / 6→3.6 / 9→2.9 / 12→2.3 / &gt;12→2.0",
         "[추론] 직선거리 proxy(실제 대중교통 시간 아님). 서울=강남/시청/여의도"),
        ("학군", "intel 정성 + 입시학원수(KAKAO)", "hakgun_score base + 학원≥8 +0.5·≥4 +0.3. <b>전용&lt;59㎡=0</b>(가족 비대상). 공백=표본평균",
         "[추론] 명문중·학원가 근접 proxy. 학업성취 측정 아님(council 경고)"),
        ("경사", "opentopodata 실측 slope_pct", "≤3%→5.0 / 6→4.3 / 10→3.5 / 15→2.6 / &gt;15→1.8. 공백=표본평균",
         "[사실] 표고 실측. 거주·임대 공통 불리. 경가중 0.02 tiebreaker"),
        ("후기", "커뮤니티 coarse 감성(0~5)", "review_score 직접. 공백=표본평균 대체",
         "[추론] ★편향·단일출처(council 경고). 경가중 0.02 tiebreaker 전용"),
    ]
    e_rows = "".join(_row([a, inp, calc, prov]) for a, inp, calc, prov in _AXIS_META)
    H.append("<details><summary>별지 E — 10축 정량화근거 · 산출근거 (메타정보)</summary><div class=dwrap>"
             "<div class=src>각 축이 <b>어떤 실데이터</b>를 <b>어떤 식·임계</b>로 0~5 점으로 환원하는지의 단일 출처표. "
             "모든 점수는 결정론(LLM 재계산 0)이며, 임계·구간은 설계자 판단(별지 B·C 한정 참조).</div>"
             "<table>" + _row(["축", "정량화 입력(데이터 소스)", "산출식 · 임계", "provenance · 한계"], "th")
             + e_rows + "</table>"
             "<div class='box'><b>데이터 커버리지·블로커(정직 고지, 2026-06-04 갱신)</b> — 리포트 생성은 "
             "<b>COVERAGE GATE</b>(regen_reports.py)가 차원별 공백을 차단/경고로 검문해 통과한 것: "
             "학군·후기·base-rate·용적률은 <b>HARD(우리 통제 — 공백 시 생성 차단)</b>, 전세가율·실거래추세는 "
             "<b>SOFT(외부 데이터 가용성 한계 — 정직 공백)</b>. "
             "① <b>용적률(실측)</b>: 네이버 단지정보 DOM 스크랩으로 150/155 실측(재발견 포함). 미발견 5곳만 추정 잔존. "
             "② <b>건폐율</b>: 네이버 단지정보 DOM 스크랩으로 141/155 수집 → 토지지분(보조 ±0.4)·재건축잠재(용적률 Gap 동반 시)에 편입(2026-06-04 사용자 요청). 네이버가 '-'로 비운 단지는 미반영. "
             "③ <b>전세가율</b>: 네이버가 *현재 전세매물 등록* 단지만 호가 노출 → 약 50/155(주상복합은 11/61, 전세 희소). 미등록은 정직 공백, 가격방어는 중립 대체. 매매급락 보정은 전월세 실거래 시계열 API 미구독으로 미적용. "
             "④ <b>연간거래수</b>: MOLIT exit 캐시 직접매칭 51/155, 나머지는 자치구 거래중위 대체. "
             "⑤ <b>대지지분(평)</b>: 등기부 실측 전까지 용적률 proxy.</div>"
             "<div class='box crit'><b>★역할 한계 — 실증 백테스트 고지(2026-06-04, roleaudit)</b>. 이 10축 점수는 "
             "<b>미래수익·하방방어 예측기가 아니다</b>. 같은 급지(생활권×전용band) 안에서 관측속성을 일관 규칙으로 줄세우는 "
             "<b>감사 가능한 검수표</b>일 뿐이며, 아래는 직접 측정으로 확인된 경계다:<ul>"
             "<li><b>하방방어 미입증(H1 NULL)</b>: 점수 상위군이 하락국면(2021고점→2023) drawdown을 덜 맞는다는 증거 <b>없음</b> "
             "— '가격방어' 축 이름은 <b>구조적 안정성 속성</b>(대단지·신축·거래유동성·전세두께)을 뜻할 뿐 실현 방어력 보증이 아님. "
             "겉보기 방어(−0.44)는 가격메리트의 종점가 누수 인공물(많이 빠질수록 싸짐→점수↑)이었고, 누수 제거 시 무의미(+0.05).</li>"
             "<li><b>예측력 ρ²≈1.2%(n=552)</b>: 총점↔실현CAGR cross-sectional 예측력 사실상 0. 가중치 과적합이 아니라 "
             "입력 축의 ex-ante 예측정보 부재가 원인(G0: PIT-clean Ridge OOS R²+0.13 ≈ 손수가중치).</li>"
             "<li><b>다중공선성(측정)</b>: 같은 펀더멘털이 여러 축에 중복 투입 — <b>상승여력↔토지지분 r=+0.80</b>(둘 다 용적률 구동) "
             "· <b>환금성↔가격방어 r=+0.58</b>(둘 다 세대수). VIF 1.1~4.2(심각 아님). 단 0.80 쌍은 둘 다 재건축 가점에 기대는데 "
             "<b>H3가 재개발 단계 alpha=NULL</b>이라 미검증 thesis에 0.25 가중을 이중으로 건 셈 — 총점 해석 시 유의.</li>"
             "<li><b>base-rate 비정상성</b>: '생활권 base-rate'(과거 15년 구 CAGR)를 미래 낙관 시나리오로 외삽 — 프로젝트 자체가 "
             "'구-index ≠ 실현(composition 착시)'·centrality 신호 비정상성을 확인. 점추정 아닌 <b>조건부 밴드·낙관 leg 전용</b>으로만 사용(전이 금지).</li></ul>"
             "<span class=sub>전세수요·경사·후기는 outcome-joined 데이터 부재로 IC 측정 자체 불가(0 아님). 산출 `report/backtest/backtest_roleaudit_*.html`.</span></div>"
             "</div></details>")

    # 별지 C — 한계 + 자기진단 가드 (면책 전문, 텍스트 보존)
    H.append("<details><summary>별지 C — 한계와 자기진단 가드 (면책 전문)</summary><div class=dwrap>"
             "<div class='box crit'>"
             "<b>'95% 신뢰'는 단일물건 부동산 매수에서 정의 가능한 주장이 아니다.</b> 이 시스템이 보장하는 것은 "
             "<b>(a) 결정론 계산의 산술 정확성·재현성</b>(LTV·DSR·세금·전세전환·밴드 — 67테스트)과 "
             "<b>(b) 사실/추론/가정의 정직한 분리</b>이다. 이것은 <b>재현성</b>이지 <b>미래 적중률</b>이 아니다 — "
             "두 가지는 다른 개념인데 혼동하면 '계산기의 일관성'을 '예측의 정확성'으로 착각하게 된다.<br>"
             "<b>이 리포트로 할 수 있는 것</b>: 검증된 입력 하에서 자본구조·세부담·보유 현금흐름을 일관 비교하고, "
             "미래 가치의 <b>불확실성 폭</b>을 정직하게 본다. <b>할 수 없는 것</b>: 미래 시세를 점으로 맞히는 것 "
             "(§6은 점예측이 아니라 밴드이며, 모든 단지가 추세 '약함'이면 사실상 '모름'을 뜻한다).</div>"
             "<div class=box>전세·대지지분이 추정(estimate)인 항목은 표기됨. 호가·재건축단계는 수동 주입 = 미검증. "
             "정책 스냅샷이 비면 §2 미완 — scan-policy 필요. DSR은 입력 연소득·부채 기반 근사. "
             "재산세는 공시가 가정 근사. 순위는 가중치(설계자 판단)에 민감 — 별지 A에서 가중치 ±30% 교란 결과 확인.</div>"
             "<div class=box>[사실] 전 매물 4요소·NAVER_LIVE_CHROME 검증 ✓ / [사실] 추정 호가 §3 진입 0건 ✓ / "
             f"[사실] ExitStrategy={strategy.value} 분기 적용 ✓ / [사실] 모든 수치 결정론(LLM 재계산 0) ✓</div>"
             "</div></details>")

    # 참조 1 — 정책 스냅샷
    p_parts: list[str] = []
    if policy_meta:
        if policy_meta.get("is_default"):
            p_parts.append("<div class='box crit'>[가정] <b>세율·공시가율·공제가 코드 기본값(미검증)</b> — "
                           "취득세·종부세·재산세 계산이 현행 정책과 다를 수 있음. <code>scan-policy</code> 로 param 주입 권장(RDU-059).</div>")
        else:
            p_parts.append(f"<div class=src>[사실] 세율 기준: {policy_meta.get('confirmed_date')} "
                           f"(출처 {policy_meta.get('source')})</div>")
    if policies:
        p_parts.append("<table>" + _row(["주제", "사실", "법조문", "출처", "확인일"], "th"))
        for p in policies:
            p_parts.append(_row([p["topic"], f"[사실] {p['statement']}", p.get("law_ref", ""),
                                 f"<a href='{p['url']}'>link</a>", p["confirmed_date"]]))
        p_parts.append("</table>")
    else:
        p_parts.append("<div class=box>정책 캐시 비어 있음 — <code>agent-realestate scan-policy</code> 로 채우거나 주입 필요(RDU-059).</div>")
    H.append("<details><summary>참조 1 — 정책 스냅샷 (출처 URL · 법조문 · 확인일)</summary><div class=dwrap>"
             + "".join(p_parts) + "</div></details>")

    # 참조 2 — Council 통합통찰 원문
    if council_insight:
        H.append("<details><summary>참조 2 — Council 통합통찰 (원문 인용)</summary><div class=dwrap>"
                 f"<div class=box>[추론] (council 원문 인용) “{council_insight}”<br>"
                 + (f"<span class=bad>⚠️ model diversity={council_models} (단일모델) — 합의 신뢰 낮음, "
                    "down-weight 강제. 결론은 §1·§4·§6 결정론 수치로 교차검증할 것.</span><br>"
                    if council_models is not None and council_models < 2 else "")
                 + "<span class=sub>주의: council 통찰은 정성 보조이며 load-bearing 수치는 §1·§4·§6 결정론 산출이 우선."
                 + (f" session={council_session} → report_outcome 으로 결과 피드백." if council_session else "")
                 + "</span></div></div></details>")

    # 별지 — 발견 감사 (P0): MOLIT 거래단지 ∪ 네이버 스캔 비교로 누락(gap) 표면화 (silent drop 금지)
    if coverage and coverage.get("gap_by_gu"):
        H.append("<details><summary>별지 — 발견 감사 (MOLIT 누락 단지 = 재발견 후보)</summary><div class=dwrap>"
                 "<div class='box' style='border-left:5px solid #C06A6A;background:#FFF8E3'>"
                 "<b>왜 이 표인가</b> — 네이버 마커 스캔은 '현재 매물 등록 단지'만 표출하므로 거래는 활발하나 스캔에 없는 단지가 누락된다. "
                 "MOLIT 실거래를 <b>발견 감사자</b>로 써서 그 누락을 고발한다. 아래는 <b>거래 n≥30(유동)·예산적합(전용59/84 중위 ≤9.65억)인데 스캔에 없는 단지</b> — "
                 "<b>세대수·좌표 미검증</b>(K-apt 전수 미적용)이라 자동 후보 편입은 안 했고, <b>재발견 후보로 표면화</b>한다. 관심 단지가 여기 있으면 직접 편입 요청.</div>")
        for gu, lst in coverage["gap_by_gu"].items():
            if not lst:
                continue
            H.append(f"<div class=note style='margin-top:8px'><b>[{gu}]</b> 누락 유동단지 {len(lst)}개 (거래 많은 순 상위)</div>")
            H.append("<table>" + _row(["단지(MOLIT 등록명)", "전용band 중위", "전용", "MOLIT 거래수"], "th"))
            for nm, eok, band, n in lst[:12]:
                H.append(_row([nm, f"{eok}억", f"전용{band}", f"{n}건"]))
            H.append("</table>")
        H.append("</div></details>")

    # 참조 3 — 다음 의사결정 질문
    H.append("<details><summary>참조 3 — 다음 의사결정 질문</summary><div class=dwrap>"
             "<div class=box>후보 1~2개로 좁히면 등기부 대지지분 실측·전용별 전세 실호가·DSR 정밀시뮬로 확정.</div>"
             "</div></details>")

    H.append("</div></body></html>")
    return "\n".join(H)
