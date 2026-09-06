"""탐색기(EXPLORER_HTML) 계측 회귀테스트(2026-09-06 P0) — 옛 `<sup class=sup> F</sup>` 마커가 없고
GA4 이벤트 5종(explorer_filter/sort/search/preset, complex_click)이 typeof gtag==='function' 가드
아래 계측되는지, 사실(F) 고지 문구가 남아있는지 검증한다. 템플릿 문자열 자체와, write_out 이 그것을
그대로 디스크에 써내는 경로 둘 다 확인한다.
"""
from __future__ import annotations

from blog.build_explorer import EXPLORER_HTML, write_out

EVENT_NAMES = ("explorer_filter", "explorer_sort", "explorer_search", "explorer_preset", "complex_click")


def test_explorer_html_template_has_no_old_fsup_marker_and_has_ga4_events():
    assert " F</sup>" not in EXPLORER_HTML
    for ev in EVENT_NAMES:
        assert ev in EXPLORER_HTML
    assert "typeof gtag==='function'" in EXPLORER_HTML or "typeof gtag === 'function'" in EXPLORER_HTML
    assert "국토부 실거래 사실(F)" in EXPLORER_HTML


def test_write_out_emits_the_same_explorer_html_to_disk(tmp_path):
    ds = {"complexes": [], "count": 0, "data_asof": "2026-09-04", "generated": "2026-09-05",
          "license": "CC-BY-NC-4.0", "disclaimer": "", "takedown": "", "sources": []}
    write_out(ds, str(tmp_path))
    written = (tmp_path / "explorer.html").read_text(encoding="utf-8")
    assert " F</sup>" not in written
    for ev in EVENT_NAMES:
        assert ev in written
    assert "국토부 실거래 사실(F)" in written
