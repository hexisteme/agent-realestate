from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from blog.community_participation import (
    observation_issue_url,
    observation_list_url,
    render_complex_card,
    render_gu_panel,
)


def test_issue_url_keeps_district_and_complex_context_without_freeform_body():
    url = observation_issue_url("노원", "상계 주공&<3>")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.path.endswith("/issues/new")
    assert query["template"] == ["resident-observation.yml"]
    assert query["title"] == ["[생활관찰][노원] 상계 주공&<3>"]
    assert "body" not in query
    listing_query = parse_qs(urlsplit(observation_list_url("노원", "상계 주공&<3>")).query)["q"][0]
    assert listing_query == 'is:issue state:open in:title "[생활관찰][노원] 상계 주공&<3>"'


def test_panels_escape_hostile_names_and_separate_public_submission_notice():
    gu_html = render_gu_panel('노원"><script>boom()</script>')
    complex_html = render_complex_card("노원", '<img src=x onerror="boom()">')
    assert "<script>boom()</script>" not in gu_html
    assert '<img src=x onerror="boom()">' not in complex_html
    assert "외부 후기 표본·가격·점수·순위에 자동 합산되지 않습니다" in gu_html
    assert "검토 전에는 외부 후기 표본" in complex_html
    assert "community_report_click" in gu_html and "community_report_click" in complex_html
    assert "노원&quot;&gt;&lt;script&gt;boom()&lt;/script&gt; 관찰·댓글 보기" in gu_html
    assert "email" not in gu_html.lower() and "email" not in complex_html.lower()


def test_issue_form_requires_public_privacy_and_reuse_acknowledgement():
    form = Path(".github/ISSUE_TEMPLATE/resident-observation.yml").read_text(encoding="utf-8")
    assert "개인정보와 거주 인증자료를 포함하지 않았습니다" in form
    assert "사이트가 출처와 함께 요약해 표시할 수 있음에 동의합니다" in form
    assert form.count("required: true") >= 5
