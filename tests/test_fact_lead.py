"""사실 리드(FactLead) 단위테스트(2026-09-06) — 스냅샷(정확 문장)·결정론·가드·게이트·prev_ds 재현·
실데이터 스모크. 대상: blog.fact_lead(build_fact_leads/render_lead_block/render_lead_lines/
compute_surprise_score), blog.wording_guard(assert_lead_wording_ok).

스냅샷 기대값은 이 파일과 별개인 순수 산술(중위·IQR·비중)로 손으로 도출했다 — fact_lead 내부 함수를
그대로 불러 "기대값"을 만들지 않는다(그러면 회귀를 못 잡는다). 정확한 도출 과정은 각 fixture 주석 참고.
"""
from __future__ import annotations
import json

import pytest

import blog.complex_page as cp
import blog.daily_digest as dd
import blog.gu_hub as gh
import blog.weekly_summary as ws
from blog.fact_lead import build_fact_leads, render_lead_block, render_lead_lines
from blog.wording_guard import assert_lead_wording_ok


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, product_type="아파트", area_m2=59.0, molit_n=15)
    base.update(kw)
    return base


# ── 스냅샷 fixture: 3구 × 6단지(가구/나구/다구, 각 가1~6/나1~6/다1~6) ────────
# 산출 과정은 report/2026-09-06-blog-insight-redesign-brief.md 브리핑 워커 A 배정 검증 스크래치
# (scratchpad/design_fixture.py, 세션 로컬)에서 표준 통계식(중위·type-7 백분위수)으로 손으로 검산했다.

def _snapshot_ds() -> dict:
    rows = [
        _row("가구", "가1", molit_trend_dir="▲", molit_trend_pct=10.0, molit_recent_eok=8.0, molit_pos_52w=50,
             jeonse_n=8, jeonse_ratio_complex_pct=60.0, gap_eok=3.2),
        _row("가구", "가2", molit_trend_dir="▲", molit_trend_pct=20.0, molit_recent_eok=9.0, molit_pos_52w=60,
             jeonse_n=8, jeonse_ratio_complex_pct=65.0, gap_eok=3.15),
        _row("가구", "가3", molit_trend_dir="▲", molit_trend_pct=30.0, molit_recent_eok=10.0, molit_pos_52w=99,
             jeonse_n=8, jeonse_ratio_complex_pct=70.0, gap_eok=3.0),
        _row("가구", "가4", molit_trend_dir="▼", molit_trend_pct=-5.0, molit_recent_eok=7.0, molit_pos_52w=40,
             jeonse_n=8, jeonse_ratio_complex_pct=50.0, gap_eok=3.5),
        _row("가구", "가5", molit_trend_dir="—", molit_trend_pct=0.0, molit_recent_eok=6.0, molit_pos_52w=6,
             jeonse_n=8, jeonse_ratio_complex_pct=96.0, gap_eok=0.24),   # ratio>95 — 이름 비표기 대상
        _row("가구", "가6", molit_trend_dir="▲", molit_trend_pct=15.0, molit_recent_eok=6.35, molit_pos_52w=74,
             jeonse_n=8, jeonse_ratio_complex_pct=58.7, gap_eok=2.62),   # C1/C3 대상 행("중앙하이츠" 유사 위치)

        _row("나구", "나1", molit_trend_dir="▲", molit_trend_pct=70.0, molit_recent_eok=15.0, molit_pos_52w=80),
        _row("나구", "나2", molit_trend_dir="▲", molit_trend_pct=80.0, molit_recent_eok=16.0, molit_pos_52w=85),
        _row("나구", "나3", molit_trend_dir="▼", molit_trend_pct=-10.0, molit_recent_eok=14.0, molit_pos_52w=20),
        _row("나구", "나4", molit_trend_dir="—", molit_trend_pct=0.0, molit_recent_eok=13.0, molit_pos_52w=30),
        _row("나구", "나5", molit_trend_dir=None, molit_trend_pct=None, molit_recent_eok=12.0),
        _row("나구", "나6", molit_trend_dir=None, molit_trend_pct=None, molit_recent_eok=11.0),

        _row("다구", "다1", molit_trend_dir="▼", molit_trend_pct=-30.0, molit_recent_eok=3.0, molit_pos_52w=10),
        _row("다구", "다2", molit_trend_dir="▼", molit_trend_pct=-25.0, molit_recent_eok=3.5, molit_pos_52w=15),
        _row("다구", "다3", molit_trend_dir="—", molit_trend_pct=0.0, molit_recent_eok=4.0, molit_pos_52w=50),
        _row("다구", "다4", molit_trend_dir=None, molit_trend_pct=None, molit_recent_eok=3.2),
        _row("다구", "다5", molit_trend_dir=None, molit_trend_pct=None, molit_recent_eok=3.8),
        _row("다구", "다6", molit_trend_dir=None, molit_trend_pct=None, molit_recent_eok=4.5),
    ]
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-09-06", "generated": "2026-09-06"}


def _leads_by_family(leads: list[dict]) -> dict[str, dict]:
    return {ld["family"]: ld for ld in leads}


# ── 1. 스냅샷(정확 문장) ─────────────────────────────────────────────────

def test_snapshot_s1_seoul_change():
    fam = _leads_by_family(build_fact_leads(_snapshot_ds(), "seoul"))
    assert fam["S1"]["text"] == (
        "추세 판정 가능한 13단지 중 6곳(46.2%)이 최근 3개월 중위가 직전 9개월보다 높다. "
        "상승폭 중위 +25.0%(기준 2026-09-06)."
    )


def test_snapshot_g1_gu_change():
    fam = _leads_by_family(build_fact_leads(_snapshot_ds(), "gu", "가구"))
    assert fam["G1"]["text"] == (
        "가구 6단지 중 추세 판정 6곳, 그중 상승 4곳. 상승폭 중위 +17.5%(서울 +25.0%)."
    )


def test_snapshot_g3_gu_jeonse():
    fam = _leads_by_family(build_fact_leads(_snapshot_ds(), "gu", "가구"))
    assert fam["G3"]["text"] == (
        "전세가율은 가4 50.0%부터 가3 70.0%까지(전세표본 5건 이상). 구 중위 60.0%, 서울 구 중위 60.0%. "
        "95% 초과 1곳은 단지명을 표기하지 않는다."
    )
    assert "가5" not in fam["G3"]["text"]   # ratio 96% — 단지명 비표기


def test_snapshot_g6_gu_vs_seoul_via_private_fn():
    # G6 은 '편차' 유형으로 G3/G4 와 슬롯을 다투고 이 fixture 에서는 G3 가 이긴다(공개 API 로는
    # 관측 불가) — S2 와 같은 이유로 private 함수를 직접 호출해 템플릿 자체를 스냅샷한다.
    import blog.fact_lead as fl
    cx = _snapshot_ds()["complexes"]
    gu_rows = [r for r in cx if r["gu"] == "가구"]
    cand = fl._g6("가구", gu_rows, cx)
    assert cand["text"] == "구 중위 7.5억은 25구 중위 7.5억보다 0억 낮다. 이보다 낮은 구는 1곳."


def test_snapshot_s2_seoul_gu_trend_deviation_via_private_fn():
    # S2 는 '편차' 유형으로 S6/S7 과 슬롯을 다투므로(유형당 최대 1개), 이 스냅샷은 공개 API 선택
    # 로직이 아니라 템플릿 자체의 정확성을 고정한다 — 선택 로직은 test_max_one_per_type 이 따로 본다.
    import blog.fact_lead as fl
    cx = _snapshot_ds()["complexes"]
    by_gu = fl._group_by_gu(cx)
    cand = fl._s2(by_gu)
    assert cand["text"] == "구 중위 상승폭이 -17.5% 미만은 다구(1곳)이다."


def test_snapshot_c1_complex_vs_peers():
    ds = _snapshot_ds()
    gu_rows = [r for r in ds["complexes"] if r["gu"] == "가구"]
    row = next(r for r in gu_rows if r["name"] == "가6")
    peers = cp.select_peers(row, gu_rows)
    assert len(peers) == 5
    fam = _leads_by_family(build_fact_leads(ds, "complex", row, peers=peers))
    assert fam["C1"]["text"] == (
        "6.35억은 같은 구 ~59㎡ 피어 5곳 중위(8억)보다 1.65억 낮다. 이보다 낮은 피어는 1곳."
    )


def test_snapshot_c3_complex_jeonse():
    ds = _snapshot_ds()
    gu_rows = [r for r in ds["complexes"] if r["gu"] == "가구"]
    row = next(r for r in gu_rows if r["name"] == "가6")
    fam = _leads_by_family(build_fact_leads(ds, "complex", row, peers=[]))
    assert fam["C3"]["text"] == "전세가율 58.7%는 구 중위 60.0%보다 낮다. 매매–전세 갭 2.62억."


def test_snapshot_c7_complex_threshold_hi_and_lo():
    import blog.fact_lead as fl
    gu_rows = [r for r in _snapshot_ds()["complexes"] if r["gu"] == "가구"]
    hi_row = next(r for r in gu_rows if r["name"] == "가3")   # pos_52w=99
    lo_row = next(r for r in gu_rows if r["name"] == "가5")   # pos_52w=6
    assert fl._c7(hi_row)["text"] == "최근 3개월 체결 중위가 12개월 범위 상단(99%)에 있다."
    assert fl._c7(lo_row)["text"] == "최근 3개월 체결 중위가 12개월 범위 하단(6%)에 있다."


# ── 2. 결정론 ────────────────────────────────────────────────────────────

def test_determinism_two_calls_identical_seoul_gu_complex():
    ds = _snapshot_ds()
    assert build_fact_leads(ds, "seoul") == build_fact_leads(ds, "seoul")
    assert build_fact_leads(ds, "gu", "가구") == build_fact_leads(ds, "gu", "가구")
    gu_rows = [r for r in ds["complexes"] if r["gu"] == "가구"]
    row = next(r for r in gu_rows if r["name"] == "가6")
    peers = cp.select_peers(row, gu_rows)
    assert (build_fact_leads(ds, "complex", row, peers=peers)
            == build_fact_leads(ds, "complex", row, peers=peers))


# ── 3. 문구 가드 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "이 단지는 서울에서 2위 안에 든다.",
    "세 번째로 낮은 구다.",
    "가장 높은 상승폭을 보였다.",
    "내년에는 상승 전망이다.",
])
def test_lead_wording_guard_rejects_ordinal_superlative_forecast(bad):
    with pytest.raises(ValueError, match="리드 금칙 패턴"):
        assert_lead_wording_ok(bad, "unit-test:lead-guard")


def test_lead_wording_guard_still_rejects_base_forbidden_words():
    with pytest.raises(ValueError, match="금칙어"):
        assert_lead_wording_ok("이 단지는 추천 매물이다.", "unit-test:lead-guard-base")


def test_lead_wording_guard_passes_clean_fact_sentence():
    assert assert_lead_wording_ok("구 중위 7.5억은 25구 중위 7.5억보다 0억 낮다.", "unit-test:clean") is None


# ── 4. 게이트 ────────────────────────────────────────────────────────────

def test_jeonse_n_under_5_skips_c3():
    row = _row("가구", "전세미달", jeonse_n=3, jeonse_ratio_complex_pct=70.0)
    fam = _leads_by_family(build_fact_leads({"complexes": [row]}, "complex", row, peers=[]))
    assert "C3" not in fam


def test_ratio_over_95_skips_c3_but_g3_counts_and_never_names_it():
    ds = _snapshot_ds()
    over95_row = next(r for r in ds["complexes"] if r["name"] == "가5")
    fam_complex = _leads_by_family(build_fact_leads(ds, "complex", over95_row, peers=[]))
    assert "C3" not in fam_complex

    fam_gu = _leads_by_family(build_fact_leads(ds, "gu", "가구"))
    assert "가5" not in fam_gu["G3"]["text"]
    assert "95% 초과 1곳" in fam_gu["G3"]["text"]


def test_missing_trend_n_recent_prior_skips_c2():
    row = _row("가구", "추세필드없음", molit_trend_pct=10.0)   # molit_trend_n_recent/prior 미설정(None)
    fam = _leads_by_family(build_fact_leads({"complexes": [row]}, "complex", row, peers=[]))
    assert "C2" not in fam


# ── 5. prev_ds 패턴 재현 ──────────────────────────────────────────────────
# S3 전용 fixture: 15개 "저추세"(10.0 균일) + 5개 "고추세"(40~48, area_band 4/5 가 60-84㎡ 로 다수).
# 중위=10.0, IQR=7.5(P25=10.0,P75=17.5) → hi=17.5 → 고추세 5개만 그 임계를 넘어 |set|=5, 다수 비중 0.8.

def _pattern_ds(area_bands: list[str]) -> dict:
    # molit_trend_dir 은 _s1(항상 함께 계산되는 후보)이 bracket 접근하므로 pct>=0 인 모든 행에
    # 명시적으로 채워야 한다 — 실데이터는 필드가 항상 존재(null 허용)하지만 합성 fixture 는 아니다.
    rows = [_row("가구", f"저{i}", molit_trend_pct=10.0, molit_recent_eok=5.0, molit_trend_dir="▲")
            for i in range(15)]
    hot_vals = [40.0, 42.0, 44.0, 46.0, 48.0]
    for i, (v, band) in enumerate(zip(hot_vals, area_bands)):
        rows.append(_row("가구", f"고{i}", molit_trend_pct=v, molit_recent_eok=5.0,
                          molit_trend_dir="▲", area_band=band))
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-09-06"}


def test_pattern_not_reproduced_without_prev_ds_gets_prefix():
    ds = _pattern_ds(["60-84㎡", "60-84㎡", "60-84㎡", "60-84㎡", "85-114㎡"])
    fam = _leads_by_family(build_fact_leads(ds, "seoul", prev_ds=None))
    assert fam["S3"]["text"].startswith("이번 주 관측: ")
    assert fam["S3"]["refs"]["reproduced"] is False
    assert "60-84㎡" in fam["S3"]["text"]


def test_pattern_reproduced_in_prev_ds_drops_prefix():
    ds = _pattern_ds(["60-84㎡", "60-84㎡", "60-84㎡", "60-84㎡", "85-114㎡"])
    prev_ds = _pattern_ds(["60-84㎡", "60-84㎡", "60-84㎡", "60-84㎡", "85-114㎡"])  # 지난주도 같은 다수
    fam = _leads_by_family(build_fact_leads(ds, "seoul", prev_ds=prev_ds))
    assert not fam["S3"]["text"].startswith("이번 주 관측: ")
    assert fam["S3"]["refs"]["reproduced"] is True


def test_pattern_different_majority_in_prev_ds_keeps_prefix():
    ds = _pattern_ds(["60-84㎡", "60-84㎡", "60-84㎡", "60-84㎡", "85-114㎡"])
    prev_ds = _pattern_ds(["85-114㎡", "85-114㎡", "85-114㎡", "85-114㎡", "60-84㎡"])  # 지난주는 반대 다수
    fam = _leads_by_family(build_fact_leads(ds, "seoul", prev_ds=prev_ds))
    assert fam["S3"]["text"].startswith("이번 주 관측: ")
    assert fam["S3"]["refs"]["reproduced"] is False


# ── 선택 로직: 유형당 최대 1개 ────────────────────────────────────────────

def test_max_one_per_type_and_limit_respected():
    leads = build_fact_leads(_snapshot_ds(), "seoul")
    types = [ld["type"] for ld in leads]
    assert len(types) == len(set(types))          # 유형 중복 없음
    assert len(leads) <= 5                          # seoul 기본 limit
    scores = [ld["score"] for ld in leads]
    assert scores == sorted(scores, reverse=True)   # 점수 desc 정렬


# ── 6. 실데이터 스모크(site/dataset.json) ────────────────────────────────

def _real_ds() -> dict:
    with open("site/dataset.json", encoding="utf-8") as f:
        return json.load(f)


def test_real_data_seoul_leads_at_least_3():
    ds = _real_ds()
    leads = build_fact_leads(ds, "seoul")
    assert len(leads) >= 3
    for ld in leads:
        assert_lead_wording_ok(ld["text"], "smoke:seoul")
        assert "None" not in ld["text"] and "nan" not in ld["text"]


def test_real_data_every_gu_has_at_least_2_leads():
    ds = _real_ds()
    gus = sorted({r["gu"] for r in ds["complexes"] if r.get("gu")})
    assert len(gus) >= 20   # 25구 발행 스코프(feedback-realestate-scan-exclusions 등으로 변동 허용폭)
    for gu in gus:
        leads = build_fact_leads(ds, "gu", gu)
        assert len(leads) >= 2, f"{gu}: {len(leads)}개뿐"
        for ld in leads:
            assert_lead_wording_ok(ld["text"], f"smoke:gu:{gu}")
            assert "None" not in ld["text"] and "nan" not in ld["text"]


def test_real_data_five_complexes_with_peers_have_leads():
    ds = _real_ds()
    cx = ds["complexes"]
    gated = cp.select_page_complexes(ds)
    checked = 0
    for row in gated:
        gu_rows = [r for r in cx if r["gu"] == row["gu"]]
        peers = cp.select_peers(row, gu_rows)
        if len(peers) < 2:
            continue
        leads = build_fact_leads(ds, "complex", row, peers=peers)
        assert len(leads) >= 1
        for ld in leads:
            assert_lead_wording_ok(ld["text"], f"smoke:complex:{row['gu']}/{row['name']}")
            assert "None" not in ld["text"] and "nan" not in ld["text"]
        checked += 1
        if checked >= 5:
            break
    assert checked >= 5


def test_real_data_renderers_render_without_error():
    ds = _real_ds()
    cx = ds["complexes"]
    asof, today = ds["data_asof"], "2026-09-06"

    gu_rows = [r for r in cx if r["gu"] == "중랑"]
    out = gh.render_gu_hub("중랑", gu_rows, asof, today, ds=ds)
    assert '<section class="lead">' in out

    row = next(r for r in gu_rows if r["name"] == "중앙하이츠")
    peers = cp.select_peers(row, gu_rows)
    out2 = cp.render_complex_page(row, peers, None, asof, today, ds=ds)
    assert '<section class="lead">' in out2

    digest = dd.build_daily_digest(ds, today, asof)
    assert len(digest["tistory_html"].encode("utf-8")) <= 30000
    assert '<section class="lead">' in digest["site_html"]

    weekly = ws.build_weekly_summary(ds, "2026-09-07")
    assert len(weekly) <= 3500


def test_real_data_render_lead_block_and_lines_shape():
    ds = _real_ds()
    leads = build_fact_leads(ds, "seoul")
    block = render_lead_block(leads)
    assert block.startswith('<section class="lead">') and block.endswith("</section>")
    assert "<h2>이 페이지에서만 알 수 있는 것</h2>" in block
    lines = render_lead_lines(leads)
    assert lines == [ld["text"] for ld in leads]
    assert render_lead_block([]) == ""
    assert render_lead_lines([]) == []
