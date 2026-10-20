"""외부 리뷰(2026-10-13) S2 — 제목(C2·U7)·관측요약 범위(C3·C4·C5)·구성 효과(C10)·매매·전세 중위 차이(C9).

C2·U7: 제목이 최근 3개월 2건짜리 단지명과 '갭'을 앞세웠다 → 서울 전체 집계만, 짧게.
C3·C4: 1,755 관측 중 486단지 결과를 서울 전체처럼 읽게 했다 → 깔때기·관측 대비 비율을 붙인다.
C5: 최근 3개월 2건 단지가 상승 수를 채운다 → 최근 3개월 5건 이상 보조 집계를 붙인다.
C9: '갭'이 필요 자기자본처럼 읽힌다 → '매매·전세 중위 차이' + 자기자본 아님 고지.
C10: 중위 변화는 거래 구성(동·층·면적) 변화도 섞인다 → 한 줄 고지.
"""
import ast
import pathlib
import re

from blog import build_explorer as be
from blog import daily_digest as dd
from blog import market_charts as mch

_COUNTS = {"n_total": 1755, "n_judged": 486, "up": 464, "down": 21, "flat": 1,
           "n_robust": 193, "up_robust": 188, "down_robust": 5, "robust_min_recent": 5,
           "funnel": {"n_total": 1755, "n_priced": 1631, "n_no_price": 124, "excl_non_apt": 241,
                      "excl_small": 26, "excl_thin": 709, "n_gated": 655, "n_judged": 486, "n_unjudged": 169}}


def test_title_names_no_complex_and_no_gap_and_states_the_judged_base():
    sel = {"hi": [{"name": "관악동부센트레빌", "gu": "관악"}], "lo": [],
           "jr": [{"name": "영등포자이르네", "gu": "영등포", "gap_eok": 1.28}], "tv": []}
    title = dd._build_digest_title("2026-10-13", _COUNTS, sel)
    assert title == "서울 아파트 실거래가 — 비교 486단지, 최근 3개월 vs 직전 9개월 (2026-10-13)"   # 비교 분모·기간 유지, 방향별 수치는 본문
    assert "관악동부센트레빌" not in title and "갭" not in title


def test_title_without_judged_complexes_falls_back_to_observation_count():
    title = dd._build_digest_title("2026-10-13", {"n_total": 7, "n_judged": 0, "up": 0, "down": 0}, {})
    assert title == "서울 주거용 부동산 실거래가 — 관측 7단지, 기간 비교 미산출 (2026-10-13)"


def test_observation_summary_states_share_robust_subset_funnel_and_mix_caveat():
    lines = mch.observation_summary_lines(_COUNTS, None, asof="2026-10-12")
    text = " ".join(lines)
    assert "3·9개월 비교가 가능한 486단지(관측의 27.7%)" in text
    assert "높은 곳 464 · 낮은 곳 21" in text
    # Codex X4 — 실제 비교 창(당월 제외)을 적는다
    assert "최근 3개월(2026-07~09) 실거래 중위가 직전 9개월(2025-10~2026-06)보다" in text
    assert "최근 3개월 거래 5건 이상인 193단지만 보면 높은 곳 188 · 낮은 곳 5" in text
    # R2 Codex C3 — 단계별 제외 사유·수까지
    assert ("관측 1,755 → 12개월 가격 1,631(가격 미산출 124) → 아파트·전용 40㎡+·매매 10건+ 655"
            "(비아파트 241 · 40㎡ 미만 26 · 매매 10건 미만 709) → 3·9개월 각 2건+ 486(한 기간이라도 2건 미만 169)") in text
    assert "구성" in text and "같은 집" in text
    assert "판정" not in text


def test_observation_summary_without_funnel_keeps_the_count_line():
    counts = {k: v for k, v in _COUNTS.items() if k not in ("funnel", "n_robust", "up_robust", "down_robust")}
    text = " ".join(mch.observation_summary_lines(counts, None, asof="2026-10-12"))
    assert "높은 곳 464 · 낮은 곳 21" in text
    assert "→" not in text and "5건 이상" not in text


def test_daily_counts_carry_funnel_and_robust_subset_into_the_body():
    rows = [dict(gu="강남", name=f"단지{i}", product_type="아파트", area_m2=59.0, molit_n=15, molit_recent_eok=10.0,
                 molit_trend_dir="▲", molit_trend_pct=3.0, molit_trend_n_recent=6 if i < 2 else 2,
                 molit_trend_n_prior=10, molit_pos_52w=50) for i in range(4)]
    ds = {"complexes": rows, "count": 4, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    c = dd._today_counts(ds)
    assert c["funnel"]["n_judged"] == 4 and c["n_robust"] == 2


def _jr_row():
    return dict(gu="영등포", name="전세단지", product_type="아파트", area_m2=59.0, molit_n=12,
                molit_recent_eok=8.0, molit_pos_52w=50, molit_trend_dir=None, molit_trend_pct=None,
                jeonse_recent_eok=6.72, jeonse_n=7, gap_eok=1.28, jeonse_ratio_complex_pct=84.0)


def test_daily_body_calls_it_median_spread_not_gap_and_denies_equity_reading():
    ds = {"complexes": [_jr_row()], "count": 1, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    d = dd.build_daily_digest(ds, "2026-10-13", "2026-10-12")
    for body in (d["tistory_html"], d["site_html"]):
        assert "갭" not in re.sub(r"<[^>]+>", "", body)
        assert "매매·전세 중위 차이" in body
        assert "자기자본" in body


def _public_strings(path):
    """docstring 을 뺀 문자열 상수(f-string 조각 포함) — 주석·docstring 은 공개 표면이 아니다."""
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_no_public_gap_label_left_in_generators():
    """C9 — 공개 표면(탐색기·단지 페이지·사실 리드·일간)에 '갭' 라벨이 남지 않는다."""
    root = pathlib.Path(dd.__file__).parent
    hits = [f"{name}:{line}" for name in ("build_explorer.py", "complex_page.py", "fact_lead.py", "daily_digest.py")
            for line, text in _public_strings(root / name) if "갭" in text]
    assert hits == []


def test_funnel_partition_matches_counts_used_in_summary():
    """C3 — 깔때기 마지막 단계 = 표본기준추세수(같은 함수 계열)."""
    rows = [dict(gu="강남", name="가", product_type="아파트", area_m2=59.0, molit_n=12, molit_recent_eok=9.0,
                 molit_trend_dir="▲", molit_trend_pct=2.0, molit_trend_n_recent=3, molit_trend_n_prior=9)]
    assert be.compute_observation_funnel(rows)["n_judged"] == be.count_gated_trends(rows)["n_judged"]
