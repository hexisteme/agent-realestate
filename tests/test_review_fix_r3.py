"""R3 적대 검증(Codex·agy, 2026-10-13) 채택 결함 — 10-codex-r3.md · 11-agy-r3.md.

F5-빈목록 / W1-동명후보 / F9-보조요약 / C14-신선도 문구 / C13-검수 패킷 호출 / F7-비시각 구 칸.
"""
import gzip
import json
from datetime import date
from pathlib import Path

from blog import build_explorer as be
from blog import market_charts as mch
from blog import period_delta as pd
from test_review_fix_method import ASOF
from test_review_fix_r2 import _delta, _rec, _row


# ── F5-빈목록 ────────────────────────────────────────────────────────────────

def test_f5_empty_district_today_is_skipped_in_counts_too():
    prev = {"11710": [_rec("가", "202601"), _rec("나", "202609")]}
    f = pd.diff_filings(prev, {"11710": []}, window_start="202510")
    assert f["n_lawd_skipped"] == 1
    assert (f["n_gone"], f["n_gone_window"], f["n_gone_other"]) == (0, 0, 0)


# ── W1-동명후보 ──────────────────────────────────────────────────────────────

def test_w1_same_name_candidates_pair_by_area_regardless_of_order():
    base = [_row("현대", 59.0, "", 12.0), _row("현대", 84.0, "", 14.0)]
    cur = [_row("현대", 59.0, "A1", 13.2), _row("현대", 84.0, "A2", 12.6)]
    for b in (base, base[::-1]):
        d = _delta(b, cur)
        assert d["n_pairs"] == 2 and d["area_changed"] == [] and d["added"] == [] and d["removed"] == []


def test_w1_ambiguous_same_name_area_change_is_not_guessed():
    base = [_row("현대", 59.0, "", 12.0), _row("현대", 84.0, "", 14.0)]
    cur = [_row("현대", 74.0, "A1", 13.0), _row("현대", 101.0, "A2", 15.0)]
    d = _delta(base, cur)
    assert d["n_pairs"] == 0 and d["area_changed"] == []
    assert d["added"] == ["현대(송파)", "현대(송파)"] and d["removed"] == ["현대(송파)", "현대(송파)"]


# ── F9-보조요약 ──────────────────────────────────────────────────────────────

def test_f9_robust_subset_line_states_flat_too():
    counts = {"n_total": 10, "n_judged": 4, "up": 2, "down": 0, "n_robust": 3, "up_robust": 1,
              "down_robust": 0, "robust_min_recent": 5}
    text = " ".join(mch.observation_summary_lines(counts, None, asof=ASOF))
    assert "3단지만 보면 높은 곳 1 · 낮은 곳 0 · 보합 2." in text


# ── C14-신선도 문구 ──────────────────────────────────────────────────────────

def test_c14_contract_month_breakdown_names_months_and_does_not_assert_cause():
    f = pd.diff_filings({"11710": []}, {"11710": [_rec("가", "202609"), _rec("나", "202608"),
                                                   _rec("다", "202606"), _rec("라", "202601")]}, window_start="202510")
    txt = pd.filing_age_txt(f)
    assert "계약월 2026-08~09 2건 · 2026-06~07 1건 · 2026-05 이전 1건" in txt
    assert "직전 달" not in txt and "반영 포함" not in txt and "사유 미확인" in txt


def test_c14_no_new_records_has_no_breakdown():
    same = {"11710": [_rec("가", "202609")]}
    assert pd.filing_age_txt(pd.diff_filings(same, same, window_start="202510")) == ""


# ── C13-검수 패킷 ────────────────────────────────────────────────────────────

def test_c13_review_packet_passes_the_collection_window(monkeypatch, tmp_path):
    from blog import review_periodic as rp
    seen = {}
    real = rp.pd.diff_filings

    def spy(prev, cur, window_start=None):
        seen["window_start"] = window_start
        return real(prev, cur, window_start=window_start)

    archive = tmp_path / "molit.json.gz"
    archive.write_bytes(gzip.compress(json.dumps({"11680": [_rec("가", "202609")]}).encode()))
    monkeypatch.setattr(rp, "molit_archive_path_on", lambda *a, **k: str(archive))
    monkeypatch.setattr(rp.pd, "diff_filings", spy)
    monkeypatch.setattr(rp, "_today", lambda: date(2026, 9, 13))
    report = rp.PROJECT_ROOT / "report"
    import tempfile
    with tempfile.TemporaryDirectory(prefix="test-period-review-", dir=report) as folder:
        root = Path(folder)
        snaps = root / "snapshots"
        snaps.mkdir()
        rows = [dict(gu="강남", name=f"단지{i}", area_m2=84.0, product_type="아파트", molit_n=15,
                     molit_recent_eok=10.0, molit_pos_52w=50, price_segment="10~15억") for i in range(3)]
        mk = lambda d: {"generated": d, "data_asof": d, "count": len(rows), "complexes": rows}
        (snaps / "dataset-2026-09-06.json").write_text(json.dumps(mk("2026-09-06")))
        ds = root / "dataset.json"
        ds.write_text(json.dumps(mk("2026-09-13")))
        rp.review_periodic(today="2026-09-13", dataset=str(ds), snapshots=str(snaps), out=str(root / "review"))
    assert seen["window_start"] == pd.molit_window_start("2026-09-13")


# ── F7-비시각 ────────────────────────────────────────────────────────────────

def test_f7_tistory_heatmap_cell_names_the_trend_denominator():
    from blog import market_visuals as mv
    judged = dict(gu="강남", name="가", area_m2=84.0, product_type="아파트", molit_n=12, molit_recent_eok=20.0,
                  molit_trend_dir="▲", molit_trend_pct=4.0, molit_trend_n_recent=3, molit_trend_n_prior=9)
    thin = dict(judged, name="나", molit_n=3, molit_trend_dir=None, molit_trend_pct=None)
    html = mv.build_market_visuals({"complexes": [judged, thin], "data_asof": ASOF}, ASOF)["tistory_html"]
    assert "2곳(1)<br>▲1<br>▼0" in html
    assert "괄호=추세 비교 단지(▲▼는 그중)" in html
    assert be.count_gated_trends([judged, thin])["n_judged"] == 1


# ── R4 (13-codex-r4 · 14-agy-r4) ─────────────────────────────────────────────

def _outcome(d):
    return d["n_pairs"], sorted(d["added"]), sorted(d["removed"]), sorted(d["area_changed"])


def test_r4_w1_name_fallback_is_order_independent_and_never_guesses():
    from itertools import permutations
    cases = [  # Codex: 번호 없는 후보와 번호 있는 후보가 같은 면적으로 경쟁 / agy: 101·102 가 번호 없는 행을 매개로 엮임
        ([_row("현대", 59.0, "", 10.0), _row("현대", 59.0, "B", 14.9)],
         [_row("현대", 59.0, "", 11.0), _row("현대", 59.0, "A", 14.9)]),
        ([_row("현대", 84.0, "101", 10.0), _row("현대", 84.0, "", 12.0)],
         [_row("현대", 84.0, "102", 10.5), _row("현대", 84.0, "", 12.5)]),
    ]
    for base, cur in cases:
        outcomes = {repr(_outcome(_delta(list(b), list(c)))) for b in permutations(base) for c in permutations(cur)}
        assert len(outcomes) == 1, outcomes
        assert _outcome(_delta(base, cur))[0] == 0                     # 모호하면 가격 짝을 만들지 않는다


def test_r4_c14_year_boundary_keeps_both_years():
    f = pd.diff_filings({"11710": []}, {"11710": [_rec(x, ym) for x, ym in
                        (("가", "202601"), ("나", "202512"), ("다", "202511"), ("라", "202510"), ("마", "202509"))]})
    assert "계약월 2025-12~2026-01 2건 · 2025-10~11 2건 · 2025-09 이전 1건" in pd.filing_age_txt(f)


# ── R5 (16-codex-r5-w1) ──────────────────────────────────────────────────────

def test_r5_w1_duplicated_complex_no_is_unconfirmed_not_first_come():
    from itertools import permutations
    cases = [([_row("현대", 59.0, "A", 10.0), _row("현대", 84.0, "A", 20.0)], [_row("현대", 59.0, "A", 11.0)]),
             ([_row("현대", 59.0, "A", 10.0)], [_row("현대", 84.0, "A", 20.0), _row("현대", 59.0, "A", 11.0)])]
    for base, cur in cases:
        outcomes = {repr(_outcome(_delta(list(b), list(c)))) for b in permutations(base) for c in permutations(cur)}
        assert len(outcomes) == 1, outcomes
        d = _delta(base, cur)
        assert d["n_pairs"] == 0 and d["area_changed"] == []            # '59→59㎡' 같은 재추론 모순 없음
