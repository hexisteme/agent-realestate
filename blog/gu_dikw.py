"""구 단위 글의 K·W 층(2026-10-22, DIKW) — 표(D)와 최근 1주 기록·사실 리드(I) 위에 지식·판단 도구를 그림 우선으로 더한다.

왜: 구 원고는 단지별 실거래 표와 사실 문장(D·I)뿐이라 "그래서 이 구는 무엇이 가격을 가르고, 내 돈이면 어디까지 닿나"에
답하지 않았다(사용자 2026-10-22 「D,I 정도까지만 제공… K,W까지」「너무 글로만 채우려고 하지 마세요」).

K(지식) = 가격을 가르는 축(PriceSplitAxis): 구 발행 단지를 세대수대·준공연대·전용대로 나눴을 때 구분별 평단가 중위의
  최대−최소 차(n≥3 구분만). 서울 전체의 같은 순서와 나란히 둔다. 함께 나타나는 차이일 뿐 원인이 아니다.
W(판단 도구) = 닿는 가격(ReachablePrice)·닿는 단지 수(ReachableCount): 자기자본·연소득 [가정]에 10.15 대출 규칙(LTV 40%·
  가격별 절대한도·스트레스 DSR 30년)·취득세·중개보수를 적용한 매수 가능 상한가와, 그 안에 12개월 실거래 중위가 드는 단지 수.
  독자는 그림에서 자기 자리를 찾고 한도를 묶는 것(LTV·절대한도·스트레스 DSR)과 무엇을 늘려야 닿는 가격이 움직이는지 본다.
  대출 심사가 아니다(FINANCE_CONFIRM_NOTICE). 생애최초(LTV 70%)·기존 대출·전세 끼고 사기(토허 2년 실거주)는 넣지 않는다.
제목 실험(TitleArm): A = 기존 키워드형(gu_tistory_post._build_title), B = 독자 상황 질문형(build_situation_title).
  본문은 두 팔이 같고 제목만 다르다. 같은 구는 회전 바퀴(14일)마다 번갈아든다(assign_title_arm). 기록: report/experiments/gu-dikw-title-20261022.md

되돌리기: RE_GU_DIKW=1 일 때만 동작한다. 지우면 구 원고는 2026-10-21 형태(사실 블록 + 표) 그대로다.
"""
from __future__ import annotations

import hashlib
import html
import io
import os
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import blog.macro_scenario as ms
import blog.market_charts as mch
from agent_realestate.analysts import finance as fin
from blog.wording_guard import assert_lead_wording_ok

_FLAG = "RE_GU_DIKW"
EOK = 100_000_000
LTV_REGULATED = 0.40               # [정책] 10.15 대책 규제지역(서울 전역) 무주택 일반 차주 LTV. 생애최초 70%는 계산에 넣지 않는다
RATE_PCT = ms.DEFAULT_RATE_PCT     # [가정] 대출금리 입력 기본값(전달 계산기와 같은 값)
STRESS_PP = ms.DEFAULT_STRESS_PP   # [정책] 수도권·규제지역 스트레스 가산 3.0%p
DSR_LIMIT = ms.DSR_LIMIT
TERM_YEARS = ms.DEFAULT_YEARS      # [정책] 주담대 만기 30년 상한(6.27)
POLICY_CHECKED = "2026-10-22"      # 한도 6억 유지 확인: 뉴스핌 2026-10-06 「주담대 한도 6억 더 줄어드나」(추가 규제는 논의 단계)
TAX_AREA_M2 = 84.0                 # [가정] 전용 85㎡ 이하 — 농특세 없음
TAX_AREA_LARGE_M2 = 100.0          # [가정] 전용 85㎡ 초과 — 농특세 포함(예산 계산기가 큰 평형을 따로 판정, Codex R3)
FARM_TAX_EDGE_M2 = 85.0            # [정책] 농특세는 전용 85㎡ 초과에만 붙는다
TITLE_INCOME_KRW = 100_000_000
CURVE_INCOMES_KRW = (70_000_000, 100_000_000, 150_000_000)
EQUITY_GRID_EOK = tuple(i / 2 for i in range(2, 31))   # 1.0~15.0억, 0.5억 간격
PRICE_STEP_KRW = 1_000_000
_PRICE_MAX_STEPS = 100 * EOK // PRICE_STEP_KRW
MIN_BUCKET_N = 3
CAP_EDGE_KRW = 15 * EOK            # 이 가격을 넘으면 절대한도가 6억 → 4억
CHART_WIDTH = mch.DASHBOARD_WIDTH

_AXES = (
    ("세대수대", "units_band", ("소규모", "2백세대급", "5백세대급", "1천세대급", "2천세대급")),
    ("준공연대", "decade", None),
    ("전용대", "area_band", ("~59㎡", "60-84㎡", "85-114㎡", "115㎡+")),
)


def enabled() -> bool:
    return os.environ.get(_FLAG) == "1"


def _label(gu: str) -> str:
    return gu if gu.endswith("구") else f"{gu}구"


_SUBJECT = {"LTV": "LTV가", "절대한도": "절대한도가", "스트레스 DSR": "스트레스 DSR이"}   # 한도 이름 + 주격 조사


def _eok(krw: int) -> str:
    tenths = (int(krw) + EOK // 20) // (EOK // 10)   # 0.1억 단위 정수 사사오입 — 예산 계산기 JS eokTxt 와 같은 규칙(Codex R3)
    return f"{tenths // 10}.{tenths % 10}억"


def _income_txt(krw: int) -> str:
    return f"{krw / EOK:g}억" if krw >= EOK else f"{krw // 10_000_000}천만"


# ── W: 닿는 가격 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReachablePrice:
    price_krw: int
    loan_krw: int
    binding: str        # "LTV" | "절대한도" | "스트레스 DSR"


def _loan_parts(price_krw: int, income_krw: int) -> tuple[tuple[str, int], ...]:
    dsr = fin.compute_dsr_loan(income_krw, (RATE_PCT + STRESS_PP) / 100, TERM_YEARS, DSR_LIMIT)
    return (("LTV", int(price_krw * LTV_REGULATED)), ("절대한도", fin.resolve_loan_abs_cap(price_krw)), ("스트레스 DSR", dsr))


def _required_equity(price_krw: int, income_krw: int, area_m2: float = TAX_AREA_M2) -> int:
    """필요자기자본(RequiredEquity) = 매매가 − 대출 + 취득세 + 중개보수. PRICE_STEP_KRW 격자 위에서는 가격에 대해 줄지 않는다
    (이분 탐색 전제, 0~100억 전수 테스트). 1원 단위로는 중개보수 요율 경계(5천만·2억)에서 줄 수 있다(Codex R1 P3)."""
    loan = min(v for _, v in _loan_parts(price_krw, income_krw))
    return (price_krw - loan + fin.compute_acquisition_tax(price_krw, False, area_m2)
            + fin.compute_broker_fee(price_krw))


def compute_reachable_price(equity_krw: int, income_krw: int, area_m2: float = TAX_AREA_M2) -> ReachablePrice:
    """자기자본으로 닿는 최대 가격(PRICE_STEP_KRW 단위). 필요자기자본이 자기자본 안에 드는 가장 큰 격자 가격.
    area_m2 는 취득세(농특세) 판정용 전용면적 — 기본은 85㎡ 이하 가정."""
    lo, hi = 0, _PRICE_MAX_STEPS
    if _required_equity(hi * PRICE_STEP_KRW, income_krw, area_m2) <= equity_krw:
        lo = hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _required_equity(mid * PRICE_STEP_KRW, income_krw, area_m2) <= equity_krw:
            lo = mid
        else:
            hi = mid
    price = lo * PRICE_STEP_KRW
    parts = _loan_parts(price, income_krw)
    loan = min(v for _, v in parts)
    return ReachablePrice(price, loan, next(name for name, v in parts if v == loan))


def count_reachable_complexes(rows: list[dict], price_krw: int) -> int:
    """닿는 단지 수(ReachableCount) — 12개월 실거래 중위가 있는 단지 중 중위 ≤ 닿는 가격."""
    return sum(1 for r in rows if r.get("molit_recent_eok") is not None
               and round(r["molit_recent_eok"] * EOK) <= price_krw)   # 9.47*1e8 = 947000000.0000001 (Codex R1)


def _priced(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("molit_recent_eok") is not None]


def build_reach_curves(rows: list[dict]) -> dict[int, list[tuple[float, int]]]:
    """연소득 [가정]별 (자기자본 억, 닿는 단지 수) 계단 — 그림 W 의 원자료."""
    return {inc: [(e, count_reachable_complexes(rows, compute_reachable_price(int(e * EOK), inc).price_krw))
                  for e in EQUITY_GRID_EOK] for inc in CURVE_INCOMES_KRW}


def find_cap_plateau(income_krw: int) -> tuple[float, float] | None:
    """닿는 가격이 15억에 머무는 자기자본 구간(격자 2점 이상) — 15억 초과 주택의 절대한도가 4억으로 줄어드는 자리."""
    flat = [e for e in EQUITY_GRID_EOK if compute_reachable_price(int(e * EOK), income_krw).price_krw == CAP_EDGE_KRW]
    return (flat[0], flat[-1]) if len(flat) >= 2 else None


def pick_title_equity(rows: list[dict]) -> tuple[int, int, int]:
    """(자기자본 억, 닿는 단지 수, 실거래 중위 단지 수) — 연소득 1억 [가정]에서 절반 이상에 닿는 가장 작은 정수 억(없으면 15)."""
    n = len(_priced(rows))
    for e in range(2, 16):
        k = count_reachable_complexes(rows, compute_reachable_price(e * EOK, TITLE_INCOME_KRW).price_krw)
        if 2 * k >= n:
            return e, k, n
    return 15, count_reachable_complexes(rows, compute_reachable_price(15 * EOK, TITLE_INCOME_KRW).price_krw), n


def build_lever_line(rows: list[dict], equity_eok: int) -> str:
    """레버 줄 — 같은 자리에서 자기자본 +1억과 연소득 +1천만원이 닿는 가격·단지 수를 얼마나 움직이나.
    가격이 그대로면 이유를 가른다: 15억 문턱(넘는 순간 한도 6억→4억)인지, LTV·절대한도가 대출을 묶은 것인지(Codex R1)."""
    here = compute_reachable_price(equity_eok * EOK, TITLE_INCOME_KRW)
    more_eq = compute_reachable_price((equity_eok + 1) * EOK, TITLE_INCOME_KRW)
    more_inc = compute_reachable_price(equity_eok * EOK, TITLE_INCOME_KRW + 10_000_000)
    k_eq, k_inc = (count_reachable_complexes(rows, r.price_krw) for r in (more_eq, more_inc))
    if more_inc.price_krw != here.price_krw:
        inc_txt = f"연소득 1천만원을 더하면 {(more_inc.price_krw - here.price_krw) / EOK:+.1f}억({k_inc}곳)"
    elif here.price_krw == CAP_EDGE_KRW:
        inc_txt = "연소득 1천만원을 더해도 그대로다(15억을 넘는 순간 대출 한도가 6억에서 4억으로 줄어드는 문턱이다)"
    elif here.binding in ("LTV", "절대한도"):
        inc_txt = f"연소득 1천만원을 더해도 그대로다({_SUBJECT[here.binding]} 먼저 묶는다)"
    else:
        inc_txt = "연소득 1천만원을 더해도 그대로다"
    return (f"같은 자리에서 자기자본 1억을 더하면 닿는 가격이 {(more_eq.price_krw - here.price_krw) / EOK:+.1f}억"
            f"({k_eq}곳), {inc_txt}.")


_POLITE_ENDINGS = (("있다", "있습니다"), ("나온다", "나옵니다"))


def _plain_binding_reason(binding: str, price_krw: int, polite: bool = False) -> str:
    """한도를 묶는 것(binding)을 쉬운 말로 — 절대한도는 가격 구간별 한도 금액으로 말한다. polite=True 는 '습니다' 체(계산기)."""
    if binding == "LTV":
        text = f"집값의 {LTV_REGULATED * 100:.0f}%까지만 빌릴 수 있다"
    elif binding == "절대한도":
        if price_krw > 25 * EOK:
            text = "25억이 넘는 집은 대출이 2억까지만 나온다"
        elif price_krw > CAP_EDGE_KRW:
            text = "15억이 넘는 집은 대출이 4억까지만 나온다"
        else:
            text = "15억 이하 집도 대출이 6억까지만 나온다"
    else:
        text = "연소득으로 갚을 수 있는 만큼(금리를 3%p 높여 잡은 심사 기준)만 빌릴 수 있다"
    if polite:
        for plain, formal in _POLITE_ENDINGS:
            if text.endswith(plain):
                return text[: -len(plain)] + formal
    return text


def policy_basis_text() -> str:
    """정책 근거 문장 — 구 원고 W 블록과 예산 계산기(reach.html)가 같은 문장을 쓴다."""
    return (f"{fin.FINANCE_CONFIRM_NOTICE} 정책 기준: 10.15 대책(LTV 40%·15억 이하 6억/15~25억 4억/25억 초과 2억·"
            f"스트레스 가산 {STRESS_PP:g}%p·만기 {TERM_YEARS}년), 확인일 {POLICY_CHECKED}. 취득세·중개보수 포함, 전용 85㎡ 이하 가정.")


def build_plain_reach_line(gu: str, rows: list[dict]) -> str:
    """한눈에보기(PlainAnswer) W 첫 줄 — [가정] 줄과 같은 자리·같은 숫자를 일반 독자 말로(DIKW 검토 2026-10-30 P0)."""
    e, k, n = pick_title_equity(rows)
    here = compute_reachable_price(e * EOK, TITLE_INCOME_KRW)
    return (f"쉽게 말해 연소득 1억에 모아둔 돈 {e}억(취득세·중개보수 포함)이면 대출 {_eok(here.loan_krw)}을 더해 "
            f"약 {_eok(here.price_krw)}짜리 집까지 닿는다 — {_plain_binding_reason(here.binding, here.price_krw)}. "
            f"{_label(gu)} 단지 {n}곳 중 {k}곳이 이 안이다. 실제 한도는 은행 심사로 정해진다.")


def build_wisdom_lines(gu: str, rows: list[dict]) -> list[str]:
    """W 줄 — 독자 자리 1곳(제목과 같은 자기자본)의 닿는 가격·한도를 묶는 것·레버, 15억 머묾 구간, 넣지 않은 규칙."""
    if not _priced(rows):
        return []
    label = _label(gu)
    e, k, n = pick_title_equity(rows)
    here = compute_reachable_price(e * EOK, TITLE_INCOME_KRW)
    lines = [build_plain_reach_line(gu, rows),
             f"[가정] 무주택·생애최초 아님·연소득 1억·대출금리 {RATE_PCT:.2f}%(스트레스 +{STRESS_PP:g}%p)·{TERM_YEARS}년이면 "
             f"자기자본 {e}억으로 닿는 가격은 약 {_eok(here.price_krw)}(대출 {_eok(here.loan_krw)}, 한도를 묶는 것은 "
             f"{here.binding}). {label} 단지별 대표 평형의 12개월 실거래 중위 {n}곳 중 {k}곳이 그 안이다(같은 단지의 다른 평형은 "
             f"따로 보지 않았다).",
             build_lever_line(rows, e)]
    plateau = find_cap_plateau(CURVE_INCOMES_KRW[-1])
    if plateau:
        k_cap = count_reachable_complexes(rows, CAP_EDGE_KRW)
        lines.append(f"주택 가격이 15억을 넘으면 대출 한도가 6억에서 4억(25억 초과는 2억)으로 줄어, 연소득 {_income_txt(CURVE_INCOMES_KRW[-1])}이면 "
                     f"자기자본 {plateau[0]:g}~{plateau[1]:g}억 구간에서 닿는 가격이 15억에 머문다 — "
                     f"{label}에서 이 구간에 닿는 단지는 {k_cap}곳으로 같다.")
    lines.append("서울 전역은 토지거래허가구역이라 취득 후 2년 실거주가 원칙이다(세입자가 있는 집은 요건을 갖추면 입주를 미루는 실거주 유예 "
                 "특례가 있다 — 일반 주택 신청 기한 2027-12-31, 일부 등록임대 아파트는 별도 기한). 이 계산은 즉시 입주하고 전세를 끼지 않는 경우다. "
                 "생애최초(LTV 70%)이거나 기존 대출이 있으면 결과가 달라진다.")
    for i, t in enumerate(lines):
        assert_lead_wording_ok(t, f"gu_dikw:wisdom:{gu}:{i}")
    return lines


# ── K: 가격을 가르는 축 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class PriceSplitAxis:
    axis: str
    buckets: tuple[tuple[str, int, int], ...]   # (구분, 평단가 중위 만원, n) — 자연 순서
    spread_man: int


def compute_price_split_axes(rows: list[dict]) -> list[PriceSplitAxis]:
    """구분별 평단가 중위의 최대−최소 차가 큰 순서(같으면 _AXES 순서). n<MIN_BUCKET_N 구분과 구분 2개 미만 축은 뺀다."""
    out = []
    for order, (axis, key, natural) in enumerate(_AXES):
        groups: dict[str, list[float]] = {}
        for r in rows:
            v, b = r.get("pyeong_price_man"), r.get(key)
            if v and b and b != "—":
                groups.setdefault(b, []).append(v)
        labels = [b for b in (natural or sorted(groups)) if len(groups.get(b, ())) >= MIN_BUCKET_N]
        if len(labels) < 2:
            continue
        buckets = tuple((b, int(round(statistics.median(groups[b]))), len(groups[b])) for b in labels)
        meds = [m for _, m, _ in buckets]
        out.append((order, PriceSplitAxis(axis, buckets, max(meds) - min(meds))))
    return [a for _, a in sorted(out, key=lambda t: (-t[1].spread_man, t[0]))]


def _axis_order(axes: list[PriceSplitAxis]) -> str:
    return " > ".join(f"{a.axis} {a.spread_man:,}만원" for a in axes)


def build_knowledge_lines(gu: str, rows: list[dict], all_rows: list[dict]) -> list[str]:
    """K 줄 — 이 구의 축 순서와 벌어진 두 구분, 서울 전체의 같은 순서, 원인이 아니라는 경계."""
    axes = compute_price_split_axes(rows)
    if not axes:
        return []
    label, top = _label(gu), axes[0]
    hi = max(top.buckets, key=lambda b: (b[1], b[0]))
    lo = min(top.buckets, key=lambda b: (b[1], b[0]))
    n = sum(1 for r in rows if r.get("pyeong_price_man"))
    lines = [f"{label} 발행 단지 중 평단가가 있는 {n}곳을 세 기준으로 나눠 평단가 중위를 견주면 구분 간 차이는 {_axis_order(axes)}. "
             f"{top.axis}에서 {hi[0]} {hi[1]:,}만원(n{hi[2]})과 {lo[0]} {lo[1]:,}만원(n{lo[2]})이 벌어진다."]
    seoul = compute_price_split_axes(all_rows)
    if seoul:
        same = [a.axis for a in seoul] == [a.axis for a in axes]
        lines.append(f"서울 전체(평단가가 있는 {sum(1 for r in all_rows if r.get('pyeong_price_man')):,}곳)에서는 {_axis_order(seoul)} — "
                     f"{label}의 순서는 서울과 {'같다' if same else '다르다'}.")
    lines.append("함께 나타나는 차이일 뿐 원인을 뜻하지 않는다 — 한 구분에 특정 동네·연식이 몰려 있으면 그 차이가 섞인다.")
    for i, t in enumerate(lines):
        assert_lead_wording_ok(t, f"gu_dikw:knowledge:{gu}:{i}")
    return lines


# ── 제목 실험 ───────────────────────────────────────────────────────────────

def assign_title_arm(gu: str, today: str, gus: list[str]) -> str:
    """TitleArm — 회전 바퀴 번호(14일) + 정렬한 구 목록에서의 자리 홀짝. 같은 구는 바퀴마다 번갈아든다.
    회전(select_rotation_gus)도 정렬 순서로 2구씩 자르므로 하루 2구는 1:1, 한 바퀴 25구는 13:12 로 갈린다
    (crc32 홀짝은 고정 11:14 집합이라 주마다 11:14 ↔ 14:11 로 출렁였다 — Codex R1).
    2026-10-26 주까지는 월요일 시작 주 번호였다. 바퀴 0 은 그 주 번호(짝수)와 홀짝이 같아, 구마다 회전 변경 뒤
    첫 글이 직전 주간 회전 글과 팔이 엇갈린다."""
    from blog.gu_tistory_post import rotation_cycle
    return "B" if (rotation_cycle(today) + sorted(gus).index(gu)) % 2 == 0 else "A"



@dataclass(frozen=True)
class TitleArmVerdict:
    verdict: str            # "A" | "B" | "판정 불가"
    click_effect: float     # B − A 클릭(14일 창), 주 사이 추세를 지운 값
    ctr_effect: float       # B − A CTR
    n_b_first: int          # B 가 먼저 나온 짝 수
    n_a_first: int


def judge_title_arms(records: list[dict]) -> TitleArmVerdict:
    """TitleArm 판정 — records 는 글마다 {gu, day(게시일 ISO), arm, impressions, clicks}(게시 후 같은 14일 창).
    같은 구의 이웃한 두 글(회전 한 바퀴 안, 팔이 다름)을 짝짓고, B 먼저 짝과 A 먼저 짝의 B−A 평균을 반씩 섞는다(교차 설계).
    주 사이 추세는 두 순서에 부호가 반대로 들어가 지워진다 — 구별 승패 과반·팔 합계 CTR 은 13:12 로 많은 쪽 순서에
    추세가 얹혀 효과가 없어도 B 를 고른다(Codex R2 반례). 노출 0 인 글이 낀 짝은 뺀다. 클릭·CTR 효과가 모두 0 보다 커야 B."""
    from blog.gu_tistory_post import GU_CYCLE_DAYS
    by_gu: dict[str, list[dict]] = {}
    for r in sorted(records, key=lambda r: (r["gu"], r["day"])):
        by_gu.setdefault(r["gu"], []).append(r)
    diffs: dict[str, list[tuple[int, float]]] = {"B": [], "A": []}
    for posts in by_gu.values():
        i = 0
        while i + 1 < len(posts):
            first, second = posts[i], posts[i + 1]
            gap = date.fromisoformat(second["day"]) - date.fromisoformat(first["day"])
            if gap.days > GU_CYCLE_DAYS or first["arm"] == second["arm"]:
                i += 1
                continue
            b, a = (first, second) if first["arm"] == "B" else (second, first)
            if a["impressions"] and b["impressions"]:
                diffs[first["arm"]].append((b["clicks"] - a["clicks"],
                                            b["clicks"] / b["impressions"] - a["clicks"] / a["impressions"]))
            i += 2
    n_b, n_a = len(diffs["B"]), len(diffs["A"])
    if not n_b or not n_a:
        return TitleArmVerdict("판정 불가", 0.0, 0.0, n_b, n_a)
    click, ctr = ((statistics.fmean(d[k] for d in diffs["B"]) + statistics.fmean(d[k] for d in diffs["A"])) / 2
                  for k in (0, 1))
    return TitleArmVerdict("B" if click > 0 and ctr > 0 else "A", click, ctr, n_b, n_a)

def build_situation_title(gu: str, rows: list[dict], today: str, asof: str) -> str:
    e, k, n = pick_title_equity(rows)
    stamp = f'국토부 {asof} 기준' + ('' if asof == today else f' ({today})')
    return f"{_label(gu)} 아파트, 자기자본 {e}억·연소득 1억이면 닿는 단지는? 대표 평형 중위로 {n}곳 중 {k}곳｜{stamp}"


# ── 그림 ────────────────────────────────────────────────────────────────────

_PAD = 48
_INK, _MUTED, _GRID = mch._INK, mch._MUTED, mch._GRID
_TOP, _OTHER, _SEOUL = mch._SALE, mch._BAR, mch._JEONSE
_INCOME_COLORS = (mch._BASE, mch._SALE, mch._RATE)
_SOURCE = ("출처 국토교통부 실거래가(12개월 동일평형 중위)", "정리 서울 아파트 실거래 데이터랩")
_INNER = CHART_WIDTH - 2 * _PAD


def _canvas(height: int):
    from blog.thumbnail_card import _resolve_font
    return mch._Canvas(CHART_WIDTH, height, _resolve_font(None))


def _fit(c, xy, value: str, size: int, fill, *, bold=False, anchor="la", width: float = _INNER) -> None:
    """폭을 넘으면 글자를 줄인다(최소 20) — 구 이름·단지 수 길이가 달라도 오른쪽이 잘리지 않게."""
    while size > 20 and c.width_of(value, size, bold) > width:
        size -= 1
    c.text(xy, value, size, fill, bold=bold, anchor=anchor)


def _boxed(c, xy, value: str, size: int, fill, *, bold=False) -> None:
    """흰 바탕 위 글자(왼쪽 가운데 기준) — 선·점선과 겹쳐도 읽히게."""
    w = c.width_of(value, size, bold)
    c.rect((xy[0] - 4, xy[1] - size * 0.7, xy[0] + w + 4, xy[1] + size * 0.7), (255, 255, 255))
    c.text(xy, value, size, fill, bold=bold, anchor="lm")


def _footer(c, y: int, extra: str = "") -> None:
    for i, line in enumerate(_SOURCE + ((extra,) if extra else ())):
        _fit(c, (_PAD, y + i * 36), line, 24, _MUTED)


def render_axis_png(gu: str, axes: list[PriceSplitAxis], seoul_median_man: int, today: str) -> bytes:
    """K 그림 — 축마다 구분별 평단가 중위 막대(자연 순서). 차이가 큰 축이 위, 서울 중위는 점선."""
    row_h, head_h, gap = 44, 54, 22
    height = 180 + sum(head_h + row_h * len(a.buckets) + gap for a in axes) + 126
    c = _canvas(height)
    _fit(c, (_PAD, 30), f"{_label(gu)} 평단가를 가르는 축", 44, _INK, bold=True)
    _fit(c, (_PAD, 92), "구분별 평단가 중위(만원/3.3㎡) · n≥3 구분만", 26, _MUTED)
    _fit(c, (_PAD, 128), f"차이가 큰 축이 위 · 점선 = 서울 중위 {seoul_median_man:,}만원", 26, _MUTED)
    x0, x1 = _PAD + 150, CHART_WIDTH - _PAD - 170
    vmax = max([m for a in axes for _, m, _ in a.buckets] + [seoul_median_man])
    xs = lambda v: x0 + (x1 - x0) * v / vmax
    y = 180
    bottom_y = y + sum(head_h + row_h * len(a.buckets) + gap for a in axes) - gap
    c.line([(xs(seoul_median_man), y), (xs(seoul_median_man), bottom_y)], _SEOUL, 2, dash=(8, 6))
    for i, a in enumerate(axes):
        _boxed(c, (_PAD, y + 24), f"{a.axis} · 구분 간 차이 {a.spread_man:,}만원", 30 if i == 0 else 28, _INK, bold=i == 0)
        y += head_h
        for b, m, n in a.buckets:
            c.text((_PAD, y + row_h / 2), b, 26, _INK, anchor="lm")
            c.rect((x0, y + 8, xs(m), y + row_h - 8), _TOP if i == 0 else _OTHER)
            _boxed(c, (xs(m) + 10, y + row_h / 2), f"{m:,}만 (n{n})", 26, _INK)
            y += row_h
        y += gap
    _footer(c, y + 10, f"기준 {today}")
    return c.png()


def render_reach_png(gu: str, curves: dict[int, list[tuple[float, int]]], n: int, mark: tuple[int, int],
                     plateau: tuple[float, float] | None, today: str) -> bytes:
    """W 그림 — 자기자본(가로)별 닿는 단지 수(세로) 계단, 연소득 [가정] 3선. 표시점 = 제목의 자리, 회색 띠 = 15억 머묾 구간."""
    plot_top, plot_h = 230, 380
    height = plot_top + plot_h + 70 + 100 + 126
    c = _canvas(height)
    _fit(c, (_PAD, 30), f"{_label(gu)} 자기자본별 닿는 단지 수", 44, _INK, bold=True)
    _fit(c, (_PAD, 92), f"단지별 대표 평형 12개월 실거래 중위 기준 · 전체 {n}곳", 26, _MUTED)
    _fit(c, (_PAD, 128), f"[가정] 무주택·금리 {RATE_PCT:.2f}%+스트레스 {STRESS_PP:g}%p·{TERM_YEARS}년", 26, _MUTED)
    _fit(c, (_PAD, 164), f"LTV 40%·절대한도 6/4/2억 · 정책 확인일 {POLICY_CHECKED}", 26, _MUTED)
    left, right, bottom = _PAD + 64, CHART_WIDTH - _PAD, plot_top + plot_h
    e0, e1 = EQUITY_GRID_EOK[0], EQUITY_GRID_EOK[-1]
    xs = lambda e: left + (right - left) * (e - e0) / (e1 - e0)
    ticks = mch._nice_ticks(0, max(n, 1))
    ys = lambda v: bottom - plot_h * (v - ticks[0]) / (ticks[-1] - ticks[0])
    if plateau:
        c.rect((xs(plateau[0]), plot_top, xs(plateau[1]), bottom), (241, 245, 249))
        c.text(((xs(plateau[0]) + xs(plateau[1])) / 2, plot_top + 6), "15억에 머묾", 22, _MUTED, anchor="ma")
    for t in ticks:
        c.line([(left, ys(t)), (right, ys(t))], _GRID, 1)
        c.text((left - 10, ys(t)), f"{t:g}", 24, _MUTED, anchor="rm")
    for e in range(1, 16, 2):
        c.text((xs(e), bottom + 12), f"{e}억", 24, _MUTED, anchor="ma")
    for inc, color in sorted(zip(CURVE_INCOMES_KRW, _INCOME_COLORS), key=lambda t: t[0] == TITLE_INCOME_KRW):
        pts = curves[inc]
        path = [(xs(pts[0][0]), ys(pts[0][1]))]
        for (_, ka), (eb, kb) in zip(pts, pts[1:]):
            path += [(xs(eb), ys(ka)), (xs(eb), ys(kb))]
        c.line(path, color, 4 if inc == TITLE_INCOME_KRW else 3)
    mx, my = xs(mark[0]), ys(mark[1])
    c.dot((mx, my), 9, _INK)
    c.dot((mx, my), 5, (255, 255, 255))
    ly = bottom + 60
    lx = _PAD
    for inc, color in zip(CURVE_INCOMES_KRW, _INCOME_COLORS):
        c.line([(lx, ly + 14), (lx + 36, ly + 14)], color, 4)
        label = f"연소득 {_income_txt(inc)}"
        c.text((lx + 44, ly + 14), label, 24, _INK, anchor="lm")
        lx += 44 + c.width_of(label, 24) + 28
    c.dot((_PAD + 10, ly + 58), 9, _INK)
    c.dot((_PAD + 10, ly + 58), 5, (255, 255, 255))
    _fit(c, (_PAD + 30, ly + 58), f"연소득 1억·자기자본 {mark[0]}억이면 {mark[1]}곳 — 아래 계산의 자리", 26, _INK, bold=True,
         anchor="lm", width=_INNER - 30)
    _footer(c, ly + 100, f"기준 {today}")
    return c.png()


def _write_png(payload: bytes, prefix: str, today: str, images_dir: str | Path) -> mch.DashboardImage:
    from blog.thumbnail_card import _pillow, _write_card_bytes
    digest = hashlib.sha256(payload).hexdigest()
    name = f"{prefix}-{today}-{digest[:16]}.png"
    destination = _write_card_bytes(Path(images_dir).resolve(), name, payload)
    image_cls = _pillow()[0]
    with image_cls.open(io.BytesIO(payload)) as im:
        w, h = im.size
    return mch.DashboardImage(str(destination), name, digest, w, h)


def write_dikw_images(gu: str, rows: list[dict], all_rows: list[dict], today: str,
                      images_dir: str | Path) -> list[mch.DashboardImage]:
    """[W 그림, K 그림] 순서(본문 순서와 같다). 그릴 원자료가 없는 그림은 빠진다. 이름에 내용 해시."""
    out = []
    priced = _priced(rows)
    if priced:
        e, k, n = pick_title_equity(rows)
        payload = render_reach_png(gu, build_reach_curves(rows), n, (e, k), find_cap_plateau(CURVE_INCOMES_KRW[-1]), today)
        out.append(_write_png(payload, "gu-reach", today, images_dir))
    axes = compute_price_split_axes(rows)
    if axes:
        seoul = [r["pyeong_price_man"] for r in all_rows if r.get("pyeong_price_man")]
        payload = render_axis_png(gu, axes, int(round(statistics.median(seoul))), today)
        out.append(_write_png(payload, "gu-axis", today, images_dir))
    return out


def body_media_entries(images: list[mch.DashboardImage], base_url: str) -> list[dict]:
    return [{"url": f"{base_url}/images/{i.name}", "image": f"../images/{i.name}", "sha256": i.sha256,
             "width": i.width, "height": i.height} for i in images]


# ── 본문 블록 ───────────────────────────────────────────────────────────────

def _img(image: mch.DashboardImage, base_url: str, alt: str) -> str:
    return (f'<p><img src="{base_url}/images/{image.name}" width="{image.width}" height="{image.height}" '
            f'alt="{html.escape(alt)}"></p>')


def build_dikw_block(gu: str, rows: list[dict], all_rows: list[dict], today: str, asof: str,
                     images: list[mch.DashboardImage], base_url: str, mut_style: str) -> dict:
    """{"html", "title", "images"} — W(판단 도구)·K(지식) 순서. title 은 B 팔일 때만 상황형 제목, A 팔이면 None."""
    label = _label(gu)
    by_prefix = {i.name.split("-")[1]: i for i in images}
    w_lines, k_lines = build_wisdom_lines(gu, rows), build_knowledge_lines(gu, rows, all_rows)
    out, used = "", []
    if w_lines and "reach" in by_prefix:
        e, k, n = pick_title_equity(rows)
        out += (f'<p><b>{label}, 내 자기자본이면 어디까지 닿나</b> <span style="{mut_style}">(공개 대출 규칙으로 계산한 [가정] 시뮬레이션)</span></p>'
                + _img(by_prefix["reach"], base_url, f"{label} 자기자본별 닿는 단지 수 계단 그래프 — 연소득 7천만·1억·1.5억 가정, "
                                                     f"자기자본 {e}억이면 실거래 {n}곳 중 {k}곳")
                + "<p>" + "<br>".join(f"• {html.escape(t)}" for t in w_lines) + "</p>"
                + f'<p><a href="{base_url}/reach.html">내 조건(모아둔 돈·연소득·기존 대출)으로 서울 전체 직접 계산해 보기 →</a></p>'
                + f'<p style="{mut_style}">{html.escape(policy_basis_text())}</p>')
        used.append(by_prefix["reach"])
    if k_lines and "axis" in by_prefix:
        out += (f'<p><b>{label} 평단가를 가르는 축</b> <span style="{mut_style}">(세대수대·준공연대·전용대별 중위 비교)</span></p>'
                + _img(by_prefix["axis"], base_url, f"{label} 구분별 평단가 중위 막대 그래프 — {k_lines[0][:60]}")
                + "<p>" + "<br>".join(f"• {html.escape(t)}" for t in k_lines) + "</p>")
        used.append(by_prefix["axis"])
    gus = sorted({r["gu"] for r in all_rows})
    title = build_situation_title(gu, rows, today, asof) if w_lines and assign_title_arm(gu, today, gus) == "B" else None
    return {"html": out, "title": title, "images": used}
