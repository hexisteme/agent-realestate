"""가격 세그먼트(F)·거래회전율(C)·전세갭(D) 파생 사실 단위테스트 — 풀확대 2단계(2026-07-10).
대상: blog.build_explorer.price_segment / add_price_segment / _trade_annual_public / add_jeonse_facts."""
from blog.build_explorer import (
    add_jeonse_facts,
    add_liquidity_facts,
    add_price_segment,
    price_segment,
)


def _ds(rows):
    return {"complexes": rows}


def test_price_segment_boundaries():
    assert price_segment(None) is None
    assert price_segment(6.0) == "6억 이하"
    assert price_segment(6.01) == "6~10억"
    assert price_segment(10.0) == "6~10억"
    assert price_segment(10.01) == "10~15억"
    assert price_segment(15.0) == "10~15억"
    assert price_segment(15.01) == "15억 초과"


def test_add_price_segment_fills_field():
    ds = _ds([{"molit_recent_eok": 8.0}, {"molit_recent_eok": None}])
    out = add_price_segment(ds)
    assert out["complexes"][0]["price_segment"] == "6~10억"
    assert out["complexes"][1]["price_segment"] is None


def test_add_liquidity_facts_turnover(tmp_path):
    # 노원 LAWD=11350, 단지명 exact-match(정규화 후 동일) 12개월 8건 → 세대100 → 회전율 8%
    molit = {"11350": [{"apt": "테스트단지"} for _ in range(8)]}
    p = tmp_path / "molit.json"
    import json
    p.write_text(json.dumps(molit), encoding="utf-8")
    ds = _ds([{"name": "테스트단지", "gu": "노원", "units": 100}])
    out = add_liquidity_facts(ds, str(p))
    r = out["complexes"][0]
    assert r["trade_annual"] == 8.0
    assert r["turnover_pct"] == 8.0


def test_add_liquidity_facts_unknown_gu_returns_none(tmp_path):
    import json
    p = tmp_path / "molit.json"
    p.write_text(json.dumps({}), encoding="utf-8")
    ds = _ds([{"name": "아무개", "gu": "존재안함구", "units": 500}])
    out = add_liquidity_facts(ds, str(p))
    assert out["complexes"][0]["trade_annual"] is None
    assert out["complexes"][0]["turnover_pct"] is None


def test_add_jeonse_facts_missing_file_is_noop():
    ds = _ds([{"name": "테스트", "gu": "노원", "area_m2": 84.0, "molit_recent_eok": 9.0}])
    out = add_jeonse_facts(ds, "/no/such/file.json")
    assert out["complexes"][0].get("jeonse_recent_eok") is None


def test_add_jeonse_facts_computes_gap(tmp_path):
    import json
    # 동일평형(±3.5㎡) 전세 레코드 2건 → 중위 6.0억, 매매중위 9.0억 → 갭 3.0억, 전세가율 66.7%
    jeonse = {"11350": [{"apt": "테스트", "area": 84.0, "price": int(6.0e8), "ym": "202605"},
                        {"apt": "테스트", "area": 85.0, "price": int(6.0e8), "ym": "202604"}]}
    p = tmp_path / "jeonse.json"
    p.write_text(json.dumps(jeonse), encoding="utf-8")
    ds = _ds([{"name": "테스트", "gu": "노원", "area_m2": 84.0, "molit_recent_eok": 9.0}])
    out = add_jeonse_facts(ds, str(p))
    r = out["complexes"][0]
    assert r["jeonse_recent_eok"] == 6.0
    assert r["gap_eok"] == 3.0
    assert r["jeonse_ratio_complex_pct"] == 66.7
