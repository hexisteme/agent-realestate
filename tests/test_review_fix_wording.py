"""외부 리뷰(2026-10-13) 교차검증(agy) 추가 결함 — A1 가격대 표 기호, A2 조사, A3 서울 밖 공고, C12 신고기한 문구."""
import re

from blog import daily_digest as dd
from blog import fact_lead as fl
from blog import housing_support as hs


def _row(gu, name, **kw):
    base = dict(gu=gu, name=name, product_type="아파트", area_m2=59.0, molit_n=15, molit_recent_eok=9.0,
                molit_pos_52w=100, molit_trend_dir="▲", molit_trend_pct=5.0)
    base.update(kw)
    return base


def test_band_table_does_not_reuse_trend_symbols_for_52w_proximity():
    """A1 — ▲·▼는 글 전체에서 '최근 3개월 중위 상승·하락'이다. 52주 상단·하단 근접 수에 같은 기호를 쓰지 않는다."""
    ds = {"complexes": [_row("강남", "가"), _row("강남", "나", molit_recent_eok=12.0, molit_pos_52w=3)],
          "count": 2, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    d = dd.build_daily_digest(ds, "2026-10-13", "2026-10-12")
    for body in (d["tistory_html"], d["site_html"]):
        heading = re.search(r'<h[23]\b[^>]*>가격대별 요약', body)
        assert heading
        seg = body[heading.start():]
        seg = seg[:seg.index("</table>")]
        assert not re.search("[▲▼]", seg)
        assert "고점 근접" in seg and "저점 근접" in seg


def test_no_public_surface_shows_partial_maint_fee():
    """C8(Codex·agy 합의) — K-apt 공용관리비 일부 7항목·산출월 미기록 값은 고지서 관리비로 읽힌다. 수집기가 실제 월·성공 월수를
    저장하기 전까지 사실 리드(서울·구·단지)·단지 페이지·탐색기·티스토리 원고 어디에도 싣지 않는다(A2 조사 오류 문장도 함께 사라짐)."""
    import pathlib
    from blog import complex_page as cp
    rows = []
    for i, gu in enumerate(["강남", "서초", "노원", "도봉"] * 4):
        rows.append(_row(gu, f"{gu}{i}", molit_recent_eok=8.0 + i, maint_fee_won=50000 + 3000 * i,
                         parking_per_unit=0.8 + i / 20))
    ds = {"complexes": rows, "data_asof": "2026-10-12"}
    gu_rows = [r for r in rows if r["gu"] == "강남"]
    cands = (fl._seoul_candidates(rows, None, "2026-10-12") + fl._gu_candidates("강남", gu_rows, rows, None)
             + fl._complex_candidates(gu_rows[0], [], gu_rows))
    texts = [c["text"] for c in cands if c]
    assert texts and not [t for t in texts if "관리비" in t]
    assert "maint_fee_won" not in [k for k, *_ in cp._FACT_SPECS]
    root = pathlib.Path(dd.__file__).parent
    assert 'k:"maint_fee_won"' not in (root / "build_explorer.py").read_text()
    assert "관리비 {" not in (root / "tistory_draft.py").read_text()


def test_housing_support_drops_non_seoul_regional_notices():
    """A3 — LH '전국' 지역 공고 중 제목이 서울 밖 지역본부·시도를 명시한 것은 서울 글에서 뺀다."""
    assert not hs.is_seoul_relevant("[인천지역본부] 26년 2차 고령자 매입임대주택 예비입주자 모집")
    assert not hs.is_seoul_relevant("장애인 자립특화형 주택 여기가 입주자 모집공고(경기도 김포시)")
    assert hs.is_seoul_relevant("[서울지역본부] 26년 2차 비분양전환형 든든전세주택 입주자 모집공고")
    assert hs.is_seoul_relevant("2026년 국민임대주택 입주자 모집공고")
    assert hs.is_seoul_relevant("강동구 청년창업가,예술인주택(수요자맞춤형) 잔여세대 모집 공고")


def test_no_absolute_30_day_lag_wording_in_generators():
    """C12 — '신고 지연 최대 30일'은 절대 상한처럼 읽힌다. 생성 코드에 남지 않는다."""
    import pathlib
    root = pathlib.Path(dd.__file__).parent
    hits = [p.name for p in root.glob("*.py") if "최대 30일" in p.read_text()]
    assert hits == []


def test_seoul_briefing_excludes_unverified_gongsi_and_partial_maint_fee():
    """C7·C8 — 공시연도 미확인 공시가 배율(S5)·공용관리비 일부 7항목(S7)은 서울 핵심 요약 후보에서 뺀다."""
    rows = []
    for i, gu in enumerate(["강남", "서초", "노원", "도봉", "구로", "중랑", "은평", "관악"] * 3):
        rows.append(_row(gu, f"{gu}{i}", molit_recent_eok=8.0 + i, gongsi_man=50000 + 1000 * i,
                         gongsi_multiple=1.5 + i / 50, maint_fee_won=50000 + 3000 * i))
    leads = fl.build_fact_leads({"complexes": rows, "data_asof": "2026-10-12"}, "seoul", limit=10)
    assert not {ld["family"] for ld in leads} & {"S5", "S7"}


def test_gongsi_ratio_uses_area_matched_multiple_only():
    """C7 — 사실 리드의 공시가 배율도 면적 일치분(gongsi_multiple)만 쓴다(단지 페이지와 같은 정의)."""
    r = _row("강남", "가", gongsi_man=50000, gongsi_multiple=None)
    assert fl._gongsi_ratio_extractor(r) is None
    r2 = _row("강남", "나", gongsi_man=50000, gongsi_multiple=1.8)
    assert fl._gongsi_ratio_extractor(r2) == 1.8


def test_cta_and_missing_price_wording_do_not_overclaim():
    """Codex X2·X3 — 관측 단지는 주상복합·도생을 포함한다('서울 아파트 N단지 전체' 금지). 중위가 없는 단지는
    거래가 없는 게 아니라 대표면적 중위를 못 낸 것이다('실거래 없음' 금지)."""
    import pathlib
    root = pathlib.Path(dd.__file__).parent
    for name in ("daily_digest.py", "build_explorer.py", "tistory_draft.py"):
        text = (root / name).read_text()
        assert "단지 전체 직접" not in text
        assert "공공 실거래 없음" not in text and ">실거래 없음<" not in text
