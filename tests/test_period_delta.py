"""주간결산/월간결산(blog.period_delta, 2026-09-07) 단위테스트 — 달력 판정·base 해소(전월 마지막 일요일→대체)·밴드 재계산과
이동·구별 Δ와 표본 억제·신고 델타 multiset·렌더 메타(_post_meta 규약)·claims·periodic 원고·구 포스트 오인 방지."""
from __future__ import annotations
import gzip
import json
from datetime import date

from blog import period_delta as pd
from blog.build_site import _latest_gu_post_href, _latest_period_post_href, _post_meta


def _row(gu, name, eok, n=15, pos=None, area=59.0):
    return dict(gu=gu, name=name, area_m2=area, product_type="아파트", molit_n=n, molit_recent_eok=eok,
                molit_pos_52w=pos, molit_trend_dir=None, molit_trend_pct=None, units=300, built_year=2005,
                price_segment="옛라벨(무시돼야 함)")


def _ds(rows, asof="2026-09-05", gen="2026-09-13"):
    return {"complexes": rows, "count": len(rows), "data_asof": asof, "generated": gen}


def test_period_kind_calendar_and_env_override(monkeypatch):
    monkeypatch.delenv("RE_PERIODIC_POSTS", raising=False)
    assert pd.period_kind("2026-09-13") == "weekly"      # 일요일
    assert pd.period_kind("2026-09-27") == "monthly"     # 9월 마지막 일요일
    assert pd.period_kind("2026-09-14") is None          # 월요일
    assert pd.is_last_sunday("2026-10-25") and pd.is_last_sunday("2026-11-29") and not pd.is_last_sunday("2026-09-20")
    monkeypatch.setenv("RE_PERIODIC_POSTS", "0")
    assert pd.period_kind("2026-09-13") is None
    monkeypatch.setenv("RE_PERIODIC_POSTS", "monthly")
    assert pd.period_kind("2026-09-08") == "monthly"


def test_previous_month_last_sunday():
    assert pd.previous_month_last_sunday("2026-09-27") == date(2026, 8, 30)
    assert pd.previous_month_last_sunday("2026-10-25") == date(2026, 9, 27)
    assert pd.previous_month_last_sunday("2026-03-01") == date(2026, 2, 22)


def test_resolve_base_monthly_prefers_prev_month_then_oldest_sunday(tmp_path):
    d = tmp_path / "snaps"; d.mkdir()
    (d / "dataset-2026-09-06.json").write_text("{}")                    # 일요일, 09-27 기준 21일 전
    assert pd.resolve_base("monthly", "2026-09-27", str(d))[0] == date(2026, 9, 6)   # 08-30 없음 → 대체
    (d / "dataset-2026-08-30.json").write_text("{}")
    assert pd.resolve_base("monthly", "2026-09-27", str(d))[0] == date(2026, 8, 30)  # 전월 마지막 일요일 우선
    assert pd.resolve_base("weekly", "2026-09-13", str(d))[0] == date(2026, 9, 6)
    assert pd.resolve_base("weekly", "2026-09-20", str(d)) is None                     # 09-13 없음
    assert pd.resolve_base("monthly", "2026-09-20", str(tmp_path / "none")) is None


def test_band_deltas_recompute_from_eok_and_migrations():
    base = _ds([_row("강남", "A", 9.9), _row("강남", "B", 14.9), _row("노원", "C", 5.0), _row("노원", "D", None)])
    cur = _ds([_row("강남", "A", 10.2), _row("강남", "B", 15.0), _row("노원", "C", 5.1), _row("노원", "D", None), _row("서초", "E", 21.0)])
    mig = pd.compute_band_migrations(cur, base)
    assert [(m["name"], m["from"], m["to"], m["direction"]) for m in mig] == [
        ("A", "10억 미만", "10~15억", "up"), ("B", "10~15억", "15~20억", "up")]
    bands = {b["band"]: b for b in pd.compute_band_deltas(cur, base)}
    assert bands["10억 미만"]["n_base"] == 2 and bands["10억 미만"]["n_cur"] == 1 and bands["10억 미만"]["moved_out"] == 1
    assert bands["10~15억"]["moved_in"] == 1 and bands["10~15억"]["moved_out"] == 1
    assert bands["20억 이상"]["n_cur"] == 1 and bands["20억 이상"]["n_base"] == 0
    assert bands["10억 미만"]["delta_median_pct"] is None      # 짝 1개 < MIN_PAIRS → 억제
    pairs, added, removed = pd.pair_complexes(cur, base)
    assert len(pairs) == 4 and [r["name"] for r in added] == ["E"] and removed == []


def test_gu_deltas_suppress_small_samples_and_count_positions():
    rows_b = [_row("강남", f"G{i}", 10.0 + i, pos=100) for i in range(6)]
    rows_c = [_row("강남", f"G{i}", 10.5 + i, pos=(100 if i < 3 else 50)) for i in range(6)] + [_row("노원", "N1", 5.0)]
    gus = {g["gu"]: g for g in pd.compute_gu_deltas(_ds(rows_c), _ds(rows_b))}
    assert gus["강남"]["n_pairs"] == 6 and gus["강남"]["delta_median_pct"] > 0
    assert gus["강남"]["hi_base"] == 6 and gus["강남"]["hi_cur"] == 3
    assert gus["노원"]["n_pairs"] == 0 and gus["노원"]["delta_median_pct"] is None and gus["노원"]["n_base"] == 0


def test_diff_filings_multiset_gone_and_record_highs():
    a = {"apt": "A", "area": 59.9, "price": 10_0000_0000, "ym": "202508"}
    prev = {"11680": [a, a] + [{"apt": "B", "area": 84.9, "price": p, "ym": "202506"} for p in (18e8, 19e8, 20e8)], "_done": []}
    cur = {"11680": [a] * 3 + [{"apt": "B", "area": 84.9, "price": 21e8, "ym": "202509"}],
           "11350": [{"apt": "C", "area": 59.0, "price": 5e8, "ym": "202509"}]}
    d = pd.diff_filings(prev, cur)
    assert d["n_new"] == 3 and d["n_gone"] == 3                       # A +1(3−2), B 신규 1, C 신규 1 / B 옛 3건 사라짐(창 이탈)
    assert sorted(x["apt"] for x in d["new"]) == ["A", "B", "C"] and {x["gu"] for x in d["new"]} == {"강남", "노원"}
    assert [(h["apt"], h["prev_max"]) for h in d["record_highs"]] == [("B", 20_0000_0000)]
    s = pd.summarize_filings(d)
    assert s["by_gu"] == {"강남": 2, "노원": 1} and {b["band"]: b["n"] for b in s["bands"]}["20억 이상"] == 1


def test_write_period_post_renders_meta_claims_and_periodic_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"; snap.mkdir()
    base = _ds([_row("강남", "A", 9.9, pos=100), _row("강남", "B", 14.9), _row("노원", "C", 5.0)], asof="2026-08-30", gen="2026-09-06")
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    cur = _ds([_row("강남", "A", 10.2, pos=100), _row("강남", "B", 15.0), _row("노원", "C", 5.1), _row("서초", "E", 21.0)])
    res = pd.write_period_post(cur, "2026-09-13", str(tmp_path))
    assert res["kind"] == "weekly" and res["base_date"] == "2026-09-06" and res["n_pairs"] == 3 and res["filings"] is False
    p = tmp_path / "posts" / "2026-09-13-주간결산.html"
    assert p.exists()
    d, t, desc = _post_meta(str(p))
    assert d == "2026-09-13" and "주간결산" in t and "09-06 대비" in t and desc and '"' not in desc   # U8 짧은 제목
    claims = [json.loads(l) for l in (tmp_path / "posts" / "2026-09-13-주간결산.claims.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {c["claim"] for c in claims} == {"weekly_band_change", "weekly_gu_change", "price_band_migration"}
    assert (tmp_path / "tistory" / "2026-09-13-periodic-tistory-draft.html").exists()
    html_txt = p.read_text(encoding="utf-8")
    assert "10~15억" in html_txt and "가격대 이동 단지 2곳" in html_txt and "신고 델타를 내지 않음" in html_txt
    posts = [str(p)]
    assert _latest_gu_post_href(posts, "강남") is None          # 구 포스트로 오인하지 않는다
    assert _latest_period_post_href(posts) == str(p)


def test_write_period_post_monthly_includes_weekly_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_PERIODIC_POSTS", "monthly")
    snap = tmp_path / "snapshots"; snap.mkdir()
    for dt in ("2026-09-06", "2026-09-13", "2026-09-20"):
        (snap / f"dataset-{dt}.json").write_text(json.dumps(_ds([_row("강남", "A", 9.9)] * 1, gen=dt), ensure_ascii=False), encoding="utf-8")
    res = pd.write_period_post(_ds([_row("강남", "A", 10.1)], gen="2026-09-27"), "2026-09-27", str(tmp_path))
    assert res["kind"] == "monthly" and res["base_date"] == "2026-09-06"     # 08-30 없음 → 21일 전 대체
    html_txt = (tmp_path / "posts" / "2026-09-27-월간결산.html").read_text(encoding="utf-8")
    assert "주차별" in html_txt and "2026-09-13" in html_txt and "이번 주(2026-09-20" in html_txt


def test_write_period_post_skips_without_base_or_off_day(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    assert pd.write_period_post(_ds([_row("강남", "A", 9.9)]), "2026-09-13", str(tmp_path))["skipped"].startswith("no_base")
    monkeypatch.delenv("RE_PERIODIC_POSTS", raising=False)
    assert pd.write_period_post(_ds([]), "2026-09-14", str(tmp_path))["skipped"] == "no_period_today"


def test_render_worst_case_fits_tistory_budget_by_trimming():
    """25구·이동 60곳·최고가 경신 20건·월간 주차 5행 — 사이트 본문은 전체, 티스토리는 단계 축약으로 예산 안."""
    gus = list(pd.be.GU_LAWD)
    base_rows = [_row(gus[i % 25], f"단지{i:03d}", 9.0 + (i % 12), pos=100) for i in range(300)]
    cur_rows = [_row(gus[i % 25], f"단지{i:03d}", (9.0 + (i % 12)) * (1.25 if i < 60 else 1.0), pos=100) for i in range(300)]
    cur, base = _ds(cur_rows, gen="2026-09-27"), _ds(base_rows, gen="2026-08-30")
    prev = {"11680": [{"apt": f"경신{i}", "area": 84.9, "price": p, "ym": "202506"} for i in range(20) for p in (18e8, 19e8, 20e8)]}
    curm = {"11680": prev["11680"] + [{"apt": f"경신{i}", "area": 84.9, "price": 21e8, "ym": "202509"} for i in range(20)]}
    wk = pd.build_period_delta(cur, base, "weekly", "2026-09-27", date(2026, 9, 20))
    rows = [{"date": f"2026-09-{dd:02d}", "n": 300, "seoul_median": 12.0, "delta_pct": 0.1, "bands": {b: 75 for b in pd.BANDS}}
            for dd in (6, 13, 20, 27)]
    d = pd.build_period_delta(cur, base, "monthly", "2026-09-27", date(2026, 8, 30),
                              filings=pd.diff_filings(prev, curm), weekly=wk, weekly_rows=rows)
    assert len(d["migrations"]) >= 40 and len(d["filings"]["record_highs"]) == 20
    post = pd.render_period_post(d)
    assert len(post["tistory_html"].encode()) <= pd.TISTORY_BUDGET and post["tistory_level"] > 0
    assert "지면 한도로" in post["tistory_html"] and "지면 한도로" not in post["html"]
    assert post["html"].count("52주 상단(기준→이번)") == 1 and "경신0(" in post["html"]      # 사이트는 전체 표
    assert len(post["claims"]) == 4 + 25 + len(d["migrations"])


def test_monthly_with_macro_extras_fits_budget():
    """R1(10-26): 10-25 월간결산이 공시가 배율·평형 격차·거시 맥락까지 붙어 마지막 축약에도 35KB → ValueError 로
    블로그 생성 전체가 멈춰 일간 원고도 못 나갔다. 실제 10-25 세 절(fixture)을 붙인 최악 사례도 예산 안이어야 한다."""
    from pathlib import Path
    extras = json.loads((Path(__file__).parent / "fixtures/period_monthly_extras_2026-10-25.json").read_text(encoding="utf-8"))
    gus = list(pd.be.GU_LAWD)
    base_rows = [_row(gus[i % 25], f"단지{i:03d}", 9.0 + (i % 12), pos=100) for i in range(300)]
    cur_rows = [_row(gus[i % 25], f"단지{i:03d}", (9.0 + (i % 12)) * (1.25 if i < 60 else 1.0), pos=100) for i in range(300)]
    cur, base = _ds(cur_rows, gen="2026-10-25"), _ds(base_rows, gen="2026-09-27")
    prev = {"11680": [{"apt": f"경신{i}", "area": 84.9, "price": p, "ym": "202506"} for i in range(20) for p in (18e8, 19e8, 20e8)]}
    curm = {"11680": prev["11680"] + [{"apt": f"경신{i}", "area": 84.9, "price": 21e8, "ym": "202510"} for i in range(20)]}
    wk = pd.build_period_delta(cur, base, "weekly", "2026-10-25", date(2026, 10, 18))
    rows = [{"date": f"2026-10-{dd:02d}", "n": 300, "seoul_median": 12.0, "delta_pct": 0.1, "bands": {b: 75 for b in pd.BANDS}}
            for dd in (4, 11, 18, 25)]
    d = pd.build_period_delta(cur, base, "monthly", "2026-10-25", date(2026, 9, 27),
                              filings=pd.diff_filings(prev, curm), weekly=wk, weekly_rows=rows)
    d.update(extras)
    post = pd.render_period_post(d)
    assert len(post["tistory_html"].encode()) <= pd.TISTORY_BUDGET
    assert "지면 한도로" in post["tistory_html"]
    assert "거시 맥락" in post["html"] and "공시가 배율" in post["html"]          # 사이트는 전체 절


def test_pool_change_keeps_medians_on_paired_complexes(tmp_path, monkeypatch):
    """발행 풀이 640→927 처럼 커져도 '기준→이번' 중위는 짝 단지만으로 — 새 단지 유입이 +22.8% 로 찍히던 경로(게이트 ② Codex 판정)."""
    base = _ds([_row("강남", "A", 10.0), _row("강남", "B", 12.0), _row("노원", "C", 5.0)], asof="2026-08-30", gen="2026-09-06")
    cur = _ds(base["complexes"] + [_row("강남", f"N{i}", 30.0) for i in range(5)], gen="2026-09-13")
    d = pd.build_period_delta(cur, base, "weekly", "2026-09-13", date(2026, 9, 6))
    assert d["n_pairs"] == 3 and d["seoul_median_base"] == d["seoul_median_cur"] == 10.0 and d["seoul_median_pool_cur"] == 30.0
    gus = {g["gu"]: g for g in d["gus"]}
    assert gus["강남"]["n_cur"] == 7 and gus["강남"]["gu_median_delta_pct"] == 0.0 and gus["강남"]["median_cur"] == gus["강남"]["median_base"] == 11.0
    bands = {b["band"]: b for b in d["bands"]}
    assert bands["20억 이상"]["n_cur"] == 5 and bands["20억 이상"]["median_cur"] is None      # 짝 없는 밴드는 수준도 —
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"
    snap.mkdir()
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    pd.write_period_post(cur, "2026-09-13", str(tmp_path))
    html_txt = (tmp_path / "posts" / "2026-09-13-주간결산.html").read_text(encoding="utf-8")
    assert "짝지은 3단지 중 양쪽 집계 게이트 통과 3단지만으로 계산" in html_txt and "(+0.0%)" in html_txt and "+200.0%" not in html_txt
    assert "짝 기준→이번" in html_txt and "시점별 소속" in html_txt


def test_monthly_weekly_rows_delta_uses_paired_complexes(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_PERIODIC_POSTS", "monthly")
    snap = tmp_path / "snapshots"
    snap.mkdir()
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(_ds([_row("강남", "A", 9.9)], gen="2026-09-06"), ensure_ascii=False), encoding="utf-8")
    (snap / "dataset-2026-09-13.json").write_text(json.dumps(_ds([_row("강남", "A", 9.9), _row("서초", "B", 30.0)], gen="2026-09-13"), ensure_ascii=False), encoding="utf-8")
    pd.write_period_post(_ds([_row("강남", "A", 9.9), _row("서초", "B", 30.0)], gen="2026-09-27"), "2026-09-27", str(tmp_path))
    html_txt = (tmp_path / "posts" / "2026-09-27-월간결산.html").read_text(encoding="utf-8")
    assert "직전 주 대비(짝 n)" in html_txt and "+101.5%" not in html_txt              # 풀 [9.9]→[9.9, 30] 의 수준 차이가 Δ 로 찍히지 않는다
    assert "+0.0% n1" in html_txt and "서울 중위(억, 풀 전체)" in html_txt                  # Δ 의 표본 수와 '풀 전체' 수준을 구분(S7 Codex)


def test_gate_crossing_pairs_do_not_create_delta(tmp_path, monkeypatch):
    """S7 Codex P1: 가격 고정·거래표본만 9→10건이면 한쪽만 집계 게이트를 넘어 서울·구·주차별 Δ 가 +100% 로 찍히던 경로 —
    양쪽 게이트 통과 짝(n_valid)만으로 비교하고 표시한 표본 수 = 계산에 쓴 수."""
    base = _ds([_row("강남", f"L{i}", 10.0, n=15) for i in range(5)] + [_row("강남", f"H{i}", 30.0, n=9) for i in range(5)], gen="2026-09-06")
    cur = _ds([_row("강남", f"L{i}", 10.0, n=15) for i in range(5)] + [_row("강남", f"H{i}", 30.0, n=10) for i in range(5)], gen="2026-09-13")
    d = pd.build_period_delta(cur, base, "weekly", "2026-09-13", date(2026, 9, 6))
    assert d["n_pairs"] == 10 and d["n_valid"] == 5 and d["seoul_median_base"] == d["seoul_median_cur"] == 10.0
    g = {x["gu"]: x for x in d["gus"]}["강남"]
    assert g["gu_median_delta_pct"] == 0.0 and g["n_valid"] == 5 and g["n_pairs"] == 10
    monkeypatch.setenv("RE_PERIODIC_POSTS", "monthly")
    snap = tmp_path / "snapshots"
    snap.mkdir()
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    (snap / "dataset-2026-09-13.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    pd.write_period_post(cur, "2026-09-27", str(tmp_path))
    html_txt = (tmp_path / "posts" / "2026-09-27-월간결산.html").read_text(encoding="utf-8")
    assert "+100.0%" not in html_txt and "+0.0% n5" in html_txt and "양쪽 집계 게이트 통과 5단지" in html_txt
    claims = [json.loads(x) for x in (tmp_path / "posts" / "2026-09-27-월간결산.claims.jsonl").read_text(encoding="utf-8").splitlines()]
    c = [x for x in claims if x["claim"] == "monthly_gu_change"][0]
    assert c["n_valid"] == 5 and c["n_pairs"] == 10 and c["median_basis"] == "valid_pairs" and c["delta_pct"] == 0.0


def test_band_cell_shows_per_period_membership_and_counts(tmp_path, monkeypatch):
    """S7 Codex P2: 밴드 중위는 시점별 소속 분포 — 14.9→15.0 이동만으로 '10~15억' 중위가 13.45→12 로 바뀌므로 양쪽 표본 수·설명을 함께 적는다."""
    base = _ds([_row("강남", f"S{i}", 12.0) for i in range(5)] + [_row("강남", f"M{i}", 14.9) for i in range(5)], gen="2026-09-06")
    cur = _ds([_row("강남", f"S{i}", 12.0) for i in range(5)] + [_row("강남", f"M{i}", 15.0) for i in range(5)], gen="2026-09-13")
    b = {x["band"]: x for x in pd.compute_band_deltas(cur, base)}["10~15억"]
    assert (b["median_base"], b["n_med_base"], b["median_cur"], b["n_med_cur"]) == (13.45, 10, 12.0, 5) and b["delta_median_pct"] == 0.0
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"
    snap.mkdir()
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    pd.write_period_post(cur, "2026-09-13", str(tmp_path))
    html_txt = (tmp_path / "posts" / "2026-09-13-주간결산.html").read_text(encoding="utf-8")
    assert "13.45억(n10) → 12억(n5)" in html_txt and "시점별 소속" in html_txt and "밴드 이동으로 구성이 달라짐" in html_txt
    claims = [json.loads(x) for x in (tmp_path / "posts" / "2026-09-13-주간결산.claims.jsonl").read_text(encoding="utf-8").splitlines()]
    c = [x for x in claims if x["claim"] == "weekly_band_change" and x["band"] == "10~15억"][0]
    assert c["n_med_base"] == 10 and c["n_med_cur"] == 5 and c["median_basis"] == "valid_pairs_by_period_band"


def test_unit_deltas_and_migrations_exclude_gate_crossing_pairs():
    """기준 n9→이번 n10 단지가 9→12억이어도 통계·이동에는 양쪽 통과 5개만 사용."""
    base = _ds([_row("강남", f"S{i}", 12.0) for i in range(5)]
               + [_row("강남", f"L{i}", 9.0, n=9) for i in range(6)], gen="2026-09-06")
    cur = _ds([_row("강남", f"S{i}", 12.0) for i in range(5)]
              + [_row("강남", f"L{i}", 12.0, n=10) for i in range(6)], gen="2026-09-13")
    d = pd.build_period_delta(cur, base, "weekly", "2026-09-13", date(2026, 9, 6))
    g = d["gus"][0]
    assert (g["n_pairs"], g["n_valid"], g["n_delta"]) == (11, 5, 5)
    assert g["delta_median_pct"] == g["gu_median_delta_pct"] == 0.0
    assert not d["migrations"]
    b = next(x for x in d["bands"] if x["band"] == "10~15억")
    assert b["n_pairs"] == 5 and b["delta_median_pct"] == 0.0 and b["moved_in"] == 0
    p = pd.render_period_post(d)
    assert "+33.3%" not in p["tistory_html"] and "가격대 이동 단지 0곳" in p["tistory_html"]
    c = next(x for x in p["claims"] if x["claim"] == "weekly_gu_change")
    assert c["n_pairs"] == 11 and c["n_delta"] == 5 and c["delta_median_pct"] == 0.0
    # 역방향도 현재 n9 때문에 제외되어야 한다.
    back = pd.build_period_delta(base, cur, "weekly", "2026-09-20", date(2026, 9, 13))
    assert back["gus"][0]["delta_median_pct"] == 0.0 and not back["migrations"]


def test_filing_disappearance_does_not_claim_cancellation():
    rows = [_row("강남", f"A{i}", 12.0) for i in range(5)]
    d = pd.build_period_delta(_ds(rows), _ds(rows), "weekly", "2026-09-13", date(2026, 9, 6),
                             filings={"new": [], "n_new": 0, "n_gone": 7, "record_highs": []})
    p = pd.render_period_post(d)
    for text in (p["html"], p["tistory_html"]):
        assert "사라진 건 7건(창 이탈·해제·정정 등, 사유 미확인)" in text
        assert "사라진 건(해제·정정)" not in text


def _dated(apt, price, day, area=84.9):
    return {"apt": apt, "area": area, "price": price, "ym": day[:4] + day[5:7], "deal_date": day}


def test_diff_filings_keys_on_contract_day_when_both_sides_carry_it():
    """계약일 키(2026-10-14 승인) — 계약일만 정정된 거래는 새 기록 1 + 사라진 기록 1(용산더프라임 4/27→4/28 실측)."""
    keep = _dated("K", 9e8, "2026-04-10")
    prev = {"11170": [_dated("P", 30e8, "2026-04-27"), keep, keep]}
    cur = {"11170": [_dated("P", 30e8, "2026-04-28"), keep, keep]}
    d = pd.diff_filings(prev, cur)
    assert (d["n_new"], d["n_gone"], d["key_fields"]) == (1, 1, "apt,area,price,ym,deal_date")
    assert d["new"][0]["deal_date"] == "2026-04-28"


def test_diff_filings_same_day_duplicates_count_by_multiset():
    a, b = _dated("A", 10e8, "2026-08-01"), _dated("A", 10e8, "2026-08-02")
    d = pd.diff_filings({"11680": [a, b]}, {"11680": [a, b, b]})
    assert (d["n_new"], d["n_gone"]) == (1, 0) and d["new"][0]["deal_date"] == "2026-08-02"


def test_diff_filings_falls_back_to_legacy_key_when_any_side_lacks_contract_day():
    legacy = {"apt": "P", "area": 84.9, "price": 30e8, "ym": "202604"}
    d = pd.diff_filings({"11170": [legacy]}, {"11170": [_dated("P", 30e8, "2026-04-28")]})
    assert (d["n_new"], d["n_gone"], d["key_fields"]) == (0, 0, "apt,area,price,ym")


def test_diff_filings_contract_day_key_keeps_window_exit_and_record_highs():
    old = [_dated("B", p, "2025-06-1%d" % i) for i, p in enumerate((18e8, 19e8, 20e8))]
    cur = [_dated("B", 21e8, "2026-09-03")]
    d = pd.diff_filings({"11680": old}, {"11680": cur}, window_start="202510")
    assert (d["n_gone_window"], d["n_gone_other"]) == (3, 0)
    assert [(h["apt"], h["prev_max"]) for h in d["record_highs"]] == [("B", 20e8)]


def test_diff_filings_decides_contract_day_key_per_district():
    """G6 Codex: 한 구의 구캐시(계약일 없음)가 다른 구의 계약일 비교까지 끄던 전환 반례."""
    legacy = {"apt": "S", "area": 59.9, "price": 12e8, "ym": "202608"}
    prev = {"11170": [_dated("P", 30e8, "2026-04-27")], "11710": [legacy]}
    cur = {"11170": [_dated("P", 30e8, "2026-04-28")], "11710": [dict(legacy)]}
    d = pd.diff_filings(prev, cur)
    assert (d["n_new"], d["n_gone"]) == (1, 1)
    assert d["key_fields"] == "mixed" and d["key_fields_by_lawd"] == {"11170": "apt,area,price,ym,deal_date",
                                                                     "11710": "apt,area,price,ym"}


def test_diff_filings_counts_contract_day_only_pairs_and_summary_keeps_them():
    """계약일만 다른 새/사라짐 짝은 '정정일 수 있는 짝'으로 따로 센다 — 같은 거래라고 단정하지 않는다."""
    prev = {"11170": [_dated("P", 30e8, "2026-04-27"), _dated("Q", 9e8, "2026-08-02")]}
    cur = {"11170": [_dated("P", 30e8, "2026-04-28"), _dated("R", 7e8, "2026-09-01")]}
    d = pd.diff_filings(prev, cur)
    assert (d["n_new"], d["n_gone"], d["n_day_only_pairs"]) == (2, 2, 1)
    s = pd.summarize_filings(d)
    assert s["n_day_only_pairs"] == 1 and s["key_fields"] == "apt,area,price,ym,deal_date"
    txt = pd.filing_gone_txt({**s, "n_gone_window": 0, "n_gone_other": 2, "n_lawd_skipped": 1})
    assert "사라진 기록 중 새 기록과 계약일만 다른 것 1건(정정일 수 있음)" in txt
    assert txt.index("계약일만") < txt.index("수집 누락")   # R3 Codex: '그중'이 제외 구를 가리키지 않게
    assert "계약일만" not in pd.filing_gone_txt({**s, "n_day_only_pairs": 0, "n_gone_window": 0, "n_gone_other": 2})


def _frec(apt, ym, price=1_000_000_000, area=84.97):
    return {"apt": apt, "area": area, "price": price, "ym": ym}


def test_diff_filings_sets_aside_months_after_the_base_archive_window():
    """월이 바뀌면 수집기가 직전 달을 처음 담는다(2026-10-04 주간결산 '새로 확인된 신고 2335건' 중 2239건이 2026-09) —
    기준 아카이브 수집 창 뒤의 월은 그 아카이브에 있을 수 없던 기록이라 새 기록이 아니고 따로 센다."""
    assert (pd.molit_window_end("2026-09-27"), pd.molit_window_end("2026-10-04")) == ("202608", "202609")
    prev = {"11710": [_frec("가", "202509"), _frec("가", "202608")]}
    cur = {"11710": [_frec("가", "202608"), _frec("나", "202608"), _frec("다", "202609"), _frec("라", "202609")]}
    f = pd.diff_filings(prev, cur, window_start=pd.molit_window_start("2026-10-04"),
                        base_window_end=pd.molit_window_end("2026-09-27"))
    assert [r["apt"] for r in f["new"]] == ["나"] and f["ref_ym"] == "202608"
    assert (f["n_new_window"], f["new_window_by_gu"], f["new_window_yms"]) == (2, {"송파": 2}, ["202609"])
    assert (f["n_gone_window"], f["n_gone_other"]) == (1, 0)
    s = pd.summarize_filings(f)
    assert (s["n_new"], s["n_new_window"], s["new_window_yms"]) == (1, 2, ["202609"])
    plain = pd.diff_filings(prev, cur)
    assert plain["n_new"] == 3 and plain["n_new_window"] is None and pd.filing_window_txt(plain) == ""


def test_weekly_post_on_a_month_boundary_counts_only_records_the_base_archive_could_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"
    (snap / "molit").mkdir(parents=True)
    (snap / "dataset-2026-09-27.json").write_text(json.dumps(_ds([_row("송파", "A", 10.0)], gen="2026-09-27"),
                                                             ensure_ascii=False), encoding="utf-8")
    with gzip.open(snap / "molit" / "molit-2026-09-27.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"11710": [_frec("가", "202608")]}, fh)
    raw = tmp_path / "molit_raw.json"
    raw.write_text(json.dumps({"11710": [_frec("가", "202608"), _frec("나", "202608"),
                                         _frec("다", "202609"), _frec("라", "202609")]}), encoding="utf-8")
    pd.write_period_post(_ds([_row("송파", "A", 10.0)], gen="2026-10-04"), "2026-10-04", str(tmp_path), molit_path=str(raw))
    html_txt = (tmp_path / "posts" / "2026-10-04-주간결산.html").read_text(encoding="utf-8")
    assert "새로 나타난 실거래 기록 1건" in html_txt
    assert "수집 창에 새로 든 2026-09 계약 2건은 비교에서 뺌" in html_txt


def test_record_highs_skip_months_after_the_base_archive_window():
    prev = {"11710": [_frec("가", ym, 900_000_000) for ym in ("202606", "202607", "202608")]}
    cur = {"11710": prev["11710"] + [_frec("가", "202608", 950_000_000), _frec("가", "202609", 1_200_000_000)]}
    f = pd.diff_filings(prev, cur, window_start=pd.molit_window_start("2026-10-04"), base_window_end="202608")
    assert [h["price"] for h in f["record_highs"]] == [950_000_000]


def test_weekly_post_takes_the_window_end_from_the_archive_date_not_the_base_snapshot(tmp_path, monkeypatch):
    """기준 스냅샷 05-31 에 아카이브가 없고 ±1일 대체 아카이브가 06-01 이면 그 아카이브엔 이미 2026-05 가 있다 —
    스냅샷 날짜(창 끝 2026-04)로 자르면 2026-05 기록 전부가 '창에 새로 든' 것으로 잘못 빠진다."""
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"
    (snap / "molit").mkdir(parents=True)
    (snap / "dataset-2026-05-31.json").write_text(json.dumps(_ds([_row("송파", "A", 10.0)], gen="2026-05-31"),
                                                             ensure_ascii=False), encoding="utf-8")
    with gzip.open(snap / "molit" / "molit-2026-06-01.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"11710": [_frec("가", "202605")]}, fh)
    raw = tmp_path / "molit_raw.json"
    raw.write_text(json.dumps({"11710": [_frec("가", "202605"), _frec("나", "202605")]}), encoding="utf-8")
    pd.write_period_post(_ds([_row("송파", "A", 10.0)], gen="2026-06-07"), "2026-06-07", str(tmp_path), molit_path=str(raw))
    html_txt = (tmp_path / "posts" / "2026-06-07-주간결산.html").read_text(encoding="utf-8")
    assert "새로 나타난 실거래 기록 1건" in html_txt and "수집 창에 새로 든" not in html_txt


def test_period_failure_does_not_stop_daily_run(monkeypatch, capsys):
    """R1(10-26): 결산 렌더 예외가 run_daily 를 rc=1 로 끝내 그날 일간 원고·사이트·티스토리 발행이 모두 멈췄다."""
    from unittest.mock import Mock
    from blog import run_daily
    monkeypatch.setattr(pd, "write_period_post", Mock(side_effect=ValueError("budget")))
    assert run_daily.write_period_post_guarded({}, "2026-10-25", "out", None, None) == {"failed": "ValueError"}
    assert "결산 생성 실패" in capsys.readouterr().err


def test_plain_period_line_leads_both_bodies_in_plain_words():
    # DIKW 검토 2026-10-30 P0 — 결산 첫머리 한눈에 보기. 숫자는 '서울 중위' 줄과 같다.
    base_rows = [_row("강남", f"단지{i}", 10.0, pos=100) for i in range(12)]
    cur_rows = [_row("강남", f"단지{i}", 10.0 if i % 2 else 11.0, pos=100) for i in range(12)]
    d = pd.build_period_delta(_ds(cur_rows, gen="2026-09-20"), _ds(base_rows, gen="2026-09-13"), "weekly", "2026-09-20",
                              date(2026, 9, 13))
    line = pd.plain_period_line(d)
    assert line.startswith(f"지난 7일 동안 두 번 다 집계된 같은 서울 아파트 {d['n_valid']:,}곳의 가격 가운데 값은 ")
    assert f"{pd._pct(d['seoul_median_cur'], d['seoul_median_base']):+.1f}% 움직였습니다" in line or "그대로였습니다" in line
    for word in ("게이트", "짝", "중위", "IQR", "전망", "매수"):
        assert word not in line, word
    post = pd.render_period_post(d)
    for body in (post["html"], post["tistory_html"]):
        assert "<p>한눈에 보기 — 지난 7일" in body
        assert body.index("한눈에 보기") < body.index("기준 스냅샷")
    assert pd.plain_period_line({**d, "seoul_median_base": None}) == ""
