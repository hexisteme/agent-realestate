"""compset 테이블 조립 (재편 A, gen-baserate 의 결정론 코어).

설계 §3/§7: 생활권 base-rate 표 + within-생활권 recent CAGR(peers)을 *주입 테이블*로 만든다.
report-time 네트워크 없음(G3). MOLIT 동일단지 실현 CAGR 만 사용(점예측·추정 금지, RDU-021/061).

흐름: gen_baserate.py(MOLIT 수집) → same_name_cagrs() → assemble_compset() → --compset JSON
      → assembler 의 3신호 섹션(base_rate_band / mean_reversion_signal) 이 렌더.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from .compset_signals import base_rate_band

# 하드필터 탈락(우선검토 후순위) 코드 — 단일 소스는 risk.py(3차 감사 B 통일). re-export 유지.
from .risk import HARD_FAIL_CODES, has_hard_fail  # noqa: F401

# 전용 band (설계 §3 AREA_BANDS). base-rate·mean-reversion 을 *평형별*로 분리(2026-06-02).
AREA_BANDS = (59, 72, 84, 110, 135)


def bucket_area(area: float) -> int:
    """전용면적(㎡) → 가장 가까운 전용band (밴드 중점 경계). 84 고집 배제(평형별 base-rate)."""
    if area < 65.5:
        return 59
    if area < 78:
        return 72
    if area < 97:
        return 84
    if area < 122.5:
        return 110
    return 135


def assign_sg(compset: dict | None, name: str) -> str | None:
    """compset.assign 에서 단지→생활권(폴백용 — 일반명 충돌 가능, 후보가 직접 보유한 sg 우선)."""
    return (compset.get("assign") or {}).get(name) if compset else None


def lookup_band(compset: dict | None, sg: str | None, area: float):
    """(생활권 × 그 매물 전용band) base-rate 밴드. 표본 n<8 이면 None(필터only).
    해당 band 표본 없으면 인접→84 폴백(생활권 신호 보존). ★sg 는 호출자(후보)가 확정(충돌 방지)."""
    if not (compset and sg):
        return None
    lr = compset.get("longrun") or {}
    band = bucket_area(area)
    for b in (band, 84, 72, 110, 59, 135):     # 요청 band 우선, 없으면 84 등 폴백
        cagrs = lr.get(f"{sg}|{b}")
        if cagrs:
            return base_rate_band(sg, b, cagrs)
    return None


def band_median(compset: dict | None, sg: str | None, area: float, default: float = -9.0) -> float:
    """그 매물 전용band 의 생활권 base-rate median(분수). 미주입/표본부족이면 default(정렬 후순위)."""
    b = lookup_band(compset, sg, area)
    return b.cagr_median if b else default


def recent_cagr(compset: dict | None, sg: str | None, name: str, area: float) -> float | None:
    """그 (생활권·단지·전용band) 의 recent CAGR (mean-reversion 원자료). 충돌 방지로 sg 명시."""
    if not (compset and sg):
        return None
    return (compset.get("recent") or {}).get(f"{sg}|{name}|{bucket_area(area)}")


def sanctioned_key(compset: dict | None, sg: str | None, area: float, flags) -> tuple:
    """재편 A 정렬키 — (하드필터 통과, 그 매물 전용band 의 생활권 base-rate median). reverse=True."""
    return (not has_hard_fail(flags), band_median(compset, sg, area))


def same_name_cagrs(entry_rows: list[dict], exit_rows: list[dict], years: float,
                    n_min_per: int = 3) -> dict[str, float]:
    """동일단지(이름 정규화) entry→exit median 가격으로 실현 CAGR 산출.

    entry_rows/exit_rows: [{"apt": 단지명, "price": 원}]. 같은 단지가 양 구간 모두
    n_min_per 건 이상 거래된 경우만(생존편향·소표본 가드). 반환 {단지명: CAGR(분수)}.
    이름변경 완료단지는 구조적 배제됨(설계 한계 명시 — same-name MOLIT)."""
    if years <= 0:
        raise ValueError("years 는 양수 (entry→exit 연수)")
    by0: dict[str, list[int]] = defaultdict(list)
    by1: dict[str, list[int]] = defaultdict(list)
    for r in entry_rows:
        by0[str(r["apt"]).replace(" ", "")].append(int(r["price"]))
    for r in exit_rows:
        by1[str(r["apt"]).replace(" ", "")].append(int(r["price"]))
    out: dict[str, float] = {}
    for apt in set(by0) & set(by1):
        if len(by0[apt]) >= n_min_per and len(by1[apt]) >= n_min_per:
            m0, m1 = statistics.median(by0[apt]), statistics.median(by1[apt])
            if m0 > 0:
                out[apt] = round((m1 / m0) ** (1 / years) - 1, 4)
    return out


def assemble_compset(sets: dict, meta: dict | None = None) -> dict:
    """평형별 생활권 base-rate 표 → --compset 주입 구조 (band-aware, 2026-06-02).

    입력 sets: {생활권: {bands: {band: {"longrun":[CAGR], "recent":{단지:CAGR}}}}}.
    산출(결정론·정렬): {_meta,
       assign{단지: 생활권},                         # 단지 → 생활권 (평형 무관)
       longrun{"생활권|band":[CAGR]},                # base-rate, 평형별
       recent{"단지|band": CAGR},                    # mean-reversion 원자료, 평형별
       peers_recent{"생활권|band":[CAGR]}}.          # mean-reversion peers, 평형별
    longrun 표본은 assembler 가 base_rate_band(n_min=8) 게이트로 거른다(필터only 강등)."""
    assign: dict = {}
    longrun: dict = {}
    recent: dict = {}
    peers: dict = {}
    for sg in sorted(sets):
        for band in sorted(sets[sg].get("bands", {})):
            d = sets[sg]["bands"][band]
            longrun[f"{sg}|{band}"] = sorted(round(float(c), 4) for c in d.get("longrun", []))
            rec = d.get("recent", {})
            peers[f"{sg}|{band}"] = sorted(round(float(c), 4) for c in rec.values())
            for cx in sorted(rec):
                assign.setdefault(cx, sg)               # 폴백용(일반명 충돌 가능)
                recent[f"{sg}|{cx}|{band}"] = round(float(rec[cx]), 4)   # 충돌 방지 키
    return {"_meta": meta or {}, "assign": assign,
            "longrun": longrun, "recent": recent, "peers_recent": peers}
