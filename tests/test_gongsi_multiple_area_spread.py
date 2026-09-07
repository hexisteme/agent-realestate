"""공시가 배율(OfficialPriceMultiple)·평형 격차(AreaSpread) — 2026-09-07 S4. 면적 일치 게이트·㎡당 비·최소 n·신원 매칭·
overlay 병합·탐색기 열·단지 한 줄·월간결산 절(문구 가드 둘 다)·claims."""
from __future__ import annotations
import importlib.util
import json
import pathlib
import re
from datetime import date

import blog.build_explorer as be
import blog.complex_page as cp
from blog import period_delta as pd
from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok


def _rec(apt, area, price):
    return {"apt": apt, "area": area, "price": price, "ym": "202608"}


def _pd_helpers():
    spec = importlib.util.spec_from_file_location("tpd_for_s4", pathlib.Path(__file__).with_name("test_period_delta.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._row, mod._ds


def test_compute_gongsi_multiple_requires_area_match():
    row = {"molit_recent_eok": 12.0, "gongsi_man": 80000, "gongsi_area_m2": 84.9, "area_m2": 84.9}
    assert be.compute_gongsi_multiple(row) == 1.5
    assert be.compute_gongsi_multiple({**row, "area_m2": 85.3}) == 1.5            # 0.5㎡ 안
    assert be.compute_gongsi_multiple({**row, "area_m2": 59.9}) is None           # 앵커 이동 → 미발행
    assert be.compute_gongsi_multiple({**row, "gongsi_area_m2": None}) is None    # 면적 미확인(구 overlay) → 미발행
    assert be.compute_gongsi_multiple({**row, "gongsi_man": None}) is None
    assert be.compute_gongsi_multiple({**row, "molit_recent_eok": None}) is None


def test_compute_area_spread_per_m2_and_min_n():
    recs = [_rec("A", 59.9, 6e8)] * 5 + [_rec("A", 84.9, 8e8)] * 5
    out = be.compute_area_spread(recs)
    assert out["n59"] == 5 and out["n84"] == 5 and out["med59_eok"] == 6.0 and out["med84_eok"] == 8.0
    assert out["area_spread_59_84_pct"] == 6.3                                      # (6e8/59.9)/(8e8/84.9) − 1 = +6.3%
    assert be.compute_area_spread(recs[:9])["area_spread_59_84_pct"] is None        # 84 쪽 n=4
    assert be.compute_area_spread([])["n59"] == 0 and be.compute_area_spread([])["area_spread_59_84_pct"] is None
    assert be.compute_area_spread([_rec("A", 70.0, 7e8)] * 10)["n59"] == 0          # 앵커 ±3.5 밖
    inv = be.compute_area_spread([_rec("두산", 59.9, 10.8e8)] * 6 + [_rec("두산", 84.9, 7e8)] * 6)   # 84 총액 < 59 총액 → 동명 병합 의심
    assert inv["area_spread_59_84_pct"] is None and inv["spread_flag"] == "total_inversion" and inv["med84_eok"] == 7.0


def test_add_area_spread_keeps_name_identity(tmp_path):
    gu, lawd = next(iter(be.GU_LAWD.items()))
    molit = {lawd: [_rec("상계주공1단지", 59.28, 5e8)] * 6 + [_rec("상계주공1단지", 84.5, 6.5e8)] * 6 + [_rec("중계무지개", 84.5, 9e8)] * 6}
    p = tmp_path / "m.json"; p.write_text(json.dumps(molit), encoding="utf-8")
    units = {"le60": 1000, "60_85": 1000, "85_135": 0, "gt135": 0}
    ds = {"complexes": [{"name": "상계주공1단지", "gu": gu, "area_m2": 59.3, "kapt_area_units": units},
                        {"name": "없는단지", "gu": gu, "area_m2": 59.3, "kapt_area_units": units},
                        {"name": "상계주공1단지", "gu": gu, "area_m2": 59.3}]}
    be.add_area_spread(ds, str(p))
    r = ds["complexes"][0]
    assert r["n59"] == 6 and r["n84"] == 6 and r["med84_eok"] == 6.5               # 다른 단지 84 는 섞이지 않음
    assert r["area_spread_59_84_pct"] == round(((5e8 / 59.28) / (6.5e8 / 84.5) - 1) * 100, 1)
    assert ds["complexes"][1]["area_spread_59_84_pct"] is None and ds["complexes"][1]["n59"] == 0
    assert ds["complexes"][2]["area_spread_59_84_pct"] is None and ds["complexes"][2]["spread_flag"] == "identity_unverified"   # 대조 자료 없음 → 미발행


def test_overlay_merges_gongsi_area_then_multiple(tmp_path):
    ov = {"123": {"gongsi_man": 80000, "gongsi_area_m2": 84.9, "kapt_code": "X"}, "9": {"gongsi_man": 50000}}
    p = tmp_path / "ov.json"; p.write_text(json.dumps(ov), encoding="utf-8")
    ds = {"complexes": [{"complex_no": "123", "gongsi_man": None, "gongsi_area_m2": None, "molit_recent_eok": 12.0, "area_m2": 84.9},
                        {"complex_no": "9", "gongsi_man": None, "gongsi_area_m2": None, "molit_recent_eok": 10.0, "area_m2": 59.9}]}
    be.add_gongsi_multiple(be.add_enrich_overlay(ds, str(p)))
    assert ds["complexes"][0]["gongsi_area_m2"] == 84.9 and ds["complexes"][0]["gongsi_multiple"] == 1.5
    assert ds["complexes"][1]["gongsi_man"] == 50000 and ds["complexes"][1]["gongsi_multiple"] is None   # 면적 미기록 → 배율 없음


def test_explorer_columns_and_legend_are_factual():
    h = be.EXPLORER_HTML
    assert '{k:"gongsi_multiple"' in h and '{k:"area_spread_59_84_pct"' in h
    leg = [l for l in h.split("\n") if "<b>공시가 배율</b>" in l or "<b>평형 격차</b>" in l]
    assert len(leg) == 2
    assert_wording_ok(" ".join(leg), "explorer:legend")
    assert_lead_wording_ok(re.sub(r"<[^>]+>", " ", " ".join(leg)), "explorer:legend")


def test_complex_page_lines_only_when_present():
    row = {"gongsi_man": 80000, "gongsi_multiple": 1.5, "area_spread_59_84_pct": 22.7, "med59_eok": 6.0, "med84_eok": 7.0, "n59": 7, "n84": 12}
    card = cp._facts_card(row)
    assert "×1.50" in card and "+22.7%" in card and "n7" in card and "n12" in card and "None" not in card
    assert "공시가 배율" not in cp._facts_card({"gongsi_man": 80000}) and "평형 격차" not in cp._facts_card({"gongsi_man": 80000})


def test_monthly_sections_claims_and_weekly_omits():
    _row, _ds = _pd_helpers()
    rows = []
    for i in range(12):
        r = _row("노원" if i < 6 else "성동", f"단지{i}", 8.0 + i)
        r.update(price_segment=be.price_segment(8.0 + i), gongsi_multiple=1.5 + i * 0.02, area_spread_59_84_pct=10.0 + i)
        rows.append(r)
    ds, base = _ds(rows, asof="2026-09-27", gen="2026-09-27"), _ds([dict(r) for r in rows], asof="2026-08-30", gen="2026-08-30")
    d = pd.build_period_delta(ds, base, "monthly", "2026-09-27", date(2026, 8, 30))
    assert d["gongsi_multiple"]["n"] == 12 and d["gongsi_multiple"]["median"] == 1.61 and d["area_spread"]["gus"][0]["key"] == "노원"
    assert [b["n"] for b in d["gongsi_multiple"]["bands"]] == [2, 5, 5, 0]
    post = pd.render_period_post(d)
    assert "공시가 배율" in post["html"] and "평형 격차 59↔84" in post["html"] and "None" not in post["html"]
    assert "공시가 배율" in post["tistory_html"]
    assert_lead_wording_ok(re.sub(r"<[^>]+>", " ", post["html"]), "monthly:site")   # K2: 금지어 + 전망·인과·서수 둘 다
    assert {c["claim"] for c in post["claims"]} >= {"monthly_gongsi_multiple_band", "monthly_area_spread_band"}
    dw = pd.build_period_delta(ds, base, "weekly", "2026-09-13", date(2026, 9, 6))
    assert dw["gongsi_multiple"] is None and "공시가 배율" not in pd.render_period_post(dw)["html"]
    empty = pd.build_period_delta(_ds([_row("노원", "단지", 8.0)]), _ds([_row("노원", "단지", 8.0)]), "monthly", "2026-09-27", date(2026, 8, 30))
    assert "공시가 배율" not in pd.render_period_post(empty)["html"]               # 값 없으면 절 없음(지어내지 않음)


def test_kapt_area_units_identity_guard_flags_and_boundary():
    """S4 Codex P1 — K-apt 면적대 세대수 대조: 자료 없음 → identity_unverified, 세대수 0 인 면적대 거래 → kapt_area_mismatch,
    경계(60.0㎡)는 양쪽 면적대 허용, 정상이면 통과."""
    recs = [{"apt": "x", "area": 59.0, "price": 6e8}] * 5 + [{"apt": "x", "area": 84.9, "price": 8e8}] * 5
    assert be.verify_kapt_area_units(recs, 59.0, None) == "identity_unverified"
    assert be.verify_kapt_area_units(recs, 59.0, {"le60": 0, "60_85": 0, "85_135": 0, "gt135": 0}) == "identity_unverified"
    assert be.verify_kapt_area_units(recs, 59.0, {"le60": 0, "60_85": 300, "85_135": 0, "gt135": 0}) == "kapt_area_mismatch"   # 59.0 은 le60 만 → 세대수 0
    assert be.verify_kapt_area_units(recs, 59.0, {"le60": 300, "60_85": 300, "85_135": 0, "gt135": 0}) == ""
    assert be.verify_kapt_area_units([{"apt": "x", "area": 41.0, "price": 4e8}] * 5, 84.9, {"le60": 0, "60_85": 300, "85_135": 0, "gt135": 0}) == ""   # 41 은 앵커 밖 → 검사 안 함
    assert be.verify_kapt_area_units(recs, 41.0, {"le60": 0, "60_85": 300, "85_135": 0, "gt135": 0}) == "kapt_area_mismatch"   # 발행 면적 41 → le60=0
    recs60 = [{"apt": "x", "area": 60.0, "price": 6e8}] * 5 + [{"apt": "x", "area": 84.9, "price": 8e8}] * 5
    assert be.verify_kapt_area_units(recs60, 60.0, {"le60": 0, "60_85": 300, "85_135": 0, "gt135": 0}) == ""                   # 60.0 경계 → 60_85 허용
    assert be._kapt_bands_for(59.9) == {"le60", "60_85"} and be._kapt_bands_for(41.0) == {"le60"} and be._kapt_bands_for(140.0) == {"gt135"}


def test_add_area_spread_suppresses_unverified_identity(tmp_path):
    """신원 미확인·불일치 단지는 격차·n·중위 전부 미발행(빈칸) + 플래그만 남는다."""
    recs = [{"apt": "샘플", "area": 59.0, "price": 6e8}] * 5 + [{"apt": "샘플", "area": 84.9, "price": 8e8}] * 5
    lawd = be.GU_LAWD["노원"]
    p = tmp_path / "m.json"; p.write_text(json.dumps({lawd: recs}), encoding="utf-8")
    ok = {"le60": 100, "60_85": 100, "85_135": 0, "gt135": 0}
    ds = {"complexes": [{"name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": ok},
                        {"name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": None},
                        {"name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": {"le60": 0, "60_85": 100, "85_135": 0, "gt135": 0}}]}
    be.add_area_spread(ds, str(p))
    a, b, c = ds["complexes"]
    assert a["area_spread_59_84_pct"] is not None and a["spread_flag"] == ""
    assert b["area_spread_59_84_pct"] is None and b["n59"] is None and b["spread_flag"] == "identity_unverified"
    assert c["area_spread_59_84_pct"] is None and c["med84_eok"] is None and c["spread_flag"] == "kapt_area_mismatch"


def test_backfill_kapt_area_units_from_cache(tmp_path):
    spec = importlib.util.spec_from_file_location("bk", pathlib.Path("backfill_kapt_area_units.py"))
    bk = importlib.util.module_from_spec(spec); spec.loader.exec_module(bk)
    overlay = {"1": {"kapt_code": "A1"}, "2": {"kapt_code": "A2"}, "3": {}, "4": {"kapt_code": "A4", "kapt_area_units": {"le60": 1, "60_85": 0, "85_135": 0, "gt135": 0}}}
    raw = {"A1": {"kaptMparea60": "10", "kaptMparea85": "20", "kaptMparea135": "0", "kaptMparea136": ""}, "A2": {"kaptMparea60": "0"}}
    n = bk.stamp_kapt_area_units(overlay, raw)
    assert overlay["1"]["kapt_area_units"] == {"le60": 10, "60_85": 20, "85_135": 0, "gt135": 0}
    assert "kapt_area_units" not in overlay["2"] and n == {"stamped": 1, "already": 1, "no_kapt": 1, "no_basis": 0, "no_counts": 1}


def test_fact_ratio_gu_min_n_and_claims_skip_empty_bands():
    rows = [{"gongsi_multiple": 1.5 + i / 100, "price_segment": "10억 미만", "gu": "노원"} for i in range(8)]
    s5 = pd.summarize_fact_ratio(rows, "gongsi_multiple")
    s10 = pd.summarize_fact_ratio(rows, "gongsi_multiple", gu_min_n=10)
    assert s5["gus"][0]["median"] is not None and s10["gus"][0]["median"] is None and s10["gus"][0]["n"] == 8
    empty = [b for b in s10["bands"] if b["n"] == 0]
    assert len(empty) == 3 and all(b["median"] is None for b in empty)     # 빈 밴드는 중위 None → claim 대상 아님


OK_UNITS = {"le60": 100, "60_85": 100, "85_135": 0, "gt135": 0}


def _pairs(apt, n59=5, n84=5):
    return [{"apt": apt, "area": 59.0, "price": 6e8}] * n59 + [{"apt": apt, "area": 84.9, "price": 8e8}] * n84


def _write(tmp_path, molit, frame, dmap=None):
    p = tmp_path / "m.json"; p.write_text(json.dumps(molit), encoding="utf-8")
    fp = tmp_path / "frame.json"; fp.write_text(json.dumps(frame, ensure_ascii=False), encoding="utf-8")
    dm = None
    if dmap is not None:
        dm = tmp_path / "district.json"; dm.write_text(json.dumps(dmap, ensure_ascii=False), encoding="utf-8")
    return str(p), str(fp), (str(dm) if dm else None)


def test_add_area_spread_suppresses_same_name_in_frame(tmp_path):
    """S5 Codex P1 — 공공 프레임에서 같은 소재구에 이름매칭이 겹치는 다른 단지가 있으면(영등포 현대2차 711·697) 미발행."""
    lawd = be.GU_LAWD["노원"]
    frame = [{"complexNo": "1", "name": "샘플", "gu": "노원"}, {"complexNo": "1", "name": "샘플", "gu": "도봉"},   # 같은 단지의 스캔구 중복 등재
             {"complexNo": "2", "name": "샘플", "gu": "도봉"}, {"complexNo": "3", "name": "다른", "gu": "노원"}]
    p, fp, dm = _write(tmp_path, {lawd: _pairs("샘플")}, frame, {"1": {"sido": "서울특별시", "gu": "노원"}, "2": {"sido": "서울특별시", "gu": "노원", "dong": "상계동"}})
    ds = {"complexes": [{"complex_no": "1", "name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(ds, p, frame_path=fp)                                             # 소재구 맵 없음: 2번은 도봉 스캔뿐(도봉 거래 없음) → 노원엔 1개 → 발행
    assert ds["complexes"][0]["area_spread_59_84_pct"] is not None
    be.add_area_spread(ds, p, frame_path=fp, district_map_path=dm)                      # 좌표 소재구로 2번도 노원 → 같은 원본명에 2단지 → 미발행
    r = ds["complexes"][0]
    assert r["area_spread_59_84_pct"] is None and r["n59"] is None and r["med84_eok"] is None and r["spread_flag"] == "ambiguous_name"
    molit = json.load(open(p))
    assert be.build_name_owners(None, None, molit) == {} and be.build_name_owners(fp, None, molit) == {("노원", "샘플"): {"1"}}
    assert be.build_name_owners(fp, dm, molit) == {("노원", "샘플"): {"1", "2"}}


def test_ambiguity_guard_uses_the_real_matching_key(tmp_path):
    """S6 Codex F1 — '샘플1단지'·'샘플1차' 는 canonical 이 다르지만 match_molit_names 2순위(번호블록 접기)로 둘 다 MOLIT '샘플1' 에 닿는다
    → 가드도 같은 매칭 키를 써야 한다(canonical 별 세기는 우회됨)."""
    lawd = be.GU_LAWD["노원"]
    frame = [{"complexNo": "1", "name": "샘플1단지", "gu": "노원"}, {"complexNo": "2", "name": "샘플1차", "gu": "노원"}]
    p, fp, dm = _write(tmp_path, {lawd: _pairs("샘플1")}, frame, {"1": {"sido": "서울특별시", "gu": "노원"}, "2": {"sido": "서울특별시", "gu": "노원"}})
    ds = {"complexes": [{"complex_no": "1", "name": "샘플1단지", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(ds, p, frame_path=fp, district_map_path=dm)
    assert ds["complexes"][0]["spread_flag"] == "ambiguous_name" and ds["complexes"][0]["n59"] is None
    assert be.build_name_owners(fp, dm, json.load(open(p))) == {("노원", "샘플1"): {"1", "2"}}


def _recs(apt, n59, p59, n84, p84):
    return [{"apt": apt, "area": 59.0, "price": p59}] * n59 + [{"apt": apt, "area": 84.9, "price": p84}] * n84


def test_ambiguity_guard_reads_published_gu_and_blocks_all_candidates_for_unpublished(tmp_path):
    """S6 F3 / S6b R1 / S6c·S6d C1 — 발행 행은 dataset 이 정한 구 그대로(순위·허용목록·A/B 경로를 흉내내지 않음), 소재구 미확정·미발행 단지는 후보 구 전부."""
    nowon, dobong = be.GU_LAWD["노원"], be.GU_LAWD["도봉"]
    seoul = {"sido": "서울특별시", "gu": "노원"}
    # ① 1번은 맵에 없고 노원·도봉 중복 등재·미발행 → 두 구 모두 소유자, 2번(노원 발행) 은 동명 → 미발행
    frame = [{"complexNo": "1", "name": "샘플1단지", "gu": "노원"}, {"complexNo": "1", "name": "샘플1단지", "gu": "도봉"}, {"complexNo": "2", "name": "샘플1차", "gu": "노원"}]
    p, fp, dm = _write(tmp_path, {nowon: _recs("샘플1", 10, 6e8, 5, 8e8), dobong: _recs("샘플1", 8, 6e8, 8, 8e8)}, frame, {"2": seoul})
    molit = json.load(open(p))
    assert be.build_name_owners(fp, dm, molit) == {("노원", "샘플1"): {"1", "2"}, ("도봉", "샘플1"): {"1"}}
    ds = {"complexes": [{"complex_no": "2", "name": "샘플1차", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(ds, p, frame_path=fp, district_map_path=dm)
    assert ds["complexes"][0]["spread_flag"] == "ambiguous_name"
    # ② dataset 이 1번을 도봉으로 발행했으면(허용목록·순위가 무엇이었든) 가드도 도봉 — 노원엔 2번뿐 → 발행
    row1, row2 = {"complex_no": "1", "name": "샘플1단지", "gu": "도봉", "area_m2": 59.0, "kapt_area_units": OK_UNITS}, {"complex_no": "2", "name": "샘플1차", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}
    assert be.build_name_owners(fp, dm, molit, published=[row1, row2]) == {("도봉", "샘플1"): {"1"}, ("노원", "샘플1"): {"2"}}
    ds2 = {"complexes": [json.loads(json.dumps(row1)), json.loads(json.dumps(row2))]}
    be.add_area_spread(ds2, p, frame_path=fp, district_map_path=dm)
    assert [r["spread_flag"] for r in ds2["complexes"]] == ["", ""] and ds2["complexes"][1]["area_spread_59_84_pct"] is not None
    # ③ dataset 이 1번을 노원으로 발행했으면(예: --public-gu-allow 노원) 둘 다 동명 → 둘 다 미발행
    ds3 = {"complexes": [{**json.loads(json.dumps(row1)), "gu": "노원"}, json.loads(json.dumps(row2))]}
    be.add_area_spread(ds3, p, frame_path=fp, district_map_path=dm)
    assert [r["spread_flag"] for r in ds3["complexes"]] == ["ambiguous_name", "ambiguous_name"]
    # ④ dataset 후보 순위 함수는 그대로(발행 쪽 규칙)
    assert be._rank_candidate_gu("샘플1단지", "노원", molit)["rank"] > be._rank_candidate_gu("샘플1단지", "도봉", molit)["rank"]
    assert be._rank_candidate_gu("없는이름", "노원", molit) is None


def test_ambiguity_guard_keeps_existing_rows_under_any_allowlist(tmp_path):
    """S6d Codex C1 — 허용목록(--public-gu-allow 도봉·빈 목록)으로 신규 B 에서 빠진 노원 단지도 프레임 소유자로 남아, 계속 발행되는 기존 A 노원 행의 동명 차단이 유지된다."""
    nowon = be.GU_LAWD["노원"]
    frame = [{"complexNo": "1", "name": "샘플1단지", "gu": "노원"}, {"complexNo": "2", "name": "샘플1차", "gu": "노원"}]
    p, fp, dm = _write(tmp_path, {nowon: _recs("샘플1", 5, 6e8, 5, 8e8)}, frame, {"1": {"sido": "서울특별시", "gu": "노원"}, "2": {"sido": "서울특별시", "gu": "노원"}})
    for published in ([{"complex_no": "2", "name": "샘플1차", "gu": "노원"}], []):                     # 기존 A 만 남은 dataset · 아무것도 없는 dataset
        assert be.build_name_owners(fp, dm, json.load(open(p)), published=published) == {("노원", "샘플1"): {"1", "2"}}
    ds = {"complexes": [{"complex_no": "2", "name": "샘플1차", "gu": "노원", "area_m2": 59.0, "kapt_area_units": {"le60": 150, "60_85": 150, "85_135": 0, "gt135": 0}}]}
    be.add_area_spread(ds, p, frame_path=fp, district_map_path=dm)
    r = ds["complexes"][0]
    assert r["spread_flag"] == "ambiguous_name" and r["area_spread_59_84_pct"] is None and r["n59"] is None


def test_ambiguity_guard_excludes_outside_seoul_like_dataset(tmp_path):
    """S6b Codex N1 — 소재구 맵이 서울 밖(의정부)이라 dataset 이 outside_scope 로 빼는 단지는 소유자 집계에도 넣지 않는다(오차단 방지)."""
    nowon = be.GU_LAWD["노원"]
    frame = [{"complexNo": "1", "name": "샘플", "gu": "노원"}, {"complexNo": "2", "name": "샘플", "gu": "노원"}]
    p, fp, dm = _write(tmp_path, {nowon: _pairs("샘플")}, frame, {"1": {"sido": "서울특별시", "gu": "노원"}, "2": {"sido": "경기도", "gu": "의정부"}})
    assert be.build_name_owners(fp, dm, json.load(open(p))) == {("노원", "샘플"): {"1"}}
    ds = {"complexes": [{"complex_no": "1", "name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(ds, p, frame_path=fp, district_map_path=dm)
    assert ds["complexes"][0]["spread_flag"] == "" and ds["complexes"][0]["area_spread_59_84_pct"] is not None


def test_ambiguity_guard_counts_idless_published_rows(tmp_path):
    """S6e Codex E3 — complex_no 가 없는 기존 A 행도 발행 구·표시명으로 소유자에 남아, 같은 원본명의 다른 발행 행이 차단을 피하지 못한다(혼자면 자기 자신으로 차단되지 않는다)."""
    nowon = be.GU_LAWD["노원"]
    p, fp, dm = _write(tmp_path, {nowon: _recs("샘플1", 5, 6e8, 5, 8e8)}, [{"complexNo": "2", "name": "샘플1차", "gu": "노원"}], {"2": {"sido": "서울특별시", "gu": "노원"}})
    molit = json.load(open(p))
    for noid in (None, "", "  "):
        rows = [{"complex_no": noid, "name": "샘플1단지", "gu": "노원", "area_m2": 84.9, "kapt_area_units": OK_UNITS},
                {"complex_no": "2", "name": "샘플1차", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]
        assert be.build_name_owners(fp, dm, molit, published=rows) == {("노원", "샘플1"): {"noid:노원:샘플1단지", "2"}}
        ds = {"complexes": json.loads(json.dumps(rows))}
        be.add_area_spread(ds, p, frame_path=fp, district_map_path=dm)
        assert [r["spread_flag"] for r in ds["complexes"]] == ["ambiguous_name", "ambiguous_name"] and ds["complexes"][1]["n59"] is None
    alone = {"complexes": [{"complex_no": None, "name": "샘플1단지", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(alone, p, frame_path=fp, district_map_path=dm)                   # 프레임의 2번(샘플1차)이 같은 원본명 → 차단
    assert alone["complexes"][0]["spread_flag"] == "ambiguous_name"
    p2, fp2, dm2 = _write(tmp_path, {nowon: _recs("샘플", 5, 6e8, 5, 8e8)}, [], {})
    alone2 = {"complexes": [{"complex_no": "", "name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(alone2, p2, frame_path=fp2, district_map_path=dm2)               # 다른 단지 없음 → 자기 자신으로는 차단하지 않는다
    assert alone2["complexes"][0]["spread_flag"] == "" and alone2["complexes"][0]["area_spread_59_84_pct"] is not None


def test_total_inversion_publishes_nothing(tmp_path):
    """S6 Codex F4 — 총액 역전도 다른 플래그처럼 격차·n·중위 전부 None(공개 dataset 에 표본·중위가 남지 않는다)."""
    lawd = be.GU_LAWD["노원"]
    recs = [{"apt": "샘플", "area": 59.0, "price": 9e8}] * 5 + [{"apt": "샘플", "area": 84.9, "price": 8e8}] * 5      # 84 총액 < 59 총액
    p = tmp_path / "m.json"; p.write_text(json.dumps({lawd: recs}), encoding="utf-8")
    ds = {"complexes": [{"complex_no": "1", "name": "샘플", "gu": "노원", "area_m2": 59.0, "kapt_area_units": OK_UNITS}]}
    be.add_area_spread(ds, str(p))
    r = ds["complexes"][0]
    assert r["spread_flag"] == "total_inversion" and r["n59"] is None and r["n84"] is None and r["med59_eok"] is None and r["med84_eok"] is None


def test_kapt_band_lower_edge_inclusive():
    assert be._kapt_bands_for(59.5) == {"le60", "60_85"} and be._kapt_bands_for(84.5) == {"60_85", "85_135"} and be._kapt_bands_for(134.5) == {"85_135", "gt135"}
