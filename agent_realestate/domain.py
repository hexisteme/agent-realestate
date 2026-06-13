"""도메인 모델 — 가드레일을 *타입으로* 강제 (agent_money/domain.py 와 동형).

작명은 ~/.claude/rules/glossary-real-estate.md (AppraisalValue, LoanHeadroom,
ListingPrice, JeonseDeposit, EquityGap …) 를 따른다.

강제되는 가드 (관례가 아니라 타입/생성자 검증으로):
  G1 추정매물 차단 — Listing 은 4요소(동·호/평형/층/향, 호가, 중개사, 확인일) 가 없으면
     생성 불가. price_kind==ASKING_LIVE 는 source==NAVER_LIVE_CHROME 일 때만 허용.
     출처 없는 값은 PriceKind.ASKING_LIVE 로 승격 불가 → 리포트 §3 진입 불가.
     (RDU-061: 추정 호가 금지 = '출처·확인일 없는 추정값 금지', 라이브 자체는 허용.)
  G2 의도 오독 차단 — ExitStrategy 는 필수. HOLD_AND_RENT 면 매도/양도세 모듈 비활성.
  태그 — 모든 Claim 에 Provenance(FACT/INFERENCE/ASSUMPTION). FACT 는 evidence 필수.
  staleness — Listing.is_stale(today, days) 로 확인일 경과 경고.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Provenance(Enum):
    """주장의 출처 종류. 사실/추론/가정을 강제 분리."""

    FACT = "사실"        # 측정값 또는 URL·확인일로 출처가 명시된 것
    INFERENCE = "추론"   # 가설 — 사실로 단정하지 않음
    ASSUMPTION = "가정"  # 입력·추정 — 검증 전. 리포트에서 별도 라벨


class DataSource(Enum):
    """데이터가 어디서 왔는가. ASKING_LIVE 승격의 게이트."""

    NAVER_LIVE_CHROME = "네이버부동산 라이브"   # read-chrome-tab.sh 로 직접 추출
    MOLIT_API = "국토부 실거래가"
    KB_SISE = "KB시세"
    POLICY_SCRAPER = "정책 스냅샷"
    AGENT_CALC = "에이전트 계산"
    USER_INPUT = "사용자 입력"
    WEB_SEARCH = "웹검색(보조)"               # §3 진입 불가, 컨텍스트 보조용만


class PriceKind(Enum):
    """가격의 종류. 호가 ≠ 실거래 ≠ KB시세 를 타입으로 구분 (RDU-061)."""

    ASKING_LIVE = "현재 호가"      # 네이버 라이브 매물 호가 — §3 메인 기준 (유일)
    TRANSACTION_REAL = "실거래가"  # 참고 컬럼
    JEONSE_LIVE = "전세 호가"
    KB_AVG = "KB평균시세"          # 보조


class ExitStrategy(Enum):
    """출구 전략 — 필수 입력(G2). 분석 분기의 1차 스위치."""

    HOLD_AND_RENT = "보유+임대"    # 매도 안 함 → 장기보유 전세수익률·재건축·보유세 활성, 양도세 비활성
    LIVE_THEN_SELL = "거주후매도"  # 매도 손익분기·양도세(비과세 특례) 활성
    PRIMARY_ONLY = "실거주전용"    # 실거주 품질 위주


class RedevStage(Enum):
    """재건축/재개발 진행 단계 (낮을수록 초기, 토지지분 실현 시계 김)."""

    NONE = ("없음", 0)
    SAFETY_PASS = ("안전진단통과", 1)
    PROMOTION = ("정비구역지정/추진위", 2)
    UNION_SETUP = ("조합설립인가", 3)
    PROJECT_PLAN = ("사업시행인가", 4)
    MGMT_DISPOSAL = ("관리처분인가", 5)
    MOVE_OUT = ("이주/철거", 6)

    def __init__(self, label: str, level: int):
        self.label = label
        self.level = level

    @property
    def blocks_residence(self) -> bool:
        """관리처분인가(5)·이주/철거(6) 단계는 곧 이주 → 1년 실거주·임대 불가(입주권 성격)."""
        return self.level >= 5


@dataclass(frozen=True)
class Listing:
    """매물 한 건. 4요소가 없으면 생성 불가 (G1)."""

    complex_name: str
    dong_ho: str            # "203동 906호"
    area_exclusive_m2: float
    floor: str              # "9/15층"
    facing: str             # "남향"
    price_krw: int
    price_kind: PriceKind
    agent_name: str
    confirmed_date: date
    source: DataSource

    def __post_init__(self) -> None:
        # G1: 4요소 누락 차단
        for f in ("complex_name", "dong_ho", "floor", "facing", "agent_name"):
            if not getattr(self, f):
                raise ValueError(f"Listing 4요소 누락: {f} (추정매물 차단 G1)")
        if self.area_exclusive_m2 <= 0 or self.price_krw <= 0:
            raise ValueError("Listing 면적/호가 비정상 (G1)")
        # G1: 현재 호가는 네이버 라이브에서만 승격 허용
        if self.price_kind is PriceKind.ASKING_LIVE and self.source is not DataSource.NAVER_LIVE_CHROME:
            raise ValueError(
                "ASKING_LIVE 는 NAVER_LIVE_CHROME 출처만 허용 — "
                "웹검색/추정 호가는 §3 진입 불가 (RDU-061)"
            )

    @property
    def pyeong(self) -> float:
        return round(self.area_exclusive_m2 / 3.305785, 1)

    def is_stale(self, today: date, days: int = 14) -> bool:
        return (today - self.confirmed_date).days > days

    def label_4(self) -> str:
        return f"{self.complex_name} {self.dong_ho} / 전용{self.area_exclusive_m2:.0f}㎡ / {self.floor} / {self.facing} / {self.price_krw/1e8:.2f}억 / {self.agent_name} / {self.confirmed_date:%y.%m.%d}"


@dataclass(frozen=True)
class PolicyFact:
    """정책 사실 — 출처 URL + 확인일 필수 (RDU-059). 없으면 생성 불가."""

    statement: str
    url: str
    confirmed_date: date
    law_ref: str = ""       # 예: "소득세법 시행령 §154"

    def __post_init__(self) -> None:
        if not self.url or not self.statement:
            raise ValueError("PolicyFact 는 statement + url 필수 (RDU-059)")


@dataclass(frozen=True)
class Claim:
    """리포트의 한 줄. 모든 줄은 Provenance 를 가진다."""

    text: str
    provenance: Provenance
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.provenance is Provenance.FACT and not self.evidence_ids:
            raise ValueError(f"FACT 주장에 근거(evidence) 누락: {self.text!r}")

    def render(self) -> str:
        return f"[{self.provenance.value}] {self.text}"


@dataclass(frozen=True)
class Candidate:
    """평가 대상 단지+매물 묶음."""

    listing: Listing
    units: int                  # 세대수
    built_year: int
    far_pct: float              # 용적률 %
    land_share_pyeong: float    # 대지지분(평) — 추정이면 provenance 별도 표기
    land_share_is_estimate: bool
    redev_stage: RedevStage
    jeonse_krw: int | None      # 전세 호가 (JEONSE_LIVE)
    transit: str                # 입지 한 줄 (역·노선·호재)
    district: str               # 자치구
    broker_count: int = 1       # R3: 동일 매물 노출 중개사 수 (≥3 진위강, 1 단독=약)
    regulated: bool = True      # 규제지역(투기과열/조정) 여부 → LTV 분기. 서울=True, 대구 수성구(비규제)=False
    cbd_km: float | None = None # 주요 업무지구 최단거리(km) — 출퇴근접근성 축. 서울=강남/시청/여의도, 대구=동대구/반월당
    cbd_name: str = ""          # 최근접 업무지구명
    hakgun_score: float | None = None  # 학군 [추론] proxy(0~5) — 명문중·학원가 근접+intel 정성. 학업성취 측정 아님(사용자 (b) 선택으로 점수축 편입, 2026-05-30)
    slope_pct: float | None = None     # 경사도(%) [사실] — opentopodata 실측. 경사 축(2026-05-31 편입)
    academy_exam: int | None = None    # 입시학원 수(학원가 밀집 proxy) — 학군 축에 통합(2026-05-31)
    review_score: float | None = None  # 커뮤니티 후기 coarse 감성(0~5) — ★편향·단일출처, 후기 축 0.05 경가중(2026-05-31)
    saenghwalgwon: str = ""             # 생활권 키(compset base-rate 조회용) — 단지명 충돌 방지로 *후보가 직접 보유*(2026-06-02)
    # ── 3축 정량 재설계 보강데이터(2026-06-04) — 상승여력·가격방어 산출 입력 ──
    bcr_pct: float | None = None       # 건폐율 %(네이버 단지정보 DOM) — 토지지분·재건축잠재 보조(낮을수록 개방·증축여지↑, 2026-06-04)
    infra: dict | None = None          # KAKAO 인프라 {subway_m, mart_800, hosp_800, park_1k, dept_1500} — 상승여력 인프라성분
    trade_annual: float | None = None  # MOLIT 연평균 실거래수(단지) — 가격방어 유동성성분(미수집 시 구중위 대체)
    gu_jeonse_ratio: float | None = None  # 그 자치구 전세가율 중위(전세÷매매) — 가격방어 전세지지 상대비교 기준
    gu_cagr: float | None = None       # 그 자치구 15년 base-rate CAGR(%) — 상승여력 구승수(미래상승률 가중)
    # ── 자체 학군 알고리즘 입력(2026-06-05) — 학교알리미 무변별(진학률 99%) 대체 ──
    tukmokgo_pct: float | None = None  # 배정/최근접 중학교의 특목·자사·영재고 진학률 %([추론] aggregator 가공, 원자료 학교알리미 5월공시) — 학군 최강 변별신호
    school_achievement: float | None = None  # 국가수준 학업성취도 보통학력이상 평균 %([추론] aggregator, 원자료 학교알리미) — 보조
    gu_ipsi_academy: int | None = None  # 그 자치구 입시.검정 및 보습 학원 수(개원)([사실] 서울 OpenAPI neisAcademyInfo) — 구 학군강도(양천1421 vs 종로96)
    dev_catalyst: str | None = None     # 외부 개발호재(인근 대형부지 개발 등) — binary 촉매 watchlist용, 출처 필수. 점수 미반영(decision_prior 촉매). 2026-06-07
    redev_infeasible: bool = False       # 재개발 난망(고밀·신축·인근 불가 등) — True 면 토지지분 realizability haircut + 재건축잠재 0. opt-in(기본 False). 2026-06-07
    toher_zone: bool = False             # 토지거래허가구역 지정 여부 — 강남4구·목동·성수·잠실 등 지정구역(서울 전역 아님).
                                         # True 면 HOLD_AND_RENT 시 F_TOHER_RENT soft flag(2년 실거주의무·즉시갭 불가).
                                         # land.seoul.go.kr 로 구역 확인 후 입력. opt-in(기본 False). 2026-06-13.

    def __post_init__(self) -> None:
        # R4: 입력 sanity — 비논리/오타 차단 (수동주입 신뢰성 보강)
        if not (1900 <= self.built_year <= 2100):
            raise ValueError(f"준공연도 비정상: {self.built_year} (R4 sanity)")
        if not (0 < self.far_pct <= 2000):
            raise ValueError(f"용적률 비정상: {self.far_pct}% (R4 sanity)")
        if self.land_share_pyeong < 0:
            raise ValueError("대지지분 음수 (R4 sanity)")
        if self.jeonse_krw is not None:
            if self.jeonse_krw <= 0:
                raise ValueError("전세 호가 비정상(≤0) (R4 sanity)")
            if self.jeonse_krw > self.listing.price_krw:
                raise ValueError(
                    f"전세({self.jeonse_krw/1e8:.2f}억) > 매매({self.listing.price_krw/1e8:.2f}억) "
                    "— 매수 분석상 비논리(입력 오타 의심, R4)")
