"""주간결산/월간결산(blog.period_delta, 2026-09-07) 단위테스트 — 달력 판정·base 해소(전월 마지막 일요일→대체)·밴드 재계산과
이동·구별 Δ와 표본 억제·신고 델타 multiset·렌더 메타(_post_meta 규약)·claims·periodic 원고·구 포스트 오인 방지."""
from __future__ import annotations
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
    assert d == "2026-09-13" and "주간결산" in t and "2026-09-06" in t and desc and '"' not in desc
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
