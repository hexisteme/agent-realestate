"""Reading navigation must not damage market evidence or heading contracts."""
from bs4 import BeautifulSoup

from blog.tistory_reading import enhance_tistory_reading


def _toc_labels(document):
    """Tistory rewrites href="#..." to /manage/newpost/#... (2026-10-20 live), so the index is text only."""
    soup = BeautifulSoup(document, 'html.parser')
    toc = soup.select_one('[data-reading-toc="1"]')
    assert toc is not None
    assert toc.find('a') is None and 'href' not in str(toc)
    assert not soup.select('[id^="re-section-"]')
    labels = [span.get_text() for span in toc.select('span')]
    assert labels
    return labels


def test_actual_headings_replace_unlinked_outline_without_promising_absent_sections():
    document = ('<p data-outline style="color:#888">이 글의 순서: 없는 항목</p>'
                '<h3>가격대별 요약</h3><p>관측 3단지</p><h4>산식</h4>'
                '<h3>출처·고지</h3><p>국토부 RTMS, 추천 아님.</p>')
    result = enhance_tistory_reading(document)
    assert _toc_labels(result) == ['가격대별 요약', '출처·고지']
    soup = BeautifulSoup(result, 'html.parser')
    assert '없는 항목' not in result
    assert '<h4>산식</h4>' in result
    assert '<p>국토부 RTMS, 추천 아님.</p>' in result
    assert not soup.find(['h1', 'h2'])


def test_visual_answer_precedes_plain_toc_and_top_labels_become_sections():
    summary = '<p>서울 4,321단지 · 상승 20 · 하락 12<br>매매지수 100.4</p>'
    note = '<span style="color:#64748b">· 그래프와 같은 원자료</span>'
    document = ('<p><b>오늘의 관측 요약</b></p>' + summary
                + '<p><b>최근 6개월 값</b> ' + note + '</p>'
                + '<p><b>읽는 법</b></p><p>중위의 중위</p>'
                + '<p><b>구별 가격·추세</b> <span>· 가나다순</span></p>')
    result = enhance_tistory_reading(document)
    assert _toc_labels(result) == ['최근 6개월 값', '지표 읽는 법', '구별 가격·추세']
    soup = BeautifulSoup(result, 'html.parser')
    assert [heading.get_text() for heading in soup.select('h3')] == [
        '오늘의 관측 요약', '최근 6개월 값', '읽는 법', '구별 가격·추세',
    ]
    assert result.index(summary) < result.index('data-reading-toc')
    assert soup.find(['details', 'summary', 'nav', 'style']) is None
    assert note in result
    assert '<p>중위의 중위</p>' in result


def test_leading_freshness_remains_intact_after_answer_for_article_snippets():
    fresh = '<p data-daily-freshness><b>오늘 새로 확인</b><br>신고 +25건 · 단순 증감</p>'
    answer = '<p>표본 기준을 충족한 200단지에서 상승 20, 하락 12</p>'
    document = ('<p data-outline>이 글의 순서</p>' + fresh
                + '<p><b>오늘의 관측 요약</b></p>' + answer + '<h3>구별 관측</h3>')
    result = enhance_tistory_reading(document)
    assert result.index(answer) < result.index('data-reading-toc') < result.index(fresh)
    assert result.count(fresh) == 1
    assert result.count(answer) == 1


def test_freshness_reordering_requires_exact_adjacent_leading_blocks():
    lead = '<p>이 글의 필수 안내</p><p data-daily-freshness>새 신고 25건</p>'
    document = lead + '<p><b>오늘의 관측 요약</b></p><p>200단지 관측</p><h3>출처</h3>'
    result = enhance_tistory_reading(document)
    assert result.startswith(lead)


def test_heading_entities_are_not_html_injected_or_double_escaped():
    document = '<h3>전세 &amp; 매매 &lt;비교&gt; "조건"</h3><h3>출처</h3>'
    result = enhance_tistory_reading(document)
    assert _toc_labels(result)[0] == '전세 & 매매 <비교> "조건"'
    soup = BeautifulSoup(result, 'html.parser')
    assert soup.select_one('[data-reading-toc="1"] span').find('비교') is None
    assert soup.select_one('h3').get_text() == _toc_labels(result)[0]


def test_duplicate_headings_are_listed_once_and_existing_ids_are_untouched():
    document = ('<div id="existing"></div><h3 id="existing">중복</h3>'
                '<h3>중복</h3><h3 id="정책 안내">정책</h3>')
    result = enhance_tistory_reading(document)
    assert _toc_labels(result) == ['중복', '정책']
    assert result.count('id="existing"') == 2
    assert '<h3 id="정책 안내">정책</h3>' in result


def test_index_labels_drop_dated_notes_inside_headings():
    result = enhance_tistory_reading('<h3>가격대 <span>기준일 2026-10-14</span></h3><h3>출처</h3>')
    assert _toc_labels(result) == ['가격대', '출처']


def test_market_table_media_sources_disclosures_and_h4_are_byte_preserved():
    evidence = ('<h4>표본 기준</h4><table data-dashboard-table><tr><td>월</td><td>금리</td></tr>'
                '<tr><td>26.09</td><td>3.55%</td></tr></table>'
                '<p><img src="https://example.com/a.png" width="900" height="450" alt="금리 그래프"></p>'
                '<p><a href="https://example.com/source?a=1&amp;b=2">공식 원문</a> · 사람 검수 미실시</p>')
    result = enhance_tistory_reading('<h3>시장 관측</h3>' + evidence + '<h3>방법론</h3>')
    assert evidence in result
    assert enhance_tistory_reading(result) == result


def test_single_section_does_not_get_empty_or_redundant_navigation():
    document = '<h3>안내</h3><p>표본 없음</p>'
    assert enhance_tistory_reading(document) == document


def test_body_without_headings_stays_unchanged():
    document = '<p><b>시장 공지</b></p><p>새 관측 없음</p>'
    assert enhance_tistory_reading(document) == document


def test_index_stays_compact_for_a_long_article():
    document = ''.join(f'<h3>관측 항목 {number}</h3><p>사실 {number}</p>' for number in range(20))
    result = enhance_tistory_reading(document)
    assert len(_toc_labels(result)) == 8
    assert 'id=' not in result


def test_optional_index_degrades_before_evidence_when_byte_budget_is_tight():
    document = ''.join(f'<h3>관측 항목 {number}</h3><p>사실 {number} · 국토부 RTMS</p>' for number in range(8))
    full = enhance_tistory_reading(document)
    smaller = enhance_tistory_reading(document, byte_budget=len(full.encode('utf-8')) - 1)
    assert len(_toc_labels(smaller)) == 4
    assert len(smaller.encode('utf-8')) < len(full.encode('utf-8'))
    smallest = enhance_tistory_reading(document, byte_budget=len(smaller.encode('utf-8')) - 1)
    assert len(_toc_labels(smallest)) == 2
    bare = enhance_tistory_reading(document, byte_budget=len(document.encode('utf-8')))
    assert bare == document
    assert 'data-reading-toc' not in bare
    for number in range(8):
        assert f'<p>사실 {number} · 국토부 RTMS</p>' in smaller


def test_tight_index_prioritizes_districts_and_price_bands_and_keeps_existing_ids():
    document = ('<p><b>오늘의 관측 요약</b></p><p>공공 실거래 관측</p>'
                '<p><b>최근 6개월 값</b></p><p>3.55%</p><p><b>읽는 법</b></p><p>금리 지표</p>'
                '<p><b>구별 가격·추세</b></p><p>강남 20억</p>'
                '<h3 id="existing-price">가격대별 요약 <span>12개월 중위</span></h3><p>10억 미만</p>'
                '<h3 id="preserve-unlinked">집계 기준</h3><p>출처</p>')
    full = enhance_tistory_reading(document)
    assert len(_toc_labels(full)) == 5
    four = enhance_tistory_reading(document, byte_budget=len(full.encode('utf-8')) - 1)
    assert len(_toc_labels(four)) == 4 and '집계 기준' not in _toc_labels(four)
    two = enhance_tistory_reading(document, byte_budget=len(four.encode('utf-8')) - 1)
    assert _toc_labels(two) == ['구별 가격·추세', '가격대별 요약']
    soup = BeautifulSoup(two, 'html.parser')
    assert soup.find(id='existing-price').name == 'h3'
    assert soup.find(id='preserve-unlinked').get_text() == '집계 기준'


def test_original_over_budget_remains_available_for_callers_existing_compaction():
    document = '<h3>관측</h3><p>필수 근거</p><h3>고지</h3><p>사람 검수 미실시</p>'
    assert enhance_tistory_reading(document, byte_budget=1) == document


def test_non_visual_reader_cards_precede_navigation():
    document = ('<h3>시장 개요</h3><p>자료 기준일</p><h4>금리</h4>'
                '<p data-reader-card="rate">금리 3.55%</p><h4>지원</h4>'
                '<p data-reader-card="support">공식 공고 확인</p><h3>매수 검토 관측</h3>')
    result = enhance_tistory_reading(document)
    assert result.index('공식 공고 확인') < result.index('data-reading-toc')
    assert result.index('data-reading-toc') < result.index('>매수 검토 관측</h3>')


def test_non_heading_bold_labels_are_not_promoted():
    document = '<h3>관측</h3><p><b>상승률 2.5%</b> 설명</p><h3>출처</h3>'
    assert '<p><b>상승률 2.5%</b> 설명</p>' in enhance_tistory_reading(document)
