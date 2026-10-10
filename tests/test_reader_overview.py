"""Reader summary must not reintroduce raw, stale, or invented observations."""
import copy
import importlib
import math
from html.parser import HTMLParser

import pytest


def plain(body):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(); self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
    parser = Text(); parser.feed(body)
    return ' '.join(parser.parts)


def context():
    indicators = {}
    for code, val, unit in [('kr_mortgage_new', 4.66, '%'), ('seoul_apt_sale', 102.3, '2026.06=100'),
                            ('seoul_apt_jeonse', 102.2, '2026.06=100'), ('seoul_apt_sales', 4589, '호')]:
        indicators[code] = dict(code=code, value=val, date='2026-08-01', unit=unit,
                                freq='monthly', series=[['2026-07-01', val-1], ['2026-08-01', val]])
    return {'visual_snapshot': {'asof': '2026-10-04', 'indicators': indicators}}


def render(ctx=None, support=None):
    mod = importlib.import_module('blog.reader_overview')
    return mod.build_reader_overview(ctx, support, today='2026-10-04', asof='2026-10-04')


def test_four_summaries_and_truthful_units_are_on_both_surfaces():
    result = render(context())
    for key in ('site_html', 'tistory_html'):
        body = result[key]
        for phrase in ('4.66', '102.3', '102.2', '4,589', '전국', '신규취급', '2026.06=100', '신고월', '개인', '미확인'):
            assert phrase in body
        assert body.count('data-reader-card=') == 4
        assert '<script' not in body
    assert len(result['tistory_html'].encode()) <= 4500


@pytest.mark.parametrize('bad', [None, {}, {'visual_snapshot': None}, {'visual_snapshot': {'asof': 'bad'}}])
def test_missing_context_keeps_four_explanations(bad):
    result = render(bad)
    assert result['site_html'].count('data-reader-card=') == 4
    assert '미수집' in result['tistory_html']
    assert '0%' not in plain(result['tistory_html'])


@pytest.mark.parametrize('field,bad', [('value', float('nan')), ('value', float('inf')), ('value', True),
                                     ('value', -1), ('date','2026-10-05'), ('date','2026-01-01'),
                                     ('date','20261001'), ('unit','원'), ('freq','event')])
def test_invalid_observation_not_promoted(field,bad):
    ctx=context();ctx['visual_snapshot']['indicators']['kr_mortgage_new'][field]=bad
    assert '4.66' not in render(ctx)['site_html']


@pytest.mark.parametrize('stamp',['2026-10-05','2026-09-01','not-a-date'])
def test_invalid_snapshot_age_hides_values(stamp):
    ctx=context();ctx['visual_snapshot']['asof']=stamp
    assert '4.66' not in render(ctx)['site_html']


def test_raw_series_guards_preserve_current_observation_and_input():
    ctx=context();ind=ctx['visual_snapshot']['indicators']['kr_mortgage_new']
    ind['series']=[['2026-11-01',99],['bad',98],['2026-07-01',math.inf],['2026-08-01',4.66]]
    before=copy.deepcopy(ctx)
    body=render(ctx)['site_html']
    assert '99%' not in plain(body) and '98%' not in plain(body)
    assert '4.66' in body
    assert ctx==before


def test_future_index_base_and_wrong_snapshot_timing_are_hidden():
    ctx=context();ctx['visual_snapshot']['indicators']['seoul_apt_sale']['unit']='2027.06=100'
    assert '102.3' not in render(ctx)['site_html']
    ctx=context();ctx['visual_snapshot']['asof']='2026-10-01'
    ctx['visual_snapshot']['indicators']['seoul_apt_sales']['date']='2026-10-02'
    assert '4,589' not in render(ctx)['site_html']


def test_forged_notice_does_not_become_eligible_or_expose_markup():
    snapshot={'target_date':'2026-10-04','notices':[{'title':'<script>unsafe()</script>', 'status':'접수중'}], 'sources':None}
    body=render(context(),snapshot)['site_html']
    assert 'unsafe' not in body and '신청 가능' not in body


@pytest.mark.parametrize('bad',[4589.5, 10**1000])
def test_trade_count_is_finite_integer_not_rounded_or_crashing(bad):
    ctx=context();ctx['visual_snapshot']['indicators']['seoul_apt_sales']['value']=bad
    body=render(ctx)['site_html']
    assert '4,590' not in body
    assert '<div class="reader-value">4,589' not in body


def test_independent_daily_overview_has_no_svg_font_scaling():
    body=render(context())['site_html']
    assert '<svg' not in body
    assert 'reader-row' in body and '막대는 0 기준' in body


def test_tistory_has_three_actual_months_as_static_zero_based_bars():
    ctx=context();ctx['visual_snapshot']['indicators']['seoul_apt_sales']['series']=[['2026-06-01',7742],['2026-07-01',6564],['2026-08-01',4589]]
    body=render(ctx)['tistory_html']
    assert body.count('data-reader-trade-month=')==3
    for text in ['2026-06','7,742','2026-07','6,564','2026-08','4,589','막대는 0 기준']:
        assert text in body
    assert 'font-size:26px' in body
    assert '<svg' not in body and '<script' not in body and '<style' not in body


def test_site_notice_pair_preserves_target_times_and_official_urls():
    from agent_realestate.collectors.housing_support import HousingNotice, empty_housing_support
    snap=empty_housing_support('2026-10-04')
    fields=dict(published_on='2026-10-01',scope='서울',apply_start='2026-10-13',apply_end='2026-10-15',
                apply_start_at='2026-10-13T10:00:00+09:00',apply_end_at='2026-10-15T17:00:00+09:00')
    snap['notices']=[HousingNotice(issuer='sh',title='신혼·신생아 매입임대주택Ⅱ',url='https://www.i-sh.co.kr/app/notice?id=1',**fields).as_dict('2026-10-04'),
                     HousingNotice(issuer='sh',title='신혼·신생아 매입임대주택Ⅰ',url='https://www.i-sh.co.kr/app/notice?id=2',**fields).as_dict('2026-10-04'),
                     HousingNotice(issuer='lh',title='서울지역 든든전세주택',url='https://apply.lh.or.kr/lhapply/notice?id=3',**fields).as_dict('2026-10-04')]
    result=render(context(),snap)
    body=result['site_html']
    assert body.count('data-reader-notice=')==2
    for text in ['신혼·신생아 매입임대주택Ⅱ','서울지역 든든전세주택','10/13 10:00','10/15 17:00','예정','한국시간']:
        assert text in body
    assert 'https://apply.lh.or.kr/lhapply/notice?id=3' in body
    assert '신혼·신생아 매입임대주택Ⅰ' not in body
    assert 'data-reader-notice=' not in result['tistory_html']


def test_visible_h1_is_short_while_search_title_contract_is_preserved():
    from blog.daily_digest import build_daily_digest
    digest=build_daily_digest({'complexes':[]},'2026-10-04','2026-10-04')
    body=digest['site_html']
    assert '<h1>서울 집값과 주거지원, 한눈에</h1>' in body
    assert f'<title>{digest["title"]}</title>' in body
    assert '2026-10-04' in body


def test_daily_integration_keeps_detail_and_summary_separate():
    from blog.daily_digest import build_daily_digest
    ds={'complexes': [], 'data_asof':'2026-10-04'}
    digest=build_daily_digest(ds,'2026-10-04','2026-10-04')
    body=digest['site_html']
    assert 'data-reader-overview' in body
    start=body.index('<details id="reader-details"')
    assert body.index('data-reader-overview') < start < body.index('오늘의 숫자')
    assert 'open' not in body[start:body.index('>',start)]
    assert '서울 25개 구' in digest['tistory_html']
    assert '공고별' in digest['tistory_html']
    assert '<script' not in digest['tistory_html']


@pytest.mark.parametrize('reverse', [False, True])
def test_conflicting_past_trade_observations_are_omitted_in_both_surfaces(reverse):
    ctx = context()
    rows = [['2026-06-01', 7742], ['2026-07-01', 6564],
            ['2026-07-01', 99999], ['2026-07-01', 6564], ['2026-08-01', 4589]]
    ctx['visual_snapshot']['indicators']['seoul_apt_sales']['series'] = rows[::-1] if reverse else rows
    for body in render(ctx).values():
        assert '99,999' not in plain(body)
        assert '6,564' not in plain(body)
        assert '7,742' in plain(body) and '4,589' in plain(body)


def test_identical_observations_deduplicate_and_current_metadata_wins():
    ctx = context()
    item = ctx['visual_snapshot']['indicators']['seoul_apt_sales']
    item['series'] = [['2026-06-01', 7742], ['2026-06-01', 7742],
                      ['2026-08-01', 99999], ['2026-08-01', 4589]]
    result = render(ctx)
    assert result['tistory_html'].count('data-reader-trade-month=') == 2
    for body in result.values():
        assert '99,999' not in plain(body)
        assert '4,589' in plain(body)
