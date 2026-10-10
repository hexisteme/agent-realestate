"""Cross-surface observations and visualization integration contracts."""
from blog.daily_digest import build_daily_digest


def dataset():
    return {"complexes": [
        {"gu": "강남", "name": "한글이길게이어지는아파트", "saeng": "강남구",
         "product_type": "아파트", "area_m2": 59.0, "molit_n": 15,
         "molit_recent_eok": 20.0, "molit_trend_dir": "▲", "molit_trend_pct": 5.0,
         "molit_pos_52w": 50.0, "jeonse_n": 8, "jeonse_ratio_complex_pct": 60.0,
         "gap_eok": 8.0, "turnover_pct": 5.0, "molit_trend_n_recent": 3, "molit_trend_n_prior": 12},
        {"gu": "서초", "name": "서초아파트", "saeng": "서초구", "product_type": "아파트",
         "area_m2": 59.0, "molit_n": 20, "molit_recent_eok": 12.0,
         "molit_trend_dir": "▼", "molit_trend_pct": -2.0, "molit_pos_52w": 50.0,
         "jeonse_n": None, "jeonse_ratio_complex_pct": None, "gap_eok": None,
         "turnover_pct": None, "molit_trend_n_recent": 4, "molit_trend_n_prior": 16},
    ], "count": 2, "data_asof": "2026-10-03"}


def test_daily_has_visual_insights_on_both_surfaces():
    result = build_daily_digest(dataset(), "2026-10-03", "2026-10-03")
    for surface in ("site_html", "tistory_html"):
        assert 'data-market-visuals="districts"' in result[surface]
        assert "히트맵" in result[surface]
        assert "강남" in result[surface] and "서초" in result[surface]
    assert "구별 가격 비교" in result["site_html"]
    assert "서울 25개 구 요약" in result["tistory_html"]
    assert len(result["tistory_html"].encode()) <= 28000


def test_visuals_survive_budget_compaction_with_required_context():
    ds = dataset()
    ds["complexes"] *= 25
    ds["count"] = len(ds["complexes"])
    macro = {"tistory_html": "<p>필수거시지표</p>" + "<p>관측</p>" * 400,
             "site_html": "<h2>필수거시지표</h2>"}
    result = build_daily_digest(ds, "2026-10-03", "2026-10-03", macro=macro)
    for surface in ("site_html", "tistory_html"):
        assert 'data-market-visuals="districts"' in result[surface]
        assert "필수거시지표" in result[surface]
        assert "청약·공공임대 안내" in result[surface]
    assert len(result["tistory_html"].encode()) <= 28000
