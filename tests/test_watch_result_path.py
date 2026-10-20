"""The leading watch answer points to its original, identity-confirmed result subset.

Text only: the Tistory editor rewrites href="#..." to /manage/newpost/#... (2026-10-20 live check)."""
from copy import deepcopy
from html import escape

import pytest

from blog import insight_series as ins
from test_insight_series import _molit, _row, _trade


def _result():
    rows = [_row('확인<&"단지', '1'), _row('미확인단지', '2', flag='identity_unverified'),
            _row('다른셀단지', '3')]
    trades = []
    for row in rows:
        for ym, prices in [('202602', [8, 8, 8, 10, 10]), ('202606', [8, 8, 10, 10, 10])]:
            if row['name'] == '다른셀단지' and ym == '202606':
                prices = [10] * 5
            trades.extend(_trade(row['name'], 84, price, ym, i + 1) for i, price in enumerate(prices))
    return ins.compute_budget_watch({'complexes': rows}, _molit(trades), budget_won=900_000_000)


def _body(res):
    return ins.render_watch_post(res, [], {}, '2026-10-13', '2026-10-15', '').body


HEADING = '[관측] 두 기간 모두 반복된 신원 확인 단지'
JUMP = '신원 확인 1곳 / 두 기간 모두 반복 전체 2곳 — 아래 「두 기간 모두 반복된 신원 확인 단지」 표'


def test_original_verified_subset_is_pointed_to_near_answer_and_preserves_results():
    res = _result()
    before = deepcopy(res)
    body = _body(res)
    assert JUMP in body and 'href="#' not in body and 'id="watch-results"' not in body
    start = body.rindex('<h3', 0, body.index(HEADING))
    end = body.index('<h3', start + 1)
    section = body[start:end]
    assert '미확인단지' not in section and '다른셀단지' not in section
    assert escape('확인<&"단지') in section
    assert '/complex/' not in section
    assert '5건 중 9억원 이하 3건(3계약일)' in section
    assert '5건 중 9억원 이하 2건(2계약일)' in section
    assert '1~4월' in section and '5~8월' in section
    assert '각 기간 전체 거래 5건 이상·서로 다른 계약일 2일 이상' in section
    assert '9억원 이하 서로 다른 계약일 2일 이상' in section
    assert body.index(JUMP) < body.index(ins.CAUTION_WATCH) < start
    assert start < body.index('[관측] 설정을 바꾸면')
    assert res == before


@pytest.mark.parametrize('identity', ['미확인', '확인 ', '', None])
def test_empty_confirmed_subset_has_no_dead_jump(identity):
    res = _result()
    for c in res['cells']['2+|2+']:
        if c['identity'] == '확인':
            c['identity'] = identity
    body = _body(res)
    assert '아래 「' not in body and 'href="#' not in body
    assert '신원 확인 0곳 / 두 기간 모두 반복 전체 2곳' in body
    assert '신원이 확인된 단지가 없어 결과목록을 표시하지 않습니다' in body


def test_result_escapes_gu_and_uses_dynamic_sample_and_repeat_parameters():
    res = _result()
    c = next(c for c in res['cells']['2+|2+'] if c['identity'] == '확인')
    c['gu'] = '구<&"'
    res['params'].update(min_n=7, min_days=3, repeat_days=4)
    body = _body(res)
    assert escape('구<&"') in body
    assert '각 기간 전체 거래 7건 이상·서로 다른 계약일 3일 이상' in body
    assert '9억원 이하 서로 다른 계약일 4일 이상' in body


def test_empty_original_cell_has_no_jump_or_table():
    res = _result()
    res['cells']['2+|2+'] = []
    res['cross']['2+|2+'] = 0
    body = _body(res)
    assert '아래 「' not in body and 'href="#' not in body
    assert '신원 확인 0곳 / 두 기간 모두 반복 전체 0곳' in body
    assert '신원이 확인된 단지가 없어 결과목록을 표시하지 않습니다' in body


def test_result_list_survives_draft_contract_without_recomputing(tmp_path, monkeypatch):
    from blog.tistory_contract import parse_helper, write_helper

    res = _result()

    def unexpected_recompute(*args, **kwargs):
        pytest.fail('renderer must use the original cell without recomputing')

    monkeypatch.setattr(ins, 'compute_budget_watch', unexpected_recompute)
    payload = ins.render_watch_post(res, [], {}, '2026-10-13', '2026-10-15', '')
    path = tmp_path / 'watch.html'
    write_helper(str(path), payload, '2026-10-15')
    body = parse_helper(str(path))['body']
    assert body == payload.body
    assert JUMP in body and HEADING in body and 'href="#' not in body
