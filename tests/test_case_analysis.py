"""Regression tests for identity, evidence eligibility and hand-derived statistics."""
import copy
import json
import subprocess
import sys

import pytest


def sale(price=100, day='2026-09-01', **fields):
    return dict(apt_seq='11620-102', area=59.58, price=price, deal_date=day,
                cancellation_checked=True, provenance={'cdeal_type': '', 'cdeal_day': ''}, **fields)


def analyze(sales=(), rents=()):
    from blog.case_analysis import analyze_case
    return analyze_case(sales, rents, apt_seq='11620-102', area_m2=59.58, asof='2026-10-15')


def test_exact_identity_cancellation_future_and_duplicates():
    rows = [sale(), sale(), sale(200), sale(10000)]
    rows[2]['apt_seq'] = 'other'
    rows[3]['area'] = 84
    rows += [sale(999), sale(999), sale(999, '2026-10-16')]
    rows[4]['provenance']['cdeal_type'] = 'O'
    rows[5]['cancellation_checked'] = False
    result = analyze(rows)
    assert result['sales']['all_history']['n'] == 2
    assert result['sales']['all_history']['median_krw'] == 100
    assert result['exclusions']['sales'] == {'identity_mismatch': 2, 'cancelled': 1, 'cancellation_unknown': 1, 'future': 1}


def test_type7_quartiles_and_distinct_contract_dates():
    stat = analyze([sale(x) for x in [100, 200, 400, 800]])['sales']['all_history']
    assert (stat['median_krw'], stat['q25_krw'], stat['q75_krw']) == (300, 175, 500)
    assert stat['contract_date_count'] == 1


def test_complete_windows_and_current_provisional_are_separate():
    rows = [sale(100, '2025-09-30'), sale(200, '2025-10-01'), sale(300, '2026-03-31'),
            sale(400, '2026-04-01'), sale(500, '2026-09-30'), sale(600, '2026-10-01')]
    result = analyze(rows)['sales']
    periods = result['periods']
    assert periods['complete_12_months']['n'] == 4
    assert periods['complete_12_months']['start'] == '2025-10-01'
    assert periods['complete_12_months']['end'] == '2026-09-30'
    assert periods['complete_6_months']['median_krw'] == 450
    assert periods['previous_6_months']['median_krw'] == 250
    assert periods['current_month']['n'] == 1
    assert periods['current_month']['provisional'] is True
    assert result['annual']['2025']['n'] == 2


def test_jeonse_contract_groups_privacy_and_input_immutability():
    rents = [dict(apt_seq='11620-102', area=59.58, deal_date='2026-09-01',
                  deposit_krw=amount, monthly_rent_krw=monthly, contract_type=kind,
                  income='PRIVATE') for amount, monthly, kind in
             [(100, 0, '신규'), (200, 0, '갱신'), (300, 0, ''), (900, 1, '신규')]]
    before = copy.deepcopy(rents)
    result = analyze([], rents)
    assert rents == before
    assert result['jeonse']['all_history']['median_krw'] == 200
    assert {k: v['n'] for k, v in result['jeonse']['all_history']['by_contract_type'].items()} == {'new': 1, 'renew': 1, 'unknown': 1}
    assert result['exclusions']['rents'] == {'monthly_rent': 1}
    assert 'PRIVATE' not in json.dumps(result)
    assert result['sales']['all_history']['median_krw'] is None
    assert result['jeonse']['all_history']['by_contract_type']['new']['distribution_supported'] is False


@pytest.mark.parametrize('field,value', [('price', 0), ('price', True), ('price', float('nan')), ('price', '100'), ('deal_date', '2026-02-30')])
def test_invalid_target_sales_raise(field, value):
    row = sale()
    row[field] = value
    with pytest.raises(ValueError):
        analyze([row])


@pytest.mark.parametrize('field,value', [('deposit_krw', -1), ('monthly_rent_krw', -1), ('monthly_rent_krw', None)])
def test_invalid_target_rents_raise(field, value):
    row = dict(apt_seq='11620-102', area=59.58, deal_date='2026-09-01', deposit_krw=100, monthly_rent_krw=0)
    row[field] = value
    with pytest.raises(ValueError):
        analyze([], [row])


def test_cli_writes_only_requested_analysis(tmp_path):
    source, output = tmp_path / 'input.json', tmp_path / 'result.json'
    source.write_text(json.dumps({'sales': [sale()], 'rents': [], 'debt': 'PRIVATE'}))
    run = subprocess.run([sys.executable, '-m', 'blog.case_analysis', '--input', str(source),
        '--apt-seq', '11620-102', '--area', '59.58', '--asof', '2026-10-15', '--out', str(output)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text())['sales']['all_history']['n'] == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ['input.json', 'result.json']


def test_observation_cutoff_and_missing_observation_warning():
    rows = [sale(100), sale(900)]
    rows[1]['observed_date'] = '2026-10-16'
    result = analyze(rows)
    assert result['sales']['all_history']['median_krw'] == 100
    assert result['exclusions']['sales'] == {'observed_after_asof': 1}
    assert result['observation']['missing_date'] == {'sales': 1, 'rents': 0}
    assert result['observation']['observation_dates_complete'] is False


def test_zero_deposit_monthly_rent_is_excluded_but_zero_jeonse_rejected():
    row = dict(apt_seq='11620-102', area=59.58, deal_date='2026-09-01', deposit_krw=0, monthly_rent_krw=100)
    assert analyze([], [row])['exclusions']['rents'] == {'monthly_rent': 1}
    row['monthly_rent_krw'] = 0
    with pytest.raises(ValueError):
        analyze([], [row])


@pytest.mark.parametrize('cancel_day', ['20261016', '26.10.16', '2026.10.16', '2026-10-16'])
def test_future_cancellation_is_version_conflict_not_current_cancellation(cancel_day):
    row = sale(900)
    row['observed_date'] = '2026-10-15'
    row['provenance']['cdeal_day'] = cancel_day
    result = analyze([sale(100), row])
    assert result['exclusions']['sales'] == {'future_revision': 1}
    assert result['sales']['all_history']['median_krw'] == 100


def test_observation_dates_never_claim_verified_historical_vintage():
    row = sale()
    row['observed_date'] = '2026-10-15'
    result = analyze([row])
    assert result['observation']['observation_dates_complete'] is True
    assert result['observation']['historical_vintage_verified'] is False
    assert 'point_in_time_verified' not in result['observation']
    assert analyze()['observation']['observation_dates_complete'] is True


def test_jeonse_contract_statistics_follow_each_calendar_window():
    rents = [dict(apt_seq='11620-102', area=59.58, deal_date=day,
                  deposit_krw=amount, monthly_rent_krw=0, contract_type='신규')
             for day, amount in [('2020-01-01', 100000000), ('2026-09-01', 500000000)]]
    jeonse = analyze([], rents)['jeonse']
    assert jeonse['all_history']['by_contract_type']['new']['n'] == 2
    assert jeonse['all_history']['by_contract_type']['new']['median_krw'] == 300000000
    recent = jeonse['periods']['complete_12_months']['by_contract_type']['new']
    assert recent['n'] == 1
    assert recent['median_krw'] == 500000000
    assert recent['start'] == '2025-10-01'
    assert jeonse['periods']['previous_6_months']['by_contract_type']['new']['n'] == 0
    assert 'by_contract_type' not in jeonse
