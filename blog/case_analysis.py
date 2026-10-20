"""Deterministic descriptions of exact apartment transaction cohorts.

Amounts are KRW. Calendar windows do not imply source coverage is complete;
callers must separately inspect the collection coverage manifest.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path


def _date(value):
    if not isinstance(value, str):
        raise ValueError('deal_date/asof must be an ISO date string')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('invalid ISO date') from exc
    if parsed.isoformat() != value:
        raise ValueError('date must use YYYY-MM-DD')
    return parsed


def _cancel_date(value):
    # Same formats accepted by the existing MOLIT transaction parser.
    for pattern in ('%Y%m%d', '%y.%m.%d', '%Y.%m.%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, pattern).date()
        except (ValueError, TypeError):
            pass
    raise ValueError('invalid cancellation date')


def _amount(value, *, zero=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError('amount must be finite numeric KRW and positive (monthly rent may be zero)')
    return value


def _area(value):
    if isinstance(value, bool):
        raise ValueError('invalid area')
    try:
        area = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid area') from exc
    if not area.is_finite() or area <= 0:
        raise ValueError('invalid area')
    return area


def _month_shift(first, months):
    index = first.year * 12 + first.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def _stats(rows, start, end):
    selected = [(day, amount, kind) for day, amount, kind in rows
                if (start is None or day >= start) and (end is None or day <= end)]
    values = sorted(amount for _, amount, _ in selected)

    def quantile(p):
        if not values:
            return None
        position = (len(values) - 1) * p
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {'start': start.isoformat() if start else None,
            'end': end.isoformat() if end else None, 'n': len(values),
            'contract_date_count': len({day for day, _, _ in selected}),
            'median_krw': quantile(.5), 'q25_krw': quantile(.25),
            'q75_krw': quantile(.75), 'distribution_supported': len(values) >= 2}


def _summarize(rows, asof):
    current = asof.replace(day=1)
    last = current - timedelta(days=1)
    earliest = min((day for day, _, _ in rows), default=None)
    latest = max((day for day, _, _ in rows), default=None)
    periods = {
        'complete_12_months': _stats(rows, _month_shift(current, -12), last),
        'complete_6_months': _stats(rows, _month_shift(current, -6), last),
        'previous_6_months': _stats(rows, _month_shift(current, -12), _month_shift(current, -6) - timedelta(days=1)),
        'current_month': {**_stats(rows, current, asof), 'provisional': True},
    }
    years = range(earliest.year, latest.year + 1) if earliest else ()
    return {'all_history': _stats(rows, earliest, latest),
            'annual': {str(year): _stats(rows, date(year, 1, 1), min(date(year, 12, 31), asof)) for year in years},
            'periods': periods}


def analyze_case(sales, rents, *, apt_seq, area_m2, asof):
    """Return aggregate allowlisted fields without modifying input rows.

    Invalid target dates/amounts raise ValueError; exclusions count each row
    once, in identity/date/cancellation/monthly-rent order. Duplicate rows are
    retained because the source does not provide unique transaction IDs.
    """
    if not isinstance(apt_seq, str) or not apt_seq.strip():
        raise ValueError('apt_seq is required')
    target_area, cutoff = _area(area_m2), _date(asof)
    exclusions = {'sales': Counter(), 'rents': Counter()}
    accepted = {'sales': [], 'rents': []}
    missing_observation = {'sales': 0, 'rents': 0}
    for group, rows in (('sales', sales), ('rents', rents)):
        for row in rows:
            if row.get('apt_seq') != apt_seq or _area(row.get('area')) != target_area:
                exclusions[group]['identity_mismatch'] += 1
                continue
            observed = row.get('observed_date')
            if observed is None:
                missing_observation[group] += 1
            elif _date(observed) > cutoff:
                exclusions[group]['observed_after_asof'] += 1
                continue
            day = _date(row.get('deal_date'))
            monthly = _amount(row.get('monthly_rent_krw'), zero=True) if group == 'rents' else 0
            amount = _amount(row.get('price' if group == 'sales' else 'deposit_krw'), zero=monthly > 0)
            if day > cutoff:
                exclusions[group]['future'] += 1
                continue
            kind = None
            if group == 'sales':
                provenance = row.get('provenance') or {}
                cancel_day = provenance.get('cdeal_day')
                if cancel_day and _cancel_date(cancel_day) > cutoff:
                    exclusions[group]['future_revision'] += 1
                    continue
                if provenance.get('cdeal_type') or cancel_day:
                    exclusions[group]['cancelled'] += 1
                    continue
                if row.get('cancellation_checked') is not True:
                    exclusions[group]['cancellation_unknown'] += 1
                    continue
            elif monthly != 0:
                exclusions[group]['monthly_rent'] += 1
                continue
            else:
                kind = {'신규': 'new', '갱신': 'renew', 'new': 'new', 'renew': 'renew'}.get(row.get('contract_type'), 'unknown')
            accepted[group].append((day, amount, kind))
    jeonse = _summarize(accepted['rents'], cutoff)
    for stat in [jeonse['all_history'], *jeonse['annual'].values(), *jeonse['periods'].values()]:
        start = _date(stat['start']) if stat['start'] else None
        end = _date(stat['end']) if stat['end'] else None
        stat['by_contract_type'] = {
            kind: _stats([r for r in accepted['rents'] if r[2] == kind], start, end)
            for kind in ('new', 'renew', 'unknown')}
    return {'identity': {'apt_seq': apt_seq, 'area_m2': float(target_area)},
            'asof': cutoff.isoformat(), 'units': {'amount': 'KRW', 'area': 'm2'},
            'method': {'quantile': 'type7', 'duplicates': 'preserved',
                       'coverage': 'calendar windows; verify source coverage separately'},
            'exclusions': {k: dict(v) for k, v in exclusions.items()},
            'observation': {'missing_date': missing_observation,
                            'observation_dates_complete': not any(missing_observation.values()),
                            'historical_vintage_verified': False,
                            'meaning': 'observed_date is this source version retrieval date; '
                                       'date completeness is vacuously true for empty target input; '
                                       'historical source versions are not verified'},
            'sales': _summarize(accepted['sales'], cutoff), 'jeonse': jeonse}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('input', 'apt-seq', 'area', 'asof', 'out'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.input).read_text(encoding='utf-8'))
    result = analyze_case(source['sales'], source['rents'], apt_seq=args.apt_seq,
                          area_m2=args.area, asof=args.asof)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
