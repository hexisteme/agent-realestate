"""agent-realestate CLI.

  report        --profile p.json --input candidates.json [--out path]
  scan-policy   --input policies.json     정책 사실(§2) + 선택적 param(① LTV율) 캐시
  update-redev  --input redev.json        재건축 단계 캐시
  update-prices --input prices.json       MOLIT 실거래 시계열 캐시 (② §1 추세)
  update-land   --input land.json         등기부 대지지분 실측 캐시 (③)
  backfill                                (placeholder)
  doc-sync                                capability 상수 → AGENT_CAPABILITIES auto-capabilities 재생성

candidates/profile/policies/prices/land JSON 은 Claude 가 라이브 수집/WebSearch 후 구조화해
주입(agent_money MCP-주입 패턴). report 는 결정론 계산만 한다 (G3).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

from . import config
from .analysts.finance import build_finance_plan
from .analysts.redev import score_redev
from .analysts.risk import assess_flags, penalty_product, ranking_key, RiskFlag
from .analysts.scoring import merge_axis_weights, score_candidates
from .analysts.sensitivity import sensitivity_analysis
from .analysts.trend import compute_trend
from .cache import store
from .collectors import molit
from .collectors.naver_live import load_candidates
from .domain import ExitStrategy
from .notify.email_report import compose_summary, send_report
from .notify.telegram import notify_daily_result, notify_step_failure
from .policy_params import PolicyParams
from .synthesis.assembler import Evaluated, build_report
from .synthesis.scenario import compute_break_even, compute_hold


def _strategy(profile: dict) -> ExitStrategy:
    raw = profile.get("exit_strategy")
    if not raw:
        sys.exit("[G2] exit_strategy 필수 (HOLD_AND_RENT|LIVE_THEN_SELL|PRIMARY_ONLY)")
    try:
        return ExitStrategy[raw]
    except KeyError:
        sys.exit(f"[G2] 알 수 없는 exit_strategy: {raw}")


def _resolve_ltv(conn, profile: dict) -> tuple[float, str]:
    """① LTV율을 정책캐시(policy_param)에서 가져온다. 프로필 명시 override 우선,
    캐시 없으면 기본값 + [가정] 라벨."""
    if "ltv_ratio" in profile:
        return float(profile["ltv_ratio"]), f"{profile['ltv_ratio']*100:.0f}% (프로필 지정)"
    first = profile.get("first_time", True)
    regulated = profile.get("regulated", True)  # 서울 기본 규제지역
    key = ("ltv_first_regulated" if first and regulated else
           "ltv_first_nonreg" if first else "ltv_general")
    p = store.get_param(conn, key)
    if p:
        return float(p["value"]), f"{p['value']*100:.0f}% ([사실] {key}, 출처 {p['url']} {p['confirmed_date']})"
    return 0.70, "[가정] 70% — 정책캐시 없음, scan-policy 로 LTV param 주입 권장"


def _apply_land_override(conn, c):
    """③ 등기부 대지지분 실측이 있으면 추정값을 실측(FACT)으로 교체."""
    m = store.get_land(conn, c.listing.complex_name, c.listing.area_exclusive_m2)
    if m:
        return dataclasses.replace(c, land_share_pyeong=m["land_share_pyeong"],
                                   land_share_is_estimate=False)
    return c


def produce_report(*, profile: dict, input_path: str, insight: str | None = None,
                   council_session: str | None = None, council_models=None,
                   out: str | None = None, no_email: bool = True,
                   email: str | None = None, location_path: str | None = None,
                   reviews_path: str | None = None, structural_path: str | None = None,
                   cagr_path: str | None = None, compset_path: str | None = None,
                   coverage_path: str | None = None,
                   reference_path: str | None = None,
                   trend_gap_path: str | None = None) -> dict:
    """결정론 §0~11 리포트 생성 코어 — CLI(cmd_report)와 버스 워커(bus.run_task)가 공유.

    라이브러리-안전: 빈 후보는 ValueError 로 드러낸다(추정 금지, RDU-061). out_path·순위
    요약을 dict 로 반환해 호출자가 Result/v1 claims 로 흡수할 수 있게 한다."""
    config.assert_mount()
    strategy = _strategy(profile)
    conn = store.connect()
    candidates = [_apply_land_override(conn, c) for c in load_candidates(input_path)]
    # ★고정 기준집합(reference) — 가격메리트·공백평균을 이 집합으로 산출(동일 단지 점수 통일, v3)
    reference = ([_apply_land_override(conn, c) for c in load_candidates(reference_path)]
                 if reference_path else None)
    if not candidates:
        raise ValueError("후보 0건 — 라이브 수집 실패 시 추정으로 채우지 않음 (RDU-061)")

    ltv_ratio, ltv_note = _resolve_ltv(conn, profile)

    def _ltv_for(c) -> float:
        # 후보별 LTV — 규제(서울 70%) vs 비규제(대구 수성구 생애최초 80%). 프로필 override 우선.
        if "ltv_ratio" in profile:
            return float(profile["ltv_ratio"])
        first = profile.get("first_time", True)
        reg = getattr(c, "regulated", True)
        key = ("ltv_first_regulated" if first and reg else "ltv_first_nonreg" if first else "ltv_general")
        p = store.get_param(conn, key)
        return float(p["value"]) if p else (0.70 if reg else 0.80)

    params = PolicyParams.from_cache(conn)          # P0-2: 세율·공시가율 정책캐시
    num_homes = int(profile.get("num_homes", 1))
    # R6: 기존 부채 슬롯 세분화 — existing_loans[{type,annual_payment_krw}] 합산 → DSR
    _loans = profile.get("existing_loans")
    existing_debt = (sum(int(l.get("annual_payment_krw", 0)) for l in _loans) if _loans
                     else int(profile.get("existing_annual_debt_krw", 0)))
    rate = profile.get("mortgage_rate", config.DEFAULT_MORTGAGE_RATE)
    term = profile.get("term_years", config.DEFAULT_MORTGAGE_TERM_YEARS)
    today = date.today()
    # 후보별 전략 — profile["mixed_strategy"]={"regulated":..,"nonreg":..} 면 지역별 혼합 평가
    # (서울 임대 HOLD_AND_RENT / 대구 실거주 PRIMARY_ONLY)를 단일 통합 순위로 비교(2026-05-30).
    _mix = profile.get("mixed_strategy")

    def strat_for(c) -> ExitStrategy:
        if not _mix:
            return strategy
        return ExitStrategy[_mix["regulated"] if getattr(c, "regulated", True) else _mix["nonreg"]]

    strats = [strat_for(c) for c in candidates] if _mix else None
    redevs = [score_redev(c) for c in candidates]
    # ★범용화(2026-06-12): profile["axis_weights"] 부분 override (병합+재정규화). mixed_strategy 와
    #   동시 사용 시 모든 후보에 동일 적용되므로 단일 전략에서 권장.
    eff_weights = merge_axis_weights(strategy, profile.get("axis_weights"))
    axes = score_candidates(candidates, redevs, strategy, weights=eff_weights,
                            strategies=strats, reference_candidates=reference)

    evaluated: list[Evaluated] = []
    for c, r, a in zip(candidates, redevs, axes):
        cstrat = strat_for(c)
        fin = build_finance_plan(
            price_krw=c.listing.price_krw, ltv_ratio=_ltv_for(c),
            annual_income_krw=profile["annual_income_krw"], own_capital_krw=profile["own_capital_krw"],
            rate=rate, term_years=term, first_time=profile.get("first_time", True),
            area_exclusive_m2=c.listing.area_exclusive_m2,
            dsr_limit=profile.get("dsr_limit", config.DEFAULT_DSR_LIMIT),
            existing_annual_debt_krw=existing_debt,
            broker_fee_rate=profile.get("broker_fee_rate", config.DEFAULT_BROKER_FEE_RATE),
            params=params, num_homes=num_homes,
            # 스트레스 DSR 하한: 수도권·규제지역 주담대 3%(10.15 대책, 6.27→상향) / 비규제 1.5% — 후보별
            stress_addon=(0.03 if getattr(c, "regulated", True) else 0.015),
            # 수도권/규제 주담대: DSR 만기 30년 cap(6.27) + 절대한도 6억(6.27·10.15) — regulated 만 적용
            dsr_term_cap=(30 if getattr(c, "regulated", True) else None),
            loan_abs_cap_krw=(600_000_000 if getattr(c, "regulated", True) else None),
        )
        trend = compute_trend(store.get_price_series(conn, c.listing.complex_name, c.listing.area_exclusive_m2))
        hold = brk = None
        if cstrat is ExitStrategy.HOLD_AND_RENT:
            mgmt = int(params.management_fee_per_pyeong_month * 12 * c.listing.pyeong)
            maint = int(c.listing.price_krw * params.maintenance_pct)
            hold = compute_hold(
                price_krw=c.listing.price_krw, loan_krw=fin.loan_krw,
                jeonse_krw=c.jeonse_krw or 0, property_tax_krw=fin.property_tax_krw,
                rate=rate, comprehensive_tax_krw=fin.comprehensive_tax_krw,
                management_fee_annual_krw=mgmt, maintenance_annual_krw=maint,
                monthly_rent_krw=int(profile.get("monthly_rent_krw", 0)),
                vacancy_pct=params.vacancy_pct,
                # 점추정 폐기(council 20260529): 회귀 95%CI 밴드 주입. 추세 약함/없음이면 band=None
                # → compute_hold 가 보수 중립 밴드로 대체. 미래 행은 [가정] 조건부.
                growth_band=(trend.band if trend else None),
            )
        elif cstrat is ExitStrategy.LIVE_THEN_SELL:
            brk = compute_break_even(price_krw=c.listing.price_krw, acquisition_tax_krw=fin.acquisition_tax_krw,
                                     broker_fee_krw=fin.broker_fee_krw, annual_interest_krw=fin.annual_interest_krw,
                                     property_tax_krw=fin.property_tax_krw, equity_krw=fin.equity_required_krw,
                                     years=1, opportunity_rate=config.OPPORTUNITY_RATE,
                                     is_one_home=(num_homes == 1),
                                     resident_years=float(profile.get("resident_years", 1)))
        flags = list(assess_flags(c, cstrat, today=today))
        # 예산선 초과 — 자기자본 필요 > 보유 자본이면 매수 불가(2026-05-30 사용자: 지역 예산선 역산 반영).
        if not fin.equity_ok:
            cap = int(profile["own_capital_krw"])
            gap = fin.equity_required_krw - cap
            flags.append(RiskFlag("F_OVERBUDGET",
                f"자기자본 필요 {fin.equity_required_krw/1e8:.2f}억 > 보유 {cap/1e8:.1f}억 "
                f"(부족 {gap/1e8:.2f}억) — 예산선 초과, 현 자본으론 매수 불가.", 0.4))
        flags = tuple(flags)
        pp = penalty_product(list(flags))
        adjusted = round(a.weighted_total * pp, 3)
        # ★호가 분리(2026-06-04): 순위는 호가무관 fundamental_total 기준 → '많이 빠짐→싸짐→상위' 누수 차단.
        adjusted_fundamental = round(a.fundamental_total * pp, 3)
        evaluated.append(Evaluated(candidate=c, finance=fin, redev=r, axis=a, hold=hold,
                                   break_even=brk, flags=flags, adjusted_total=adjusted,
                                   adjusted_fundamental=adjusted_fundamental, trend=trend))

    # 결정신뢰도 지표 (입력 검증 완성도) — 리포트가 자신의 신뢰수준을 수치 보고
    from .analysts.location import parse_location
    from .analysts.trust import assess_trust
    def _redev_verified(name: str) -> bool:
        rv = store.get_redev(conn, name)
        return bool(rv and rv.get("source_url"))
    trust_scores = [
        assess_trust(e.candidate, trend=e.trend, policy_is_default=params.is_default,
                     has_location_signal=(parse_location(e.candidate.transit).line_count > 0),
                     redev_verified=_redev_verified(e.candidate.listing.complex_name),
                     strategy=strat_for(e.candidate))
        for e in evaluated
    ]

    profile_disp = {**{k: v for k, v in profile.items() if not k.startswith("_")}, "LTV적용": ltv_note}
    policy_meta = {"is_default": params.is_default, "confirmed_date": params.confirmed_date,
                   "source": params.source}
    compset_data = (json.loads(Path(compset_path).read_text(encoding="utf-8")) if compset_path else None)
    # 시계열 mean-reversion 타이밍(구 추세대비 저평가, 검증 ρ+0.68) → compset 에 합쳐 §★② 렌더(점수 아님).
    if trend_gap_path:
        _tg = json.loads(Path(trend_gap_path).read_text(encoding="utf-8"))
        compset_data = dict(compset_data or {})
        compset_data["trend_gap"] = {k: v for k, v in _tg.items() if not k.startswith("_")}
    # 민감도 — 조정점수(호가무관) base top1 추출 후 perturbations. 별지 A(가중치 교란) 전용.
    # ★랭킹 지표 단일화(2026-06-06 적대분석): 순위·BLUF·민감도 top 전부 adjusted_fundamental(호가무관)로 통일.
    #   §A 정렬(assembler:246)이 이미 adjusted_fundamental 인데 cli 순위·민감도·§0.6 은 adjusted_total(가격포함)을
    #   써 #1 모순(중계무지개 vs 상계주공14)이 났다 — 2026-06-04 Wittgenstein 호가분리가 순위 기준이므로 그쪽으로 통일.
    # ★하드페일 partition(3차 감사 B): assembler §A 정렬키와 동일한 ranking_key 로 — partition 없이
    #   max() 만 쓰면 F_OVERBUDGET ×0.4 점수가 통과 후보를 넘을 때 BLUF 1위 ≠ §A 1위.
    score_top = max(evaluated,
                    key=lambda e: ranking_key(e.flags, e.adjusted_fundamental)
                    ).candidate.listing.complex_name
    sens = sensitivity_analysis(candidates, strategy, [e.flags for e in evaluated], score_top,
                                base_weights=eff_weights)
    base_top = score_top
    html = build_report(profile=profile_disp, strategy=strategy, evaluated=evaluated,
                        policies=store.latest_policies(conn), today=today,
                        council_insight=insight, council_session=council_session,
                        policy_meta=policy_meta, sensitivity=sens, base_top=score_top,
                        council_models=council_models, trust_scores=trust_scores,
                        location=(json.loads(Path(location_path).read_text(encoding="utf-8"))
                                  if location_path else None),
                        reviews=(json.loads(Path(reviews_path).read_text(encoding="utf-8"))
                                 if reviews_path else None),
                        strategy_by_name=({c.listing.complex_name: strat_for(c).value for c in candidates}
                                          if _mix else None),
                        structural=(json.loads(Path(structural_path).read_text(encoding="utf-8"))
                                    if structural_path else None),
                        cagr=(json.loads(Path(cagr_path).read_text(encoding="utf-8"))
                              if cagr_path else None),
                        compset=compset_data,
                        coverage=(json.loads(Path(coverage_path).read_text(encoding="utf-8"))
                                  if coverage_path else None))
    # 명명규칙(2026-06-06): {category}_{slug}_{YYYYMMDD}.ext · scan 리포트는 report/scan/ 직속 (report/README.md).
    if not out:
        scan_dir = config.ensure_report_dir() / "scan"
        scan_dir.mkdir(parents=True, exist_ok=True)
        out = str(scan_dir / f"scan_agent_{date.today():%Y%m%d}.html")
    Path(out).write_text(html, encoding="utf-8")
    email_note = ""
    # 이메일 자동 발송 (핵심포인트 본문 + HTML 첨부) — SMTP 자격 있을 때만
    if not no_email:
        to = email or config.EMAIL_TO
        cfg = config.smtp_config()
        if cfg.ready:
            summary = compose_summary(evaluated, strategy, today, compset=compset_data)
            send_report(out, summary, to, config.EMAIL_SUBJECT, cfg)
            email_note = f"이메일 전송: {to} · 제목 '{config.EMAIL_SUBJECT}' · {Path(out).name} 첨부"
        else:
            email_note = "이메일 미전송 — .env 에 SMTP_USER/SMTP_PASS 설정 시 자동 발송"
    ranking = [(e.candidate.listing.complex_name, e.adjusted_fundamental, bool(e.flags))
               for e in sorted(evaluated, reverse=True,
                               key=lambda e: ranking_key(e.flags, e.adjusted_fundamental))]
    return {"out": out, "ranking": ranking, "strategy": strategy.name,
            "evaluated_count": len(evaluated), "base_top": base_top,
            "email_note": email_note}


def cmd_report(args) -> None:
    if getattr(args, "demo", False):
        # 슈퍼샘플 데모(2026-06-11): 합성 단지 6곳으로 클론 직후 1분 재현. 실데이터·이메일 불관여.
        from .demo import write_demo_inputs
        args.profile, args.input = write_demo_inputs()
        args.no_email = True
        if not args.out:
            args.out = f"report/scan/scan_demo_{date.today():%Y%m%d}.html"
        print("[demo] 합성 데모 단지 6곳 — 실존 단지·인물과 무관. 게이트(F_NORENT/F_OVERBUDGET) 시연 포함.")
    if not args.profile or not args.input:
        sys.exit("report: --profile/--input 필수 (또는 --demo)")
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    try:
        res = produce_report(
            profile=profile, input_path=args.input, insight=args.insight,
            council_session=args.council_session, council_models=args.council_models,
            out=args.out, no_email=args.no_email, email=args.email,
            location_path=args.location, reviews_path=args.reviews,
            structural_path=args.structural, cagr_path=args.cagr, compset_path=args.compset,
            coverage_path=args.coverage, reference_path=args.reference,
            trend_gap_path=getattr(args, "trend_gap", None),
        )
    except ValueError as e:
        sys.exit(str(e))
    print(f"리포트 생성: {res['out']}")
    if res["email_note"]:
        print(res["email_note"])
    # 순위 = 조정점수(호가무관 adjusted_fundamental, 2026-06-04 호가분리·2026-06-06 단일화). ⚠️=예산/법규 제약.
    print("조정점수(호가무관) 순위 · ⚠️=예산/법규 제약:", " > ".join(
        f"{name}({score:.3f}{'⚠️' if flagged else ''})" for name, score, flagged in res["ranking"]))


def cmd_scan_policy(args) -> None:
    config.assert_mount()
    conn = store.connect()
    for p in json.loads(Path(args.input).read_text(encoding="utf-8")):
        store.upsert_policy(conn, p["topic"], p["statement"], p["url"], p["confirmed_date"], p.get("law_ref", ""))
        if "param_key" in p and "param_value" in p:   # ① 기계가독 정책 수치 동시 적재
            store.upsert_param(conn, p["param_key"], p["param_value"], p["url"], p["confirmed_date"], p.get("topic", ""))
    print("정책 스냅샷/파라미터 캐시 갱신")


def cmd_update_redev(args) -> None:
    config.assert_mount()
    conn = store.connect()
    for r in json.loads(Path(args.input).read_text(encoding="utf-8")):
        store.upsert_redev(conn, r["complex_name"], r.get("district", ""), r["stage"],
                           r.get("stage_date", ""), r.get("source_url", ""), r["confirmed_date"])
    print("재건축 단계 캐시 갱신")


def cmd_update_prices(args) -> None:
    config.assert_mount()
    conn = store.connect()
    n = 0
    for r in json.loads(Path(args.input).read_text(encoding="utf-8")):
        store.upsert_price(conn, r["complex_name"], r["area_exclusive_m2"], r["deal_ym"], r["price_krw"],
                           r.get("source", "MOLIT_API")); n += 1
    print(f"실거래 시계열 {n}건 적재")


def cmd_update_land(args) -> None:
    config.assert_mount()
    conn = store.connect()
    for r in json.loads(Path(args.input).read_text(encoding="utf-8")):
        store.upsert_land(conn, r["complex_name"], r["area_exclusive_m2"], r["land_share_pyeong"],
                          r.get("source_url", ""), r["confirmed_date"])
    print("등기부 대지지분 실측 캐시 갱신")


def cmd_fetch_molit(args) -> None:
    """② MOLIT 실거래 API 직접 수집 → price_series 적재. --district(R12) 또는 --lawd."""
    config.assert_mount()
    from .collectors.lawd import lawd_for_district
    lawd = args.lawd or lawd_for_district(args.district or "")
    if not lawd:
        sys.exit("LAWD_CD 미해결 — --lawd 5자리 또는 --district(서울 자치구) 필요")
    args.lawd = lawd
    conn = store.connect()
    complexes = [c.strip() for c in args.complex.split(",")] if args.complex else []
    total = 0
    for ym in [y.strip() for y in args.ym.split(",")]:
        items = molit.fetch_apt_trades(args.lawd, ym)
        if complexes:
            picked = [it for c in complexes for it in molit.filter_by_complex(items, c)]
        else:
            picked = items
        for it in picked:
            if it["deal_ym"] and it["area_exclusive_m2"]:
                store.upsert_price(conn, it["complex_name"], it["area_exclusive_m2"],
                                   it["deal_ym"], it["price_krw"], "MOLIT_API")
                total += 1
    print(f"MOLIT 실거래 {total}건 적재 (lawd={args.lawd}, ym={args.ym}, 필터={complexes or '전체'})")


def cmd_parse_naver(args) -> None:
    """R2: 네이버 innerText → candidates.json (4요소+broker_count). 단지 메타는 --enrich 또는
    R5 캐시(complex_meta/redev_stage/land_registry)에서 자동 보강."""
    config.assert_mount()
    from .collectors.naver_live import build_candidates_from_text
    conn = store.connect()
    text = Path(args.input).read_text(encoding="utf-8")
    enrich = json.loads(Path(args.enrich).read_text(encoding="utf-8")) if args.enrich else {}
    cands = build_candidates_from_text(text, enrich, args.complex)
    # R5: 캐시에서 메타 자동 보강 (enrich 가 안 채운 필드만)
    meta = store.get_meta(conn, args.complex)
    redev = store.get_redev(conn, args.complex)
    filled = []
    for c in cands:
        if meta:
            c.setdefault("units", meta["units"]); c.setdefault("built_year", meta["built_year"])
            if meta.get("far_pct"):                 # 0/None 이면 용적률 미보강(K-apt 미제공)
                c.setdefault("far_pct", meta["far_pct"])
        if redev:
            c.setdefault("redev_stage", redev["stage"])
        land = store.get_land(conn, args.complex, c.get("area_exclusive_m2", 0))
        if land:
            c.setdefault("land_share_pyeong", land["land_share_pyeong"]); c["land_share_is_estimate"] = False
        filled.append(c)
    Path(args.out).write_text(json.dumps(filled, ensure_ascii=False, indent=2), encoding="utf-8")
    src = "enrich" if enrich else ("캐시" if meta else "없음(report 전 메타 보강 필요)")
    print(f"{len(filled)}개 매물 파싱 → {args.out} (메타 출처: {src})")


def cmd_fetch_meta(args) -> None:
    """R5 자동수집: K-apt(공동주택 기본정보, data.go.kr 같은 키) → 세대수·준공 → complex_meta 캐시.
    용적률은 K-apt 미제공이라 --far 로 보강(없으면 0=미보강)."""
    config.assert_mount()
    from .collectors.kapt import fetch_meta_for
    conn = store.connect()
    m = fetch_meta_for(args.district, args.complex)
    if not m:
        sys.exit(f"K-apt 단지 미매칭: {args.complex} ({args.district})")
    store.upsert_meta(conn, args.complex, m["units"], float(args.far or 0), m["built_year"],
                      "K-apt(data.go.kr 15058453)", date.today().isoformat())
    print(f"단지메타 수집: {args.complex} 세대수={m['units']} 준공={m['built_year']} 동수={m.get('dong_cnt')}"
          + (f" 용적률={args.far}%" if args.far else " (용적률 미보강)"))


def cmd_update_meta(args) -> None:
    """R5: 단지 메타(세대수·용적률·준공) 캐시 주입. (Seoul 정비사업 API 자동수집은 SEOUL_API_KEY 영역)"""
    config.assert_mount()
    conn = store.connect()
    for m in json.loads(Path(args.input).read_text(encoding="utf-8")):
        store.upsert_meta(conn, m["complex_name"], m["units"], m["far_pct"], m["built_year"],
                          m.get("source_url", ""), m.get("confirmed_date", ""))
    print("단지 메타 캐시 갱신")


def cmd_fetch_location(args) -> None:
    """R9: 카카오 로컬로 주소→최근접 지하철·도보분 'transit' 문자열 생성(candidate.transit 에 사용)."""
    config.assert_mount()
    from .collectors.kakao import build_transit
    s = build_transit(args.address)
    if not s:
        sys.exit("카카오: 좌표/역 미발견")
    print(f"transit: {s}  (candidate.transit 또는 enrich 에 넣으면 location.py 가 파싱)")


def cmd_fetch_location_features(args) -> None:
    """R9b: 단지 리스트 → 카카오(역·초중고·학원밀집도) + opentopodata(경사도) → location JSON.
    오프라인 수집 전용(report-time 네트워크 금지, G3). --input [{complex_name,district}] → --out JSON."""
    config.assert_mount()
    import os
    from .collectors import kakao, terrain
    key = os.environ.get("KAKAO_REST_KEY", "")
    if not key:
        sys.exit("KAKAO_REST_KEY 미설정 (.env)")
    items = json.loads(Path(args.input).read_text(encoding="utf-8"))
    today = date.today().isoformat()

    def _sch(d):
        return f"{d['name']}({d['distance_m']}m)" if d else "-"

    out: dict = {}
    for it in items:
        nm = it["complex_name"]
        gu = it.get("district", "")
        geo = kakao.geocode_keyword(nm, key, gu)
        if not geo:
            print(f"  {nm}: geocode 실패 — 건너뜀")
            continue
        x, y, _pn, addr = geo
        st = kakao.nearest_subway(x, y, key)
        sch = kakao.nearest_schools(x, y, key)
        ac = kakao.academy_count(x, y, key)
        ac_exam = kakao.academy_exam_count(x, y, key)
        slope = terrain.compute_slope(float(y), float(x)) or {}
        out[nm] = {
            "address": addr,
            "station": f"{st['name']}({st['distance_m']}m)" if st else "-",
            "school_elem": _sch(sch.get("초등학교")), "school_mid": _sch(sch.get("중학교")),
            "school_high": _sch(sch.get("고등학교")), "academy_count_500m": ac,
            "academy_exam_800m": ac_exam,
            "center_elev_m": slope.get("center_elev_m"), "relief_m": slope.get("relief_m"),
            "slope_pct": slope.get("slope_pct"), "slope_grade": slope.get("slope_grade"),
            "source_loc": "KAKAO_LOCAL", "source_elev": "opentopodata SRTM30m", "confirmed_date": today,
        }
        print(f"  {nm}: 역 {out[nm]['station']} · 학원 {ac}개(입시 {ac_exam}) · 경사 {slope.get('slope_pct')}% {slope.get('slope_grade')}")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"location 수집 {len(out)}/{len(items)} 단지 → {args.out}")


def cmd_fetch_redev_seoul(args) -> None:
    """R5: 서울 정비사업 현황 → 단지명 매칭 → 단계 매핑 → redev_stage 캐시."""
    config.assert_mount()
    from .collectors.seoul_redev import fetch_redev
    conn = store.connect()
    rows = fetch_redev()
    norm = args.complex.replace(" ", "")
    hit = next((r for r in rows if norm in r["name"].replace(" ", "")), None)
    if not hit:
        sys.exit(f"서울 정비사업: '{args.complex}' 매칭 없음 (총 {len(rows)}건 조회)")
    store.upsert_redev(conn, args.complex, "", hit["stage"], hit["stage_text"],
                       "data.seoul OA-2253", date.today().isoformat())
    print(f"재건축 단계 수집: {args.complex} → {hit['stage']} ({hit['stage_text']})")


def cmd_fetch_overview(args) -> None:
    """네이버 단지 overview(현재 호가범위+최근실거래+메타)를 인증 Chrome 컨텍스트로 수집.
    헤드리스 429라 Chrome(new.land 탭)+JS허용 필요. 최근실거래→price_series, 세대/준공→complex_meta 적재."""
    config.assert_mount()
    from .collectors.naver_overview import fetch_overview
    conn = store.connect()
    names = [c.strip() for c in args.complex.split(",") if c.strip()]
    rows = fetch_overview(names)
    if not rows:
        sys.exit("overview 수집 0건 — Chrome 에 new.land.naver.com 탭 + 'Apple Events JS 허용' 확인")
    for o in rows:
        if not o.found:
            print(f"  {o.query}: (검색 없음)"); continue
        if o.recent_deal_manwon and o.recent_deal_ymd and o.recent_deal_area_m2:
            ym = o.recent_deal_ymd.replace(".", "-")[:7]
            store.upsert_price(conn, o.name, o.recent_deal_area_m2, ym,
                               o.recent_deal_manwon * 10000, "NAVER_OVERVIEW")
        if o.households and o.built_year:
            store.upsert_meta(conn, o.name, o.households, 0.0, o.built_year,
                              "네이버 overview", date.today().isoformat())
        gap = f" · 호가↔실거래 괴리 {o.gap_pct:+.0f}%" if o.gap_pct is not None else ""
        print(f"  {o.name} [{o.type}] {o.households}세대·{o.built_year} | 호가 {o.asking_min}~{o.asking_max} | "
              f"최근실거래 {o.recent_deal}@{o.recent_deal_ymd}{gap}")
    print(f"overview {sum(1 for o in rows if o.found)}건 수집 (최근실거래→price_series, 메타→complex_meta 적재)")


def cmd_screen_region(args) -> None:
    """필터 우선 전수 스크리닝(council 2026-05-31 설계). L0 scan-region 열거(무료) →
    L1 scale·age 하드컷(scan 인자) → L2 geo 신호필터(좌표 haversine, 0콜) →
    L3 예산 절대경계(MOLIT 중위가, 네이버 0콜, ±15% 마진) → 생존분만 L4 호가수집(fetch-overview).
    좌표·MOLIT 결손은 자동탈락 금지(unknown 버킷 보존, false-negative 방지). 단계별 생존율 로깅."""
    config.assert_mount()
    import math
    import statistics
    from .collectors.naver_region import scan_region
    conn = store.connect()
    ANCHORS = {"여의도": (37.5215, 126.9244), "강남": (37.4979, 127.0276), "시청": (37.5663, 126.9779),
               "동대구": (35.8797, 128.6286), "범어학군": (35.8567, 128.6225)}  # 서울 업무지구 / 대구 수성 범어 학군 중심
    anchor = ANCHORS.get(args.anchor)
    if args.anchor and not anchor:
        sys.exit(f"--anchor 미지원: {args.anchor} (택1: {','.join(ANCHORS)})")

    def hav(la1, lo1, la2, lo2):
        p = math.pi / 180
        a = (math.sin((la2 - la1) * p / 2) ** 2
             + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2)
        return round(2 * 6371 * math.asin(math.sqrt(a)), 2)

    budget_cap = args.budget_eok * 1e8 * 1.15 if args.budget_eok else None   # F3: +15% 마진(하드컷 금지)
    built_max = int(f"{args.built_before}12") if args.built_before else 999912
    survivors, stat = [], {"L0": 0, "L2탈락": 0, "geo_unknown": 0, "L3탈락": 0, "budget_unknown": 0}
    for d in [x.strip() for x in args.districts.split(",") if x.strip()]:
        rows = scan_region(d, far_max=args.far_max, built_max_ym=built_max, hh_min=args.hh_min)
        stat["L0"] += len(rows)
        for c in rows:
            # L2 geo (좌표 무료 haversine; 좌표 결손은 보존)
            if c.lat is None or c.lng is None:
                dist, geo = None, "geo-unknown"; stat["geo_unknown"] += 1
            elif anchor:
                dist = hav(float(c.lat), float(c.lng), anchor[0], anchor[1])
                if args.geo_max_km and dist > args.geo_max_km:
                    stat["L2탈락"] += 1; continue
                geo = f"{dist}km"
            else:
                dist, geo = None, "-"
            # L3 예산 (MOLIT 중위가; 네이버 0콜; 결손 보존)
            s = store.get_price_series(conn, c.name, float(args.area))
            if not s:
                med, bud = None, "budget-unknown"; stat["budget_unknown"] += 1
            else:
                med = statistics.median([r["price_krw"] for r in s])
                if budget_cap and med > budget_cap:
                    stat["L3탈락"] += 1; continue
                bud = f"{med / 1e8:.1f}억"
            survivors.append({"complex_name": c.name, "complex_no": c.complex_no, "district": d,
                              "households": c.households, "built_year": c.built_year, "far_pct": c.far_pct,
                              f"{args.anchor}_거리": geo, "MOLIT중위가": bud})
    out = args.out or str(config.ensure_report_dir() / "screen_region.json")
    Path(out).write_text(json.dumps(survivors, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"스크리닝: L0 열거 {stat['L0']} → 생존 {len(survivors)} (geo탈락 {stat['L2탈락']}·geo미상보존 {stat['geo_unknown']}"
          f"·예산탈락 {stat['L3탈락']}·예산미상보존 {stat['budget_unknown']}) → {out}")
    print("  생존분만 fetch-overview/parse-naver 로 L4 라이브 호가 수집 후 report (네이버 호출 최소화).")
    for sv in survivors[:30]:
        print(f"    {sv['complex_name']}({sv['households']}세대·{sv['built_year']}·용{sv['far_pct']}%) "
              f"{sv.get(args.anchor+'_거리','')} {sv['MOLIT중위가']}")


def cmd_scan_region(args) -> None:
    """구/동을 통째로 스캔해 재건축 후보 단지를 *자동 열거*한다 (단지명 입력 불필요).
    네이버 마커 API(인증 Chrome)가 용적률·준공·세대를 주므로 far_max·built_before·hh_min 으로 필터.
    출력: 용적률 오름차순(재건축 가치순) 후보 + complex_no(이후 fetch-overview/parse-naver 로 심화)."""
    config.assert_mount()
    from .collectors.naver_region import scan_region
    districts = [d.strip() for d in args.districts.split(",") if d.strip()]
    built_max = int(f"{args.built_before}12") if args.built_before else 999912
    seen: dict[str, object] = {}
    for d in districts:
        rows = scan_region(d, far_max=args.far_max, built_max_ym=built_max, hh_min=args.hh_min)
        for c in rows:
            seen.setdefault(c.complex_no, c)   # complexNo dedup (bbox bleed 제거)
        print(f"  [{d}] {len(rows)} 후보")
    out = sorted(seen.values(), key=lambda c: (c.far_pct if c.far_pct else 9999))
    if not out:
        sys.exit("스캔 0건 — Chrome 에 new.land.naver.com 탭 + 'Apple Events JS 허용' 확인")
    print(f"= 재건축 후보 {len(out)}개 (용적률 오름차순) =")
    for c in out:
        far = f"{c.far_pct}%" if c.far_pct else "미기재"
        print(f"  #{c.complex_no:<7} far{far:<6} {c.built_ym} {c.households:>5}세대 {c.name}")
    if args.out:
        import json as _json
        with open(args.out, "w", encoding="utf-8") as f:
            _json.dump([c.__dict__ for c in out], f, ensure_ascii=False, indent=1)
        print(f"→ {args.out} 저장 ({len(out)}개)")


def cmd_fetch_kb_sise(args) -> None:
    """KB부동산 KB시세(일반가/상위/하위/매물평균/전세)를 로그인 Chrome 단지페이지 DOM 에서 수집.
    KB시세 일반가 = 은행 LTV 기준. kbland.kr/c/{code} 의 code 필요(로그인 후 검색→단지 URL)."""
    config.assert_mount()
    from .collectors.kb_sise import fetch_kb_sise
    s = fetch_kb_sise(args.code)
    if not s or not s.mae_ilban_manwon:
        sys.exit("KB시세 수집 실패 — Chrome 에 kbland.kr 로그인 + 단지페이지 접근 확인")
    def eok(m): return f"{m/10000:.2f}억" if m else "—"
    print(f"  {s.name} (code {s.code}) KB시세")
    print(f"    매매 일반가 {eok(s.mae_ilban_manwon)} (은행 LTV 기준) · 상위 {eok(s.mae_upper_manwon)} · "
          f"하위 {eok(s.mae_lower_manwon)} · 매물평균(호가) {eok(s.mae_listing_avg_manwon)} · 전세 {eok(s.jeonse_ilban_manwon)}")
    if s.listing_vs_kb_pct is not None:
        print(f"    호가↔KB시세 괴리 {s.listing_vs_kb_pct:+.0f}% (매물평균이 KB일반가보다 위)")
    if args.store and args.complex:
        conn = store.connect()
        store.upsert_price(conn, args.complex, float(args.area or 0), date.today().strftime("%Y-%m"),
                           s.mae_ilban_krw, "KB_SISE")
        print(f"    KB시세 일반가 → price_series 적재 (source=KB_SISE, {args.complex})")


def cmd_nav(args) -> None:
    """단지 검색 → 이동: 단지명으로 내부 API(complexNo) 해석 후 열린 new.land 탭을 그 단지로
    이동(수동 검색·클릭 개입 제거). 이후 fetch-overview/parse-naver 가 그 탭 위에서 작동.
    여러 단지면 마지막 단지로 이동(순차 수집 시 단지별 호출)."""
    config.assert_mount()
    from .collectors.naver_nav import navigate_to_complex
    names = [c.strip() for c in args.complex.split(",") if c.strip()]
    last_found = False
    for nm in names:
        r = navigate_to_complex(nm)
        if r.found:
            last_found = True
            print(f"  → {r.name} (complexNo {r.complex_no}) 이동 {r.navigated_to}")
        else:
            print(f"  → {nm}: 검색/이동 실패 (Chrome new.land 탭 + JS허용 확인, 추정 안 함)")
    if not last_found:
        sys.exit("단지 이동 0건 — Chrome 에 new.land.naver.com 탭 + 'Apple Events JS 허용' 확인")


def cmd_backfill(args) -> None:
    print("backfill: 실거래는 update-prices(JSON 주입) 또는 fetch-molit(API 직접) 로 적재.")


def cmd_doc_sync(args) -> None:
    """capability.py 상수에서 AGENT_CAPABILITIES.md auto-capabilities 블록을 재생성.

    코드(금융공식·평가축·신뢰등급·데이터소스 실상태)가 문서의 단일소스 — 손정합 drift 방지.
    특히 '입력 진위검증' 역량이 DataSourceStatus 에서 파생되어 overstatement 가 구조적으로 차단."""
    config.assert_mount()
    from .capability import capability_reference_md
    path = config.EXT_ROOT / "AGENT_CAPABILITIES.md"
    text = path.read_text(encoding="utf-8")
    begin, end = "<!-- BEGIN:auto-capabilities", "<!-- END:auto-capabilities -->"
    bi, ei = text.find(begin), text.find(end)
    if bi == -1 or ei == -1:
        raise SystemExit("AGENT_CAPABILITIES.md 에 auto-capabilities 마커 없음 — 구조 누락")
    bi_end = text.find("-->", bi) + 3
    new = text[:bi_end] + "\n\n" + capability_reference_md() + "\n\n" + text[ei:]
    path.write_text(new, encoding="utf-8")
    print(f"[doc-sync] AGENT_CAPABILITIES.md auto-capabilities 갱신: {path}")


def cmd_run(args) -> None:
    from agent_realestate.bus import run_task
    print(f"[result] {run_task(args.task_file)}")


def cmd_register(args) -> None:
    from agent_realestate.bus import register
    print(f"[registered] {register()}")


def cmd_daily(args) -> None:
    """일일 토큰-제로 오케스트레이터 (Phase 2, 2026-06-11) — cron/수동 공용 원커맨드.
    순서: ① MOLIT 실거래 fresh 재수집 → ② 블로그 생성(신선도-이벤트) → ③ 사이트 조립
         → ④ site push-if-changed → ⑤ 플래그십 리포트 regen(게이트, 비치명).
    전제: editable 설치(pip install -e .) 또는 repo 루트 실행 — 루트는 regen_reports.py 위치로 탐지."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    if not (root / "regen_reports.py").exists():
        sys.exit("[daily] repo 루트 탐지 실패 — editable 설치(pip install -e .) 또는 루트에서 실행하세요.")

    def step(label: str, cmd: list[str], fatal: bool = True) -> None:
        print(f"[daily] ▶ {label}")
        r = subprocess.run(cmd, cwd=root)
        if r.returncode != 0:
            print(f"[daily] ❌ {label} 실패(rc={r.returncode})" + ("" if fatal else " — 비치명, 계속"))
            if fatal:
                # ★Task I(2026-06-14): 치명 실패 시 텔레그램 알림 (TELEGRAM_BOT_TOKEN/CHAT_ID 미설정이면 무음)
                notify_step_failure(label, r.returncode, date.today().isoformat())
                sys.exit(r.returncode)

    today = date.today().isoformat()
    import shutil

    def _molit_total(p: Path) -> int:
        try:
            c = json.load(open(p)); return sum(len(v) for k, v in c.items() if k != "_done")
        except Exception:
            return 0

    # ★2026-06-14 데이터손실 방지: 기존엔 캐시를 unlink 후 fresh 재수집 → DNS/네트워크 플레이크가
    #   끼면 좋은 캐시(수만건)를 지운 채 대량실패 → 빈 결과(수십~백건)로 영구 회귀(06-14 사고: 43,046→148).
    #   수정: 삭제 대신 .daybak 백업 후 비우고, 재수집 결과가 직전 대비 급감(<70%)하면 백업 복원.
    molit_json = root / "examples/molit_recent_11gu_20260606.json"
    molit_bak = molit_json.with_name(molit_json.name + ".daybak")
    prev_total = _molit_total(molit_json) if molit_json.exists() else 0
    if molit_json.exists():
        shutil.copy(molit_json, molit_bak)   # 백업 후 비움(fresh 재수집 유도)
        molit_json.unlink()
    step("MOLIT 실거래 fresh 재수집", ["python3", "fetch_molit_recent_11gu.py"], fatal=False)
    new_total = _molit_total(molit_json) if molit_json.exists() else 0
    if prev_total > 1000 and new_total < prev_total * 0.7:
        print(f"[daily] ⚠️ MOLIT 재수집 회귀 ({new_total} ≪ 직전 {prev_total}) — DNS/네트워크 의심. "
              f"직전 캐시 복원, 블로그는 보존 데이터로 진행")
        if molit_bak.exists():
            shutil.copy(molit_bak, molit_json)
        notify_step_failure("MOLIT 재수집 회귀(직전 캐시 복원)", 1, today)
    if molit_bak.exists():
        molit_bak.unlink()
    # ★A모델(2026-06-17): run_daily 가 실명 사실 포스트 + dataset.json + explorer.html 를 모두 생성
    #   (자체 점수 없음·공공 실거래만·세대수200/corridor 제외). build_site 가 site/ 로 조립.
    step("블로그 생성(실명 포스트+탐색기)", ["python3", "-m", "blog.run_daily",
                          "--asof", today, "--today", today, "--block-stale"])
    step("사이트 조립", ["python3", "-m", "blog.build_site"])
    site = root / "site"
    if site.is_dir() and subprocess.run(["git", "-C", str(site), "status", "--porcelain"],
                                        capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(site), "add", "-A"], check=True)
        # 커밋 정체성은 site repo 로컬 git config 사용 (하드코딩 제거 — 범용화 2026-06-11)
        subprocess.run(["git", "-C", str(site),
                        "commit", "-q", "-m", f"daily snapshot {today}"], check=True)
        # ★push 재시도 + stderr 로깅(2026-06-12: 07:08 cron 에서 무출력 실패 — 일시 장애 추정이나
        #   원인 텍스트가 없어 진단 불가였음. 실패 시 에러를 시끄럽게 남긴다. Loud Failure.)
        import time as _time
        for _try in range(3):
            r = subprocess.run(["git", "-C", str(site), "push", "-q", "origin", "main"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                print("[daily] site push OK")
                break
            print(f"[daily] push 시도 {_try + 1}/3 실패(rc={r.returncode}): {r.stderr.strip()[:300]}")
            _time.sleep(10)
        else:
            print("[daily] site push 실패 — 커밋은 로컬 보존, 다음 실행에서 재시도")
    else:
        print("[daily] 실거래 무변동 — 무발행(정상)")
    # 플래그십 리포트 — 게이트 미달이면 차단되는 게 정상이라 비치명
    step("플래그십 리포트 regen(게이트)", ["python3", "regen_reports.py"], fatal=False)
    # ★Task I(2026-06-14): 정상 완료 텔레그램 알림
    notify_daily_result(ok=True, today=today, detail="daily pipeline 완료")
    print(f"[daily] done {today}")


def cmd_scan_regime(args) -> None:
    """★Task J(2026-06-14): 현 국면 진단 + BOK 금리 갱신 필요 여부 체크.
    네트워크 0 — regime.py 상수만으로 결정론 출력.
    BOK 결정일 전후(1월/2월/4월/5월/7월/8월/10월/11월 마지막 목요일 전후)에 수동 실행 권장.
    갱신 필요 시: regime.py BOK_RATE[year] 와 _TIMELINE[year] 를 검증된 사실로 업데이트 후 pytest 실행."""
    from .analysts.regime import (current_regime, classify_regime, BOK_RATE, POLICY_STANCE,
                                  _TIMELINE, regime_entry_read)
    rc = current_regime()
    yr = rc.year
    print(f"\n=== scan-regime {date.today()} ===")
    print(f"현 국면: {yr}년 {rc.phase} / {rc.policy_stance} 스탠스 / {rc.sentiment}")
    print(f"  {rc.note}")
    print()
    print(f"BOK 기준금리(연말값):")
    for y in sorted(k for k in BOK_RATE if k >= yr - 2):
        flag = " ★현재" if y == yr else ""
        src = "(FACT: bok.or.kr 웹검증 2026-06-13)" if y == 2026 else ""
        print(f"  {y}: {BOK_RATE[y]:.2f}%{flag} {src}")
    print()
    # 갱신 필요 감지: 올해 금리=작년 금리이고 타임라인 rate 표기가 '동결추정' 포함
    prev_rate = BOK_RATE.get(yr - 1)
    cur_rate = BOK_RATE.get(yr)
    tl = _TIMELINE.get(yr, ())
    rate_label = tl[3] if len(tl) > 3 else ""
    if "추정" in rate_label or "가정" in rate_label:
        print(f"⚠️  {yr} BOK_RATE 표기가 '{rate_label}' — 실측으로 갱신 권장")
        print(f"   bok.or.kr 기준금리 결정 결과 확인 후:")
        print(f"   regime.py BOK_RATE[{yr}] = <실측값>  # 소수점 둘째자리")
        print(f"   regime.py _TIMELINE[{yr}] 의 rate 문자열도 동결/인하/인상으로 수정")
    else:
        print(f"✅ {yr} BOK_RATE={cur_rate}% 확정값({rate_label}) — 갱신 불필요")
    print()
    er = regime_entry_read(yr)
    if er:
        print(f"진입환경: {er.get('read')}  위험={er.get('risk')}")
        print(f"전세가율: {er.get('jeonse_ratio')}%  추세={er.get('jeonse_trend')}")
    print()
    print("[hint] BOK 결정일: 연 8회(1/2/4/5/7/8/10/11월 마지막 목요일 전후). 결정 후 이 명령 재실행 + regime.py 수정 + pytest.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="agent-realestate")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report"); r.add_argument("--profile"); r.add_argument("--input"); r.add_argument("--out")
    r.add_argument("--demo", action="store_true", help="합성 데모 단지 6곳으로 전체 파이프라인 1분 재현(실데이터 불필요)")
    r.add_argument("--insight", help="council 결론 원문(§7 에 [추론]으로 인용)")
    r.add_argument("--council-session", dest="council_session", help="ask_council session_id (report_outcome 루프용)")
    r.add_argument("--council-models", dest="council_models", type=int, help="council model diversity 수(R10 게이트, <2 강한 경고)")
    r.add_argument("--email", help=f"리포트 발송 주소(기본 {config.EMAIL_TO})")
    r.add_argument("--no-email", dest="no_email", action="store_true", help="이메일 발송 안 함")
    r.add_argument("--location", help="입지 정량 JSON(역·학교 직선거리·경사도, 단지명 키)")
    r.add_argument("--reviews", help="커뮤니티 후기 aspect JSON(테마+n+출처, 점수 미산출)")
    r.add_argument("--structural", help="구조적 동력 JSON(공급·호재·인구 coarse 등급, 회귀 추세 보완)")
    r.add_argument("--cagr", help="실거래 장기 명목 CAGR JSON(15년 baseline, 단지명 키)")
    r.add_argument("--compset", help="재편 A 3신호 주입 JSON(gen-baserate 산출: candidates/longrun/peers_recent)")
    r.add_argument("--trend-gap", dest="trend_gap", help="구 추세대비 저평가(시계열 mean-reversion 타이밍) JSON(gen-trend-gap 산출) — §★② 렌더")
    r.add_argument("--coverage", help="발견 커버리지 감사 JSON(molit_discovery_audit 산출: coverage_pct/gap/top_gap/gap_by_gu) — '전수' overclaim 교정")
    r.add_argument("--reference", help="가격메리트 헤도닉·공백평균을 산출할 고정 기준집합 후보 JSON — 동일 단지가 통합/단독 리포트서 같은 점수(v3)")
    r.set_defaults(fn=cmd_report)
    fm = sub.add_parser("fetch-molit"); fm.add_argument("--lawd"); fm.add_argument("--district"); fm.add_argument("--ym", required=True); fm.add_argument("--complex"); fm.set_defaults(fn=cmd_fetch_molit)
    for name, fn in [("scan-policy", cmd_scan_policy), ("update-redev", cmd_update_redev),
                     ("update-prices", cmd_update_prices), ("update-land", cmd_update_land)]:
        s = sub.add_parser(name); s.add_argument("--input", required=True); s.set_defaults(fn=fn)
    pn = sub.add_parser("parse-naver"); pn.add_argument("--input", required=True); pn.add_argument("--complex", required=True); pn.add_argument("--enrich"); pn.add_argument("--out", required=True); pn.set_defaults(fn=cmd_parse_naver)
    um = sub.add_parser("update-meta"); um.add_argument("--input", required=True); um.set_defaults(fn=cmd_update_meta)
    fmeta = sub.add_parser("fetch-meta"); fmeta.add_argument("--district", required=True); fmeta.add_argument("--complex", required=True); fmeta.add_argument("--far", type=float); fmeta.set_defaults(fn=cmd_fetch_meta)
    floc = sub.add_parser("fetch-location"); floc.add_argument("--address", required=True); floc.add_argument("--complex"); floc.set_defaults(fn=cmd_fetch_location)
    flf = sub.add_parser("fetch-location-features"); flf.add_argument("--input", required=True); flf.add_argument("--out", required=True); flf.set_defaults(fn=cmd_fetch_location_features)
    frs = sub.add_parser("fetch-redev-seoul"); frs.add_argument("--complex", required=True); frs.set_defaults(fn=cmd_fetch_redev_seoul)
    nv = sub.add_parser("nav"); nv.add_argument("--complex", required=True, help="단지명 쉼표구분 — 내부 API 로 complexNo 해석 후 열린 탭을 그 단지로 이동(수동 검색 개입 제거)"); nv.set_defaults(fn=cmd_nav)
    fov = sub.add_parser("fetch-overview"); fov.add_argument("--complex", required=True, help="단지명 쉼표구분 (네이버 호가+실거래+메타, 인증 Chrome 경유)"); fov.set_defaults(fn=cmd_fetch_overview)
    scr = sub.add_parser("scan-region", help="구/동 자동 스캔으로 재건축 후보 단지 열거 (단지명 불필요)")
    scr.add_argument("--districts", required=True, help="구 이름 또는 cortarNo 쉼표구분 (예: 노원,도봉,강서)")
    scr.add_argument("--far-max", type=int, default=210, help="용적률 상한(재건축 가치 필터, 기본 210)")
    scr.add_argument("--built-before", type=int, default=1996, help="준공연도 상한(기본 1996 이전)")
    scr.add_argument("--hh-min", type=int, default=500, help="최소 세대수(기본 500)")
    sg = sub.add_parser("screen-region", help="필터우선 전수 스크리닝(L0열거→L2geo→L3예산MOLIT, 생존분만 호가수집)")
    sg.add_argument("--districts", required=True)
    sg.add_argument("--anchor", help="geo 기준점(여의도/강남/시청/동대구/범어학군)")
    sg.add_argument("--geo-max-km", type=float, help="anchor 직선거리 상한(km, 초과 탈락)")
    sg.add_argument("--budget-eok", type=float, help="예산(억) — MOLIT 중위가 ±15% 마진 컷")
    sg.add_argument("--area", type=float, default=84, help="MOLIT 중위가 조회 전용면적(기본 84)")
    sg.add_argument("--far-max", type=int, default=9999)
    sg.add_argument("--built-before", type=int)
    sg.add_argument("--hh-min", type=int, default=300)
    sg.add_argument("--out")
    sg.set_defaults(fn=cmd_screen_region)
    scr.add_argument("--out", help="결과 JSON 저장 경로")
    scr.set_defaults(fn=cmd_scan_region)
    fkb = sub.add_parser("fetch-kb-sise"); fkb.add_argument("--code", required=True, help="kbland.kr/c/{code} 단지코드"); fkb.add_argument("--complex"); fkb.add_argument("--area", type=float); fkb.add_argument("--store", action="store_true"); fkb.set_defaults(fn=cmd_fetch_kb_sise)
    sub.add_parser("backfill").set_defaults(fn=cmd_backfill)
    sub.add_parser("doc-sync", help="capability 상수 → AGENT_CAPABILITIES auto-capabilities 블록 재생성(drift 방지)").set_defaults(fn=cmd_doc_sync)
    rn = sub.add_parser("run", help="버스 워커 모드 (Task v1 → Result v1)")
    rn.add_argument("--task-file", required=True, dest="task_file"); rn.set_defaults(fn=cmd_run)
    sub.add_parser("register", help=".orchestra 레지스트리 등록").set_defaults(fn=cmd_register)
    sub.add_parser("daily", help="일일 토큰-제로 오케스트레이터(MOLIT fresh→블로그→site push→리포트 regen)").set_defaults(fn=cmd_daily)
    sub.add_parser("scan-regime", help="현 국면 진단 + BOK 금리 갱신 필요 여부 체크(네트워크 0, BOK 결정일 전후 수동 실행)").set_defaults(fn=cmd_scan_regime)
    config.load_env_file()   # .env 의 MOLIT_API_KEY 등 주입 (시크릿 비노출)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
