"""사실 리드(FactLead) 계층(2026-09-06 브리핑 §2.1) — 모든 페이지 첫 화면에 "이 페이지에서만 알 수 있는
사실" 을 결정론으로 골라 템플릿 문장으로 렌더한다. LLM 없음, 같은 입력→같은 출력(스냅샷 고정 가능).

파이프라인: 후보 사실(가족 단위, 아래 _s*/_g*/_c* 함수) 생성 → compute_surprise_score → 유형별 최대 1개
선택(_finalize) → 문구 가드(assert_lead_wording_ok) → render_lead_block/render_lead_lines.

사전 고정(§7 D): 후보 패밀리·기준집단·속성(area_band/decade/price_segment)·비교 기간은 이 파일에 고정된
것 전부다 — 임의 속성 조합을 탐색해 극단값을 고르지 않는다(다중 탐색 선택 편향 방지).
게이트는 build_explorer.passes_rank_gate/passes_jeonse_gate/passes_turnover_gate 단일소스 재사용
(다이제스트·구허브와 동일 표본 정의 — 사실 리드만 다른 표본을 보는 사고 방지).
"""
from __future__ import annotations
import math
import statistics as st

import blog.build_explorer as be
from blog.wording_guard import assert_lead_wording_ok

LEAD_TYPES = ("편차", "패턴", "변화", "임계", "맥락")

_EPS = 1e-9
_HI_POS = 99            # daily_digest._HI_POS 와 동일(12개월 범위 상단 근접 임계) — 여기서도 임포트 대신 상수 고정(순환 예방)
_LO_POS = 6
_PATTERN_SHARE = 0.7     # 패턴 인정 최소 다수 비중(§7 D)
_ATTR_FIELDS = ("area_band", "decade", "price_segment")   # 패턴 리드가 볼 수 있는 속성 전부(사전 고정)
_SHARE_IQR_REF = 0.25    # share(0~1) 계열 놀라움점수의 기준 IQR 대용치(§2.1 "share 편차" 스코어링)
_CONTEXT_SCORE = 0.05    # 맥락 유형 고정 점수 — 슬롯이 남을 때만 선택되도록 작게(§2.1)

_SEOUL_LIMIT, _GU_LIMIT, _COMPLEX_LIMIT = 5, 4, 3


# ── 산술 ──────────────────────────────────────────────────────────────────

def compute_surprise_score(value: float, ref_median: float, ref_iqr: float, n: int) -> float:
    """|값−기준중위| ÷ max(기준IQR, eps) × log(max(n,2)). 유형 내부 우선순위 산정용(표시 안 함)."""
    return abs(value - ref_median) / max(ref_iqr, _EPS) * math.log(max(n, 2))


def _median_iqr(vals: list[float]) -> tuple[float | None, float | None]:
    if not vals:
        return None, None
    s = sorted(vals)
    med = st.median(s)
    iqr = max(be._pctile(s, 0.75) - be._pctile(s, 0.25), 0.0)
    return med, iqr


# ── 포맷(이 모듈 전용 — 다른 렌더러의 _eok 등과 관례만 맞추고 의존하지 않는다) ──

def _trim(s: str) -> str:
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _fmt_eok(v: float | None) -> str:
    return "—" if v is None else _trim(f"{round(v, 2):.2f}") + "억"


def _fmt_man(v: float | None) -> str:
    return "—" if v is None else _trim(f"{round(v, 2):.2f}") + "만원"


def _fmt_ratio(v: float | None) -> str:
    return "—" if v is None else _trim(f"{round(v, 2):.2f}")


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{round(v, 1):.1f}%"


def _fmt_pct_signed(v: float | None) -> str:
    return "—" if v is None else f"{round(v, 1):+.1f}%"


def _direction(v: float, ref: float) -> str:
    """A모델 서수 금지 — 상승/하락 방향만(가장 X 아님). 동률은 낮다측으로 결정론 처리."""
    return "낮다" if v <= ref else "높다"


# ── 그룹핑 / 추출기(구 단위 기준집단 — S/§7 "reference-group statistics") ────

def _group_by_gu(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        gu = r.get("gu")
        if gu:
            out.setdefault(gu, []).append(r)
    return out


def _trend_extractor(r: dict) -> float | None:
    return r.get("molit_trend_pct") if be.passes_rank_gate(r) and r.get("molit_trend_pct") is not None else None


def _jeonse_extractor(r: dict) -> float | None:
    return r.get("jeonse_ratio_complex_pct") if be.passes_jeonse_gate(r) else None


def _maint_extractor(r: dict) -> float | None:
    if be.passes_rank_gate(r) and r.get("maint_fee_won") is not None:
        return r["maint_fee_won"] / 1e4
    return None


def _gongsi_ratio_extractor(r: dict) -> float | None:
    if be.passes_rank_gate(r) and r.get("gongsi_man") and r.get("molit_recent_eok") is not None:
        return r["molit_recent_eok"] * 1e4 / r["gongsi_man"]
    return None


def _gu_value_medians(by_gu: dict[str, list[dict]], extractor) -> dict[str, float]:
    """구별 중위값 — 25구(또는 데이터에 있는 구만) 기준집단의 원소. 표본 없는 구는 제외."""
    out = {}
    for gu, rows in by_gu.items():
        vals = [v for v in (extractor(r) for r in rows) if v is not None]
        if vals:
            out[gu] = round(st.median(vals), 1)
    return out


def _gu_jeonse_stats(gu_rows: list[dict]) -> tuple[float | None, float | None, int, list[float]]:
    vals = [r["jeonse_ratio_complex_pct"] for r in gu_rows if be.passes_jeonse_gate(r)]
    med, iqr = _median_iqr(vals)
    return med, iqr, len(vals), vals


def _gu_turnover_stats(gu_rows: list[dict]) -> tuple[float | None, float | None, int, list[float]]:
    vals = [r["turnover_pct"] for r in gu_rows if be.passes_turnover_gate(r)]
    med, iqr = _median_iqr(vals)
    return med, iqr, len(vals), vals


def _gu_maint_stats(gu_rows: list[dict]) -> tuple[float | None, float | None, int, list[float]]:
    vals = [r["maint_fee_won"] / 1e4 for r in gu_rows if be.passes_rank_gate(r) and r.get("maint_fee_won") is not None]
    med, iqr = _median_iqr(vals)
    return med, iqr, len(vals), vals


def _gu_parking_stats(gu_rows: list[dict]) -> tuple[float | None, float | None, int, list[float]]:
    vals = [r["parking_per_unit"] for r in gu_rows
            if be.passes_rank_gate(r) and r.get("parking_per_unit") is not None]
    med, iqr = _median_iqr(vals)
    return med, iqr, len(vals), vals


def _gu_gongsi_ratio_stats(gu_rows: list[dict]) -> tuple[float | None, float | None, int, list[tuple[str, float]]]:
    named = []
    for r in gu_rows:
        if be.passes_rank_gate(r) and r.get("gongsi_man") and r.get("molit_recent_eok") is not None:
            named.append((r["name"], r["molit_recent_eok"] * 1e4 / r["gongsi_man"]))
    med, iqr = _median_iqr([v for _, v in named])
    return med, iqr, len(named), named


def _attr_share(rows: list[dict], attr: str) -> tuple[str, int, int] | None:
    counts: dict[str, int] = {}
    total = 0
    for r in rows:
        v = r.get(attr)
        if v is None:
            continue
        counts[v] = counts.get(v, 0) + 1
        total += 1
    if not total:
        return None
    val = max(sorted(counts), key=lambda k: counts[k])   # 동률은 값 이름 오름차순(결정론)
    return val, counts[val], total


def _best_pattern_attr(rows: list[dict]) -> tuple[str, str, int, int] | None:
    """_ATTR_FIELDS 중 다수 비중이 _PATTERN_SHARE 이상인 것들 중 비중 최대(동률은 attr 등록 순서) 하나.
    호출측이 표본 크기(|set|) 조건은 미리 게이트한다 — 이 함수는 다수 속성 판정만 담당."""
    cands = []
    for i, attr in enumerate(_ATTR_FIELDS):
        res = _attr_share(rows, attr)
        if res is None:
            continue
        val, cnt, total = res
        share = cnt / total
        if share >= _PATTERN_SHARE:
            cands.append((-share, i, attr, val, cnt, total))
    if not cands:
        return None
    cands.sort()
    _, _, attr, val, cnt, total = cands[0]
    return attr, val, cnt, total


# ── 선택(유형별 최대 1, 점수 desc, family id 로 동률 타이브레이크) ───────────

def _finalize(cands: list[dict], limit: int) -> list[dict]:
    best: dict[str, dict] = {}
    for c in cands:
        if c is None:
            continue
        t = c["type"]
        cur = best.get(t)
        if cur is None or c["score"] > cur["score"] or (c["score"] == cur["score"] and c["family"] < cur["family"]):
            best[t] = c
    ordered = sorted(best.values(), key=lambda c: (-c["score"], c["family"]))
    return ordered[:limit]


# ── 서울 스코프 패밀리(S1~S7) ────────────────────────────────────────────

def _s1(elig: list[dict], asof: str) -> dict | None:
    detm = [r for r in elig if r.get("molit_trend_pct") is not None]
    n = len(detm)
    if n == 0:
        return None
    k = sum(1 for r in detm if r["molit_trend_dir"] == "▲")
    up_vals = [r["molit_trend_pct"] for r in detm if r["molit_trend_dir"] == "▲"]
    if not up_vals:
        return None
    x = round(st.median(up_vals), 1)
    text = (f"추세 판정 가능한 {n}단지 중 {k}곳({_fmt_pct(k / n * 100)})이 "
            f"최근 3개월 중위가 직전 9개월보다 높다. 상승폭 중위 +{_fmt_pct(abs(x))}(기준 {asof}).")
    score = compute_surprise_score(k / n, 0.5, _SHARE_IQR_REF, n)
    return {"type": "변화", "family": "S1", "text": text, "score": score, "n": n,
            "refs": {"N": n, "K": k, "X": x}}


def _s2(by_gu: dict) -> dict | None:
    gu_meds = _gu_value_medians(by_gu, _trend_extractor)
    vals = list(gu_meds.values())
    med, iqr = _median_iqr(vals)
    if med is None:
        return None
    hi, lo = round(med + iqr, 1), round(med - iqr, 1)
    hi_gus = sorted((g for g, v in gu_meds.items() if v > hi), key=lambda g: (-gu_meds[g], g))
    lo_gus = sorted((g for g, v in gu_meds.items() if v < lo), key=lambda g: (gu_meds[g], g))
    if not hi_gus and not lo_gus:
        return None
    parts = []
    if hi_gus:
        parts.append(f"{_fmt_pct_signed(hi)}를 넘는 구는 {'·'.join(hi_gus)}({len(hi_gus)}곳)")
    if lo_gus:
        parts.append(f"{_fmt_pct_signed(lo)} 미만은 {'·'.join(lo_gus)}({len(lo_gus)}곳)")
    text = "구 중위 상승폭이 " + ", ".join(parts) + "이다."
    extreme = max(vals, key=lambda v: abs(v - med))
    score = compute_surprise_score(extreme, med, iqr, len(vals))
    return {"type": "편차", "family": "S2", "text": text, "score": score, "n": len(vals),
            "refs": {"hi": hi, "lo": lo, "hi_gus": hi_gus, "lo_gus": lo_gus}}


def _s3(elig: list[dict], prev_ds: dict | None) -> dict | None:
    vals = [r["molit_trend_pct"] for r in elig if r.get("molit_trend_pct") is not None]
    med, iqr = _median_iqr(vals)
    if med is None:
        return None
    hi = med + iqr
    hot = [r for r in elig if r.get("molit_trend_pct") is not None and r["molit_trend_pct"] > hi]
    if len(hot) < 5:
        return None
    best = _best_pattern_attr(hot)
    if best is None:
        return None
    attr, val, cnt, total = best
    reproduced = False
    if prev_ds is not None:
        prev_elig = [r for r in prev_ds.get("complexes", []) if be.passes_rank_gate(r)]
        prev_vals = [r["molit_trend_pct"] for r in prev_elig if r.get("molit_trend_pct") is not None]
        p_med, p_iqr = _median_iqr(prev_vals)
        if p_med is not None:
            prev_hot = [r for r in prev_elig
                        if r.get("molit_trend_pct") is not None and r["molit_trend_pct"] > p_med + p_iqr]
            prev_best = _best_pattern_attr(prev_hot)
            reproduced = prev_best is not None and prev_best[0] == attr and prev_best[1] == val
    text = f"3/9개월 상승폭이 {_fmt_pct_signed(round(hi, 1))}를 넘는 {total}곳 중 {cnt}곳이 {val}다."
    if not reproduced:
        text = "이번 주 관측: " + text
    score = compute_surprise_score(cnt / total, 0.5, _SHARE_IQR_REF, total)
    return {"type": "패턴", "family": "S3", "text": text, "score": score, "n": total,
            "refs": {"attr": attr, "val": val, "cnt": cnt, "total": total, "reproduced": reproduced}}


def _s4(elig: list[dict]) -> dict | None:
    n = len(elig)
    a = sum(1 for r in elig if r.get("molit_pos_52w") is not None and r["molit_pos_52w"] >= _HI_POS)
    b = sum(1 for r in elig if r.get("molit_pos_52w") is not None and r["molit_pos_52w"] <= _LO_POS)
    if a == 0 and b == 0:
        return None
    text = f"12개월 범위 상단(52주 위치 {_HI_POS}% 이상)에 {a}곳, 하단({_LO_POS}% 이하)에 {b}곳이 있다."
    share = (a + b) / n if n else 0.0
    score = compute_surprise_score(share, 0.5, _SHARE_IQR_REF, n or 2)
    return {"type": "임계", "family": "S4", "text": text, "score": score, "n": n, "refs": {"A": a, "B": b}}


def _s5(by_gu: dict) -> dict | None:
    gu_meds = _gu_value_medians(by_gu, _gongsi_ratio_extractor)
    if not gu_meds:
        return None
    vals = list(gu_meds.values())
    mn, mx = min(vals), max(vals)
    text = (f"실거래÷공시가 배율은 구 중위 {_fmt_ratio(mn)}~{_fmt_ratio(mx)}배"
            f"(공시가 기준일 1월 1일, 실거래는 12개월 중위 — 시점이 다르다).")
    return {"type": "맥락", "family": "S5", "text": text, "score": _CONTEXT_SCORE, "n": len(vals),
            "refs": {"min": round(mn, 2), "max": round(mx, 2)}}


def _s6(by_gu: dict, cx: list[dict]) -> dict | None:
    gu_meds = _gu_value_medians(by_gu, _jeonse_extractor)
    vals = list(gu_meds.values())
    med, iqr = _median_iqr(vals)
    over95 = sum(1 for r in cx if (r.get("jeonse_n") or 0) >= 5
                 and r.get("jeonse_ratio_complex_pct") is not None and r["jeonse_ratio_complex_pct"] > 95)
    if med is None:
        if not over95:
            return None
        return {"type": "편차", "family": "S6", "text": f"전세가율 95% 초과 단지는 서울 {over95}곳(단지명 미표기).",
                "score": _CONTEXT_SCORE, "n": 0, "refs": {"over95": over95}}
    hi, lo = round(med + iqr, 1), round(med - iqr, 1)
    hi_gus = sorted((g for g, v in gu_meds.items() if v > hi), key=lambda g: (-gu_meds[g], g))
    lo_gus = sorted((g for g, v in gu_meds.items() if v < lo), key=lambda g: (gu_meds[g], g))
    if not hi_gus and not lo_gus and not over95:
        return None
    parts = []
    if hi_gus:
        parts.append(f"{_fmt_pct(hi)}를 넘는 구는 {'·'.join(hi_gus)}({len(hi_gus)}곳)")
    if lo_gus:
        parts.append(f"{_fmt_pct(lo)} 미만은 {'·'.join(lo_gus)}({len(lo_gus)}곳)")
    text = ("구 중위 전세가율이 " + ", ".join(parts) + "이다." if parts else "구 중위 전세가율은 구간 편차가 작다.")
    text += f" 전세가율 95% 초과 단지는 서울 {over95}곳(단지명 미표기)."
    extreme = max(vals, key=lambda v: abs(v - med)) if vals else med
    score = compute_surprise_score(extreme, med, iqr, len(vals))
    return {"type": "편차", "family": "S6", "text": text, "score": score, "n": len(vals),
            "refs": {"hi": hi, "lo": lo, "over95": over95}}


def _s7(by_gu: dict, cx: list[dict]) -> dict | None:
    gu_meds = _gu_value_medians(by_gu, _maint_extractor)
    if not gu_meds:
        return None
    vals = list(gu_meds.values())
    med, iqr = _median_iqr(vals)
    hi, lo = round(med + iqr, 1), round(med - iqr, 1)
    hi_gus = sorted((g for g, v in gu_meds.items() if v > hi), key=lambda g: (-gu_meds[g], g))
    lo_gus = sorted((g for g, v in gu_meds.items() if v < lo), key=lambda g: (gu_meds[g], g))
    if not hi_gus and not lo_gus:
        return None
    base_elig = [r for r in cx if be.passes_rank_gate(r)]
    total = len(base_elig)
    covered = sum(1 for r in base_elig if r.get("maint_fee_won") is not None)
    parts = []
    if hi_gus:
        parts.append(f"{_fmt_man(hi)}를 넘는 구는 {'·'.join(hi_gus)}({len(hi_gus)}곳)")
    if lo_gus:
        parts.append(f"{_fmt_man(lo)} 미만은 {'·'.join(lo_gus)}({len(lo_gus)}곳)")
    text = "구 중위 관리비가 " + ", ".join(parts) + f"이다(K-apt, {covered}/{total}단지 기준)."
    extreme = max(vals, key=lambda v: abs(v - med))
    score = compute_surprise_score(extreme, med, iqr, len(vals))
    return {"type": "편차", "family": "S7", "text": text, "score": score, "n": len(vals),
            "refs": {"hi": hi, "lo": lo, "covered": covered, "total": total}}


def _seoul_candidates(cx: list[dict], prev_ds: dict | None, asof: str) -> list[dict | None]:
    elig = [r for r in cx if be.passes_rank_gate(r)]
    by_gu = _group_by_gu(cx)
    return [_s1(elig, asof), _s2(by_gu), _s3(elig, prev_ds), _s4(elig),
            _s5(by_gu), _s6(by_gu, cx), _s7(by_gu, cx)]


# ── 구 스코프 패밀리(G1~G6) ──────────────────────────────────────────────

def _seoul_trend_up_median(cx: list[dict]) -> float | None:
    vals = [r["molit_trend_pct"] for r in cx
            if be.passes_rank_gate(r) and r.get("molit_trend_dir") == "▲" and r.get("molit_trend_pct") is not None]
    return round(st.median(vals), 1) if vals else None


def _g1(gu: str, gu_rows: list[dict], cx: list[dict]) -> dict | None:
    base = [r for r in gu_rows if be.passes_rank_gate(r)]
    if not base or not any(r.get("gu") != gu for r in cx):
        return None
    detm = [r for r in base if r.get("molit_trend_pct") is not None]
    if not detm:
        return None
    up_vals = [r["molit_trend_pct"] for r in detm if r["molit_trend_dir"] == "▲"]
    if not up_vals:
        return None
    y = _seoul_trend_up_median(cx)
    if y is None:
        return None
    u = len(up_vals)
    x = round(st.median(up_vals), 1)
    text = (f"{gu} {len(base)}단지 중 추세 판정 {len(detm)}곳, 그중 상승 {u}곳. "
            f"상승폭 중위 +{_fmt_pct(abs(x))}(서울 +{_fmt_pct(abs(y))}).")
    score = compute_surprise_score(u / len(detm), 0.5, _SHARE_IQR_REF, len(detm))
    return {"type": "변화", "family": "G1", "text": text, "score": score, "n": len(detm),
            "refs": {"N": len(base), "K": len(detm), "U": u, "X": x, "Y": y}}


def _g2(gu: str, gu_rows: list[dict], prev_ds: dict | None) -> dict | None:
    base = [r for r in gu_rows if be.passes_rank_gate(r)]
    vals = [r["molit_trend_pct"] for r in base if r.get("molit_trend_pct") is not None]
    med, iqr = _median_iqr(vals)
    if med is None:
        return None
    hi = med + iqr
    hot = sorted((r for r in base if r.get("molit_trend_pct") is not None and r["molit_trend_pct"] > hi),
                 key=lambda r: (-r["molit_trend_pct"], r["name"]))
    k = len(hot)
    if k < 3:
        return None
    names_txt = ", ".join(f"{r['name']} {_fmt_pct_signed(r['molit_trend_pct'])}" for r in hot)
    text = f"상승폭이 {_fmt_pct_signed(round(hi, 1))}를 넘는 {k}곳({names_txt})"
    refs = {"K": k, "hi": round(hi, 1)}
    best = _best_pattern_attr(hot)
    if best is not None:
        attr, val, cnt, total = best
        text += f"이 모두 {val}다." if cnt == total else f" 중 {cnt}곳이 {val}다."
        reproduced = False
        if prev_ds is not None:
            prev_rows = [r for r in prev_ds.get("complexes", []) if r.get("gu") == gu]
            prev_base = [r for r in prev_rows if be.passes_rank_gate(r)]
            prev_vals = [r["molit_trend_pct"] for r in prev_base if r.get("molit_trend_pct") is not None]
            p_med, p_iqr = _median_iqr(prev_vals)
            if p_med is not None:
                prev_hot = [r for r in prev_base
                            if r.get("molit_trend_pct") is not None and r["molit_trend_pct"] > p_med + p_iqr]
                prev_best = _best_pattern_attr(prev_hot)
                reproduced = prev_best is not None and prev_best[0] == attr and prev_best[1] == val
        if not reproduced:
            text = "이번 주 관측: " + text
        refs["reproduced"] = reproduced
    else:
        text += "."
    score = compute_surprise_score(k / len(base), 0.5, _SHARE_IQR_REF, len(base))
    return {"type": "임계", "family": "G2", "text": text, "score": score, "n": k, "refs": refs}


def _g3(gu: str, gu_rows: list[dict], cx: list[dict]) -> dict | None:
    gm, _, n, _vals = _gu_jeonse_stats(gu_rows)
    if gm is None or n == 0:
        return None
    if not any(r.get("gu") != gu for r in cx):
        return None
    by_gu_all = _group_by_gu(cx)
    seoul_gu_meds = _gu_value_medians(by_gu_all, _jeonse_extractor)
    if not seoul_gu_meds:
        return None
    sm = round(st.median(list(seoul_gu_meds.values())), 1)
    named = sorted(((r["name"], r["jeonse_ratio_complex_pct"]) for r in gu_rows if be.passes_jeonse_gate(r)),
                    key=lambda t: t[1])
    min_name, min_v = named[0]
    max_name, max_v = named[-1]
    over95 = sum(1 for r in gu_rows if (r.get("jeonse_n") or 0) >= 5
                 and r.get("jeonse_ratio_complex_pct") is not None and r["jeonse_ratio_complex_pct"] > 95)
    text = (f"전세가율은 {min_name} {_fmt_pct(min_v)}부터 {max_name} {_fmt_pct(max_v)}까지"
            f"(전세표본 5건 이상). 구 중위 {_fmt_pct(gm)}, 서울 구 중위 {_fmt_pct(sm)}.")
    if over95:
        text += f" 95% 초과 {over95}곳은 단지명을 표기하지 않는다."
    _, s_iqr = _median_iqr(list(seoul_gu_meds.values()))
    score = compute_surprise_score(gm, sm, s_iqr, len(seoul_gu_meds))
    return {"type": "편차", "family": "G3", "text": text, "score": score, "n": n,
            "refs": {"gu_median": gm, "seoul_gu_median": sm, "over95": over95}}


def _g4(gu_rows: list[dict]) -> dict | None:
    maint_named = sorted(((r["name"], r["maint_fee_won"] / 1e4) for r in gu_rows
                           if be.passes_rank_gate(r) and r.get("maint_fee_won") is not None), key=lambda t: t[1])
    if not maint_named:
        return None
    mn_name, mn_v = maint_named[0]
    mx_name, mx_v = maint_named[-1]
    ratio = round(mx_v / mn_v, 2) if mn_v else None
    covered = len(maint_named)
    text = (f"관리비는 {mn_name} {_fmt_man(mn_v)}부터 {mx_name} {_fmt_man(mx_v)}까지 "
            f"{_fmt_ratio(ratio)}배 차이(K-apt 월, {covered}단지).")
    park_named = sorted(((r["name"], r["parking_per_unit"]) for r in gu_rows
                          if be.passes_rank_gate(r) and r.get("parking_per_unit") is not None), key=lambda t: t[1])
    if park_named:
        pmn_name, pmn_v = park_named[0]
        pmx_name, pmx_v = park_named[-1]
        text += f" 세대당 주차 {pmn_name} {pmn_v:g}대 ~ {pmx_name} {pmx_v:g}대."
    med, iqr = _median_iqr([v for _, v in maint_named])
    score = compute_surprise_score(mx_v, med, iqr, covered)
    return {"type": "편차", "family": "G4", "text": text, "score": score, "n": covered,
            "refs": {"min": mn_v, "max": mx_v, "ratio": ratio}}


def _g5(gu_rows: list[dict]) -> dict | None:
    med, _, n, named = _gu_gongsi_ratio_stats(gu_rows)
    if med is None or not named:
        return None
    named_sorted = sorted(named, key=lambda t: t[1])
    mn_name, mn_v = named_sorted[0]
    mx_name, mx_v = named_sorted[-1]
    text = (f"실거래÷공시가 배율은 {mn_name} {_fmt_ratio(mn_v)}배부터 {mx_name} {_fmt_ratio(mx_v)}배까지"
            f"(공시가 기준일 1월 1일, 실거래는 12개월 중위 — 시점이 다르다).")
    return {"type": "맥락", "family": "G5", "text": text, "score": _CONTEXT_SCORE, "n": n,
            "refs": {"min": round(mn_v, 2), "max": round(mx_v, 2)}}


def _g6(gu: str, gu_rows: list[dict], cx: list[dict]) -> dict | None:
    g_med = be.compute_gu_median(gu_rows)
    if g_med is None or not any(r.get("gu") != gu for r in cx):
        return None
    by_gu_all = _group_by_gu(cx)
    all_meds = {g: be.compute_gu_median(rows) for g, rows in by_gu_all.items()}
    all_meds = {g: v for g, v in all_meds.items() if v is not None}
    if gu not in all_meds or len(all_meds) < 2:
        return None
    vals = list(all_meds.values())
    s_med, s_iqr = _median_iqr(vals)
    d = round(abs(g_med - s_med), 2)
    k = sum(1 for v in vals if v < g_med)
    text = (f"구 중위 {_fmt_eok(g_med)}은 25구 중위 {_fmt_eok(s_med)}보다 {_fmt_eok(d)} {_direction(g_med, s_med)}. "
            f"이보다 낮은 구는 {k}곳.")
    score = compute_surprise_score(g_med, s_med, s_iqr, len(vals))
    return {"type": "편차", "family": "G6", "text": text, "score": score, "n": len(vals),
            "refs": {"gu_median": g_med, "seoul_median": s_med, "k_lower": k}}


def _gu_candidates(gu: str, gu_rows: list[dict], cx: list[dict], prev_ds: dict | None) -> list[dict | None]:
    return [_g1(gu, gu_rows, cx), _g2(gu, gu_rows, prev_ds), _g3(gu, gu_rows, cx),
            _g4(gu_rows), _g5(gu_rows), _g6(gu, gu_rows, cx)]


# ── 단지 스코프 패밀리(C1~C7) ────────────────────────────────────────────

def _c1(row: dict, peers: list[dict]) -> dict | None:
    v = row.get("molit_recent_eok")
    if v is None or not peers:
        return None
    peer_vals = sorted(p["molit_recent_eok"] for p in peers if p.get("molit_recent_eok") is not None)
    if not peer_vals:
        return None
    pm, iqr = _median_iqr(peer_vals)
    band = row.get("area_band") or (be.area_band(row["area_m2"]) if row.get("area_m2") is not None else "—")
    p_n = len(peer_vals)
    k = sum(1 for x in peer_vals if x < v)
    d = round(abs(v - pm), 2)
    k_txt = "이보다 낮은 피어는 없다." if k == 0 else f"이보다 낮은 피어는 {k}곳."
    text = f"{_fmt_eok(v)}은 같은 구 {band} 피어 {p_n}곳 중위({_fmt_eok(pm)})보다 {_fmt_eok(d)} {_direction(v, pm)}. {k_txt}"
    score = compute_surprise_score(v, pm, iqr, p_n)
    return {"type": "편차", "family": "C1", "text": text, "score": score, "n": p_n,
            "refs": {"peer_median": pm, "peer_n": p_n, "k_lower": k}}


def _c2(row: dict, peers: list[dict]) -> dict | None:
    nr, npb = row.get("molit_trend_n_recent"), row.get("molit_trend_n_prior")
    if nr is None or npb is None or nr < 5 or npb < 5:
        return None
    x = row.get("molit_trend_pct")
    if x is None:
        return None
    peer_trends = [p["molit_trend_pct"] for p in peers if p.get("molit_trend_pct") is not None]
    q = len(peer_trends)
    text = f"최근 3개월 중위({nr}건)는 직전 9개월 중위({npb}건)보다 {_fmt_pct(abs(x))} {_direction(x, 0)}."
    if q:
        pm = round(st.median(peer_trends), 1)
        text += f" 추세가 있는 피어 {q}곳 중위는 {_fmt_pct(abs(pm))}."
        ref_med, ref_iqr = _median_iqr(peer_trends)
    else:
        text += " 추세가 있는 피어 없음."
        ref_med, ref_iqr = 0.0, 1.0
    score = compute_surprise_score(x, ref_med, ref_iqr, max(q, 1))
    return {"type": "변화", "family": "C2", "text": text, "score": score, "n": q,
            "refs": {"n_recent": nr, "n_prior": npb, "peer_n": q}}


def _c3(row: dict, gu_rows: list[dict]) -> dict | None:
    jn = row.get("jeonse_n")
    ratio = row.get("jeonse_ratio_complex_pct")
    if jn is None or jn < 5 or ratio is None or ratio > 95:
        return None
    gm, iqr, n, _vals = _gu_jeonse_stats(gu_rows)
    if gm is None:
        return None
    gap = row.get("gap_eok")
    text = f"전세가율 {_fmt_pct(ratio)}는 구 중위 {_fmt_pct(gm)}보다 {_direction(ratio, gm)}."
    if gap is not None:
        text += f" 매매–전세 갭 {_fmt_eok(gap)}."
    score = compute_surprise_score(ratio, gm, iqr, n)
    return {"type": "편차", "family": "C3", "text": text, "score": score, "n": n, "refs": {"gu_median": gm}}


def _c4(row: dict, gu_rows: list[dict]) -> dict | None:
    if not be.passes_turnover_gate(row):
        return None
    t, p = row.get("trade_annual"), row.get("turnover_pct")
    gm, iqr, n, _vals = _gu_turnover_stats(gu_rows)
    if gm is None or t is None:
        return None
    text = f"12개월 거래 {t:g}건, 회전율 {_fmt_pct(p)}는 구 중위 {_fmt_pct(gm)}보다 {_direction(p, gm)}."
    score = compute_surprise_score(p, gm, iqr, n)
    return {"type": "편차", "family": "C4", "text": text, "score": score, "n": n, "refs": {"gu_median": gm}}


def _c5(row: dict, gu_rows: list[dict]) -> dict | None:
    m = row.get("maint_fee_won")
    if m is None or not be.passes_rank_gate(row):
        return None
    m_man = m / 1e4
    gm, iqr, n, _vals = _gu_maint_stats(gu_rows)
    if gm is None:
        return None
    d_pct = round(abs(m_man - gm) / gm * 100, 1) if gm else None
    text = f"관리비 {_fmt_man(m_man)}/월은 구 중위 {_fmt_man(gm)}보다 {_fmt_pct(d_pct)} {_direction(m_man, gm)}."
    pk = row.get("parking_per_unit")
    gpk, _pk_iqr, pk_n, _pk_vals = _gu_parking_stats(gu_rows)
    if pk is not None and gpk is not None and pk_n:
        text += f" 세대당 주차 {pk:g}대(구 중위 {gpk:g}대)."
    score = compute_surprise_score(m_man, gm, iqr, n)
    return {"type": "편차", "family": "C5", "text": text, "score": score, "n": n, "refs": {"gu_median_man": gm}}


def _c6(row: dict, gu_rows: list[dict]) -> dict | None:
    g, med = row.get("gongsi_man"), row.get("molit_recent_eok")
    if not g or med is None:
        return None
    r = med * 1e4 / g
    gmed, _iqr, n, _named = _gu_gongsi_ratio_stats(gu_rows)
    text = f"공시가 {_fmt_eok(g / 1e4)}, 실거래÷공시가 {_fmt_ratio(r)}배"
    text += (f"(구 중위 {_fmt_ratio(gmed)}배; 공시가 1월 1일 기준·실거래 12개월 중위)." if gmed is not None
             else "(공시가 1월 1일 기준·실거래 12개월 중위).")
    return {"type": "맥락", "family": "C6", "text": text, "score": _CONTEXT_SCORE, "n": n or 1,
            "refs": {"ratio": round(r, 2), "gu_ratio": gmed}}


def _c7(row: dict) -> dict | None:
    pos = row.get("molit_pos_52w")
    if pos is None:
        return None
    if pos >= 99:
        text = f"최근 3개월 체결 중위가 12개월 범위 상단({pos:g}%)에 있다."
    elif pos <= 6:
        text = f"최근 3개월 체결 중위가 12개월 범위 하단({pos:g}%)에 있다."
    else:
        return None
    n_ref = row.get("molit_n") or 2
    score = compute_surprise_score(pos, 50.0, 25.0, n_ref)
    return {"type": "임계", "family": "C7", "text": text, "score": score, "n": n_ref, "refs": {"pos": pos}}


def _complex_candidates(row: dict, peers: list[dict], gu_rows: list[dict]) -> list[dict | None]:
    return [_c1(row, peers), _c2(row, peers), _c3(row, gu_rows), _c4(row, gu_rows),
            _c5(row, gu_rows), _c6(row, gu_rows), _c7(row)]


# ── 공개 API ─────────────────────────────────────────────────────────────

def build_fact_leads(ds: dict, scope: str, key=None, *, peers: list[dict] | None = None,
                      prev_ds: dict | None = None, limit: int | None = None) -> list[dict]:
    """scope별 후보 사실을 만들어 유형(LEAD_TYPES)당 최대 1개, 점수 desc 로 최대 limit 개 반환.
    ds 는 dataset.json 형태({"complexes":[...], "data_asof":...}) — scope='gu' 는 gu 하나만 담은
    합성 ds(예: {"complexes": 그 구 rows})도 허용하되 그 경우 서울 전역 참조가 필요한 패밀리는
    자동으로 스킵된다(다른 구 표본이 ds 안에 없다는 사실 자체로 판정, 별도 플래그 불필요)."""
    ds = ds or {}
    cx = ds.get("complexes", [])
    asof = ds.get("data_asof") or ds.get("generated") or "-"

    if scope == "seoul":
        limit = _SEOUL_LIMIT if limit is None else limit
        cands = _seoul_candidates(cx, prev_ds, asof)
    elif scope == "gu":
        limit = _GU_LIMIT if limit is None else limit
        gu = key
        gu_rows = [r for r in cx if r.get("gu") == gu]
        cands = _gu_candidates(gu, gu_rows, cx, prev_ds)
    elif scope == "complex":
        limit = _COMPLEX_LIMIT if limit is None else limit
        row = key or {}
        gu_rows = [r for r in cx if r.get("gu") == row.get("gu")] if cx else []
        cands = _complex_candidates(row, peers or [], gu_rows)
    else:
        raise ValueError(f"[fact_lead] 알 수 없는 scope: {scope!r}")

    leads = _finalize(cands, limit)
    for ld in leads:
        assert_lead_wording_ok(ld["text"], f"fact_lead:{scope}:{ld['family']}")
    return leads


def render_lead_block(leads: list[dict], heading: str = "이 페이지에서만 알 수 있는 것") -> str:
    """구허브/단지페이지/다이제스트 site 판용 — leads 가 비면 빈 문자열(섹션 자체를 렌더하지 않는다)."""
    if not leads:
        return ""
    items = "".join(f"<li>{ld['text']}</li>" for ld in leads)
    return (f'<section class="lead"><h2>{heading}</h2><ul>{items}</ul>'
            '<p class="lead-note">국토부 공공 실거래 기준 결정론 산출 — 자체 점수·순위 아님.</p></section>')


def render_lead_lines(leads: list[dict]) -> list[str]:
    """텔레그램/티스토리용 평문 — 호출측이 각 줄을 <p>…</p> 로 감싸거나 개행으로 join."""
    return [ld["text"] for ld in leads]
