"""★단일 진실원천 리포트 재생성 — 손으로 친 명령의 '인자 누락'·'키 불일치'로 공백이 조용히 실리는
재발 방지. 모든 enrich 인자를 항상 고정 배선 + 생성 전 COVERAGE GATE 로 차원별 공백을 *시끄럽게* 고발.
(Silent Success / Loud Failure: 임계 미달이면 누가·어떤 차원·어떤 키로 비었는지 출력 후 중단.)

사용: python3 regen_reports.py            # 게이트 통과해야 생성
      python3 regen_reports.py --force    # 게이트 미달도 강행(블로커 의식적 수용 시)
"""
import sys, json, subprocess
from pathlib import Path
from agent_realestate import config
config.load_env_file()
from agent_realestate.cache import store
from agent_realestate.collectors.naver_live import load_candidates
from agent_realestate.analysts.trend import compute_trend

EX = Path("examples")
PROFILE = "examples/profile_user.json"

# ── 고정 입력 배선 (단일 진실원천) ──────────────────────────────────────────
UNIVERSE = "examples/candidates_universe155_20260603.json"   # 통합(아파트+주상복합+재발견)
JUSANG = "examples/candidates_jusang_injected_20260603.json"  # 주상복합 단독(=universe slice, infra 주입본)
REFERENCE = UNIVERSE                                          # v3 고정기준집합(동일단지 동일점수)
REVIEWS = "examples/reviews_full_20260604.json"      # apt+jusang+재발견·아파트 backfill 병합본
LOCATION = "examples/location_apt_jusang_20260603.json"
COMPSET = "examples/compset_10gu_byband_20260603.json"
COVERAGE = "examples/discovery_audit_20260603.json"

REPORTS = [
    ("통합(155단지)", UNIVERSE, REFERENCE, "report/scan/scan_universe155_20260603.html", COVERAGE),
    ("주상복합 단독", JUSANG, REFERENCE, "report/scan/scan_jusang-only_20260603.html", None),
]

# COVERAGE GATE — 두 종류로 구분(근본설계):
#   HARD(차단) = *우리가 통제* 하는 차원. 공백 = 인자누락/enrich 미실행 우리 책임 → 리포트 차단.
#   SOFT(경고) = *외부 데이터 가용성* 한계. 전세=네이버 현재 전세매물 등록분만, 추세=MOLIT 실거래 보유분.
#                존재하면 표시·없으면 공백이 정직 → 시끄럽게 보고하되 차단하진 않음.
THRESH_HARD = {"학군": 0.85, "후기": 0.78, "base-rate": 0.95, "용적률(실측)": 0.85, "건폐율": 0.80}
THRESH_SOFT = {"전세가율": 0.30, "실거래추세": 0.30}
THRESH = {**THRESH_HARD, **THRESH_SOFT}
FAR_DEFAULTS = {180.0, 250.0}   # build_rediscover 하드코딩 추정값 = 미실측 표식


def _gu(c):
    d = c.district or ""
    return d.split()[-1] if d else ""


def _nm(c):
    g = _gu(c)
    return f"[{g}]{c.listing.complex_name}" if g else c.listing.complex_name


def _seg(c):
    n = c.listing.complex_name
    return "주복" if "[주상복합]" in n else "재발견" if "[재발견]" in n else "아파트"


def audit_coverage(input_path):
    cands = load_candidates(input_path)
    conn = store.connect()
    reviews = json.load(open(REVIEWS))
    location = json.load(open(LOCATION))
    compset = json.load(open(COMPSET))
    sg_known = set(compset.get("assign", {}).values()) | {k.split("|")[0] for k in compset.get("longrun", {})}
    dims = {}

    def has_review(c):
        return bool(reviews.get(_nm(c)) or reviews.get(c.listing.complex_name))

    def has_hak(c):
        return bool((location.get(_nm(c), {}) or location.get(c.listing.complex_name, {})).get("school_grade")
                    or c.hakgun_score is not None)

    def has_jeonse(c):
        return c.jeonse_krw is not None

    def has_baserate(c):
        return bool(c.saenghwalgwon and c.saenghwalgwon in sg_known)

    def has_trend(c):
        return bool(compute_trend(store.get_price_series(conn, c.listing.complex_name, c.listing.area_exclusive_m2)))

    def has_far_real(c):
        return c.far_pct not in FAR_DEFAULTS

    def has_bcr(c):
        return c.bcr_pct is not None

    checks = {"학군": has_hak, "후기": has_review, "전세가율": has_jeonse,
              "base-rate": has_baserate, "실거래추세": has_trend, "용적률(실측)": has_far_real,
              "건폐율": has_bcr}
    for dim, fn in checks.items():
        miss = [c for c in cands if not fn(c)]
        by_seg = {}
        for c in cands:
            s = _seg(c)
            by_seg.setdefault(s, [0, 0])
            by_seg[s][1] += 1
            if fn(c):
                by_seg[s][0] += 1
        dims[dim] = {"have": len(cands) - len(miss), "total": len(cands),
                     "miss_names": [_nm(c) for c in miss], "by_seg": by_seg}
    return dims


def print_gate(label, dims):
    ok = True
    print(f"\n═══ COVERAGE GATE — {label} ═══")
    for dim, d in dims.items():
        ratio = d["have"] / d["total"]
        seg = " ".join(f"{s}:{v[0]}/{v[1]}" for s, v in sorted(d["by_seg"].items()))
        hard = dim in THRESH_HARD
        th = THRESH.get(dim, 0)
        seg_zero = hard and any(v[0] == 0 for v in d["by_seg"].values())   # 세그먼트0 차단은 HARD만
        pass_ = ratio >= th and not seg_zero
        if hard:
            ok = ok and pass_
        mark = ("✅" if pass_ else "❌") if hard else ("✅" if pass_ else "⚠️")
        kind = "HARD" if hard else "SOFT"
        print(f"  {mark} [{kind}] {dim:<10} {d['have']:>3}/{d['total']} ({ratio*100:4.0f}%, 임계{th*100:.0f}%)  [{seg}]")
        if not pass_:
            ms = d["miss_names"]
            tag = "차단 누락" if hard else "외부데이터 한계(미차단)"
            print(f"       └ {tag} {len(ms)}개: {', '.join(ms[:10])}{' …' if len(ms) > 10 else ''}")
    return ok


def audit_hygiene(input_path):
    """위생 게이트 (3차 감사 UX#2·6·24, 2026-06-11) — 입력 중복·잡태그가 리포트 전 섹션에
    유령 행으로 번지는 것을 빌드에서 차단. (해태보라매타워 이중행 = §0~별지 36회 중복 사례)"""
    cands = load_candidates(input_path)
    errs = []
    seen = {}
    for c in cands:
        k = (c.district, c.listing.complex_name, c.listing.area_exclusive_m2)
        if k in seen:
            errs.append(f"중복 후보 행: {k} — 수집 중복(유닛/매물 차이)이면 한 행만 남길 것")
        seen[k] = True
        nm = c.listing.complex_name
        core = nm.split("[")[0]
        if f"[{core}]" in nm:
            errs.append(f"잡태그 이름: {nm} — 수집 시 단지명이 태그로 중복됨")
    return errs


def audit_output_hygiene(out_path):
    """산출 HTML 위생 — Python None/빈 placeholder 가 사용자에게 노출되는 것 차단(UX#6)."""
    html = open(out_path, encoding="utf-8").read()
    errs = []
    for pat, desc in (("Nonem", "고도 None 노출"), (">None<", "None 셀 노출")):
        if pat in html:
            errs.append(f"{desc} ('{pat}' {html.count(pat)}회)")
    return errs


def main():
    force = "--force" in sys.argv
    all_ok = True
    for label, inp, _ref, _out, _cov in REPORTS:
        dims = audit_coverage(inp)
        all_ok = print_gate(label, dims) and all_ok
        hyg = audit_hygiene(inp)
        if hyg:
            all_ok = False
            print(f"  ❌ [HYGIENE] {label}: " + " | ".join(hyg))
    if not all_ok and not force:
        print("\n❌ COVERAGE GATE 실패 — 위 누락을 메우거나(enrich 재수집) --force 로 강행. 리포트 미생성.")
        sys.exit(1)
    if not all_ok:
        print("\n⚠️ --force: 게이트 미달이나 강행 생성(블로커 의식적 수용).")
    for label, inp, ref, out, cov in REPORTS:
        cmd = ["python3", "-m", "agent_realestate.cli", "report", "--profile", PROFILE,
               "--input", inp, "--reference", ref, "--reviews", REVIEWS, "--location", LOCATION,
               "--compset", COMPSET, "--no-email", "--out", out]
        if cov:
            cmd += ["--coverage", cov]
        print(f"\n▶ 생성: {label} → {out}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        print((r.stdout or r.stderr).splitlines()[0] if (r.stdout or r.stderr) else "(무출력)")
        oh = audit_output_hygiene(out)
        if oh:
            print(f"  ⚠️ [OUTPUT HYGIENE] {label}: " + " | ".join(oh))


if __name__ == "__main__":
    main()
