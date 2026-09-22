"""Normalize living-context evidence without changing ranking inputs.

The explorer dataset historically carried school proximity, terrain, and review
fields directly on each complex row.  This module gives those values an
explicit freshness/provenance envelope.  It intentionally does not calculate a
combined score: the context is descriptive evidence only.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any


_REVIEW_BIAS_WARNING = (
    "커뮤니티 후기는 작성자 자기선택·소표본·시점 편향이 있으며, "
    "단지 전체의 대표 평가나 점수·순위 근거가 아닙니다."
)

_REVIEW_THEME_REPLACEMENTS = (
    ("매수 권유", "매수 의견"),
    ("KB시세", "민간 시세"),
    ("저평가", "가격 평가"),
    ("고평가", "가격 평가"),
    ("급등", "단기 가격 변동"),
    ("급락", "단기 가격 변동"),
    ("폭등", "가격 변동"),
    ("폭락", "가격 변동"),
    ("유망", "선호 의견"),
    ("추천", "선호 의견"),
    ("1위", "비교 우위 주장"),
    ("호가", "표시 가격"),
)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _gu_labels(value: Any) -> list[str]:
    """Return literal and common Seoul-gu labels, without fuzzy matching."""
    raw = str(value or "").strip()
    if not raw:
        return []
    labels = [raw]
    tail = raw.split()[-1]
    if tail not in labels:
        labels.append(tail)
    if not tail.endswith("구"):
        labels.append(f"{tail}구")
    return list(dict.fromkeys(labels))


def _identity_keys(row: Mapping[str, Any]) -> list[str]:
    name = str(row.get("name") or "").strip()
    if not name:
        return []
    return [f"[{gu}]{name}" for gu in _gu_labels(row.get("gu"))]


def _match_named(
    records: Mapping[str, Any],
    row: Mapping[str, Any],
    name_counts: Counter[str],
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Match exact ``[gu]name`` first, then a globally unique plain name."""
    for key in _identity_keys(row):
        value = records.get(key)
        if isinstance(value, Mapping):
            return value, key

    name = str(row.get("name") or "").strip()
    if name and name_counts[name] == 1:
        value = records.get(name)
        if isinstance(value, Mapping):
            return value, name
    return None, None


def _section_evidence(
    metadata: Mapping[str, Any],
    section: str,
    row: Mapping[str, Any],
    name_counts: Counter[str],
) -> Mapping[str, Any]:
    """Resolve per-row evidence in the supported metadata layouts.

    Accepted layouts are ``{section: {identity: evidence}}``,
    ``{identity: {section: evidence}}``, and
    ``{rows: {identity: {section: evidence}}}``.  A plain-name identity is used
    only when that name occurs once in the dataset, matching review behavior.
    """
    aliases = (section, "review") if section == "reviews" else (section,)

    for alias in aliases:
        section_map = metadata.get(alias)
        if isinstance(section_map, Mapping):
            evidence, _ = _match_named(section_map, row, name_counts)
            if evidence is not None:
                return evidence

    row_maps: list[Mapping[str, Any]] = []
    rows = metadata.get("rows")
    if isinstance(rows, Mapping):
        row_maps.append(rows)
    row_maps.append(metadata)
    for row_map in row_maps:
        row_evidence, _ = _match_named(row_map, row, name_counts)
        if row_evidence is None:
            continue
        for alias in aliases:
            evidence = row_evidence.get(alias)
            if isinstance(evidence, Mapping):
                return evidence
    return {}


def _provenance(evidence: Mapping[str, Any], fallback: Mapping[str, Any] | None = None) -> tuple[Any, Any, Any]:
    fallback = fallback or {}
    observed = (
        evidence.get("observed_date")
        or evidence.get("confirmed_date")
        or evidence.get("confirmed")
        or fallback.get("observed_date")
        or fallback.get("confirmed_date")
        or fallback.get("confirmed")
    )
    source_label = (
        evidence.get("source_label")
        or evidence.get("source")
        or evidence.get("_source")
        or fallback.get("source_label")
        or fallback.get("source")
        or fallback.get("_source")
    )
    source_url = (
        evidence.get("source_url")
        or evidence.get("url")
        or evidence.get("_url")
        or fallback.get("source_url")
        or fallback.get("url")
        or fallback.get("_url")
    )
    if isinstance(source_url, str):
        source_url = next((part.strip() for part in source_url.split(";") if part.strip()), None)
    return observed, source_label, source_url


def _status(
    *,
    has_value: bool,
    observed: Any,
    today: date,
    stale_after_days: int | None,
    missing_warning: str,
    legacy_warning: str,
    stale_warning: str,
) -> tuple[str, bool, str | None, str]:
    if not has_value:
        return "missing", False, None, missing_warning

    observed_text = _date_text(observed)
    observed_date = _as_date(observed)
    if observed_date is None:
        warning = legacy_warning
        if observed_text:
            warning = f"{legacy_warning} 날짜 형식도 검증되지 않았습니다: {observed_text}"
        return "legacy_unverified", False, observed_text, warning
    if observed_date > today:
        return (
            "legacy_unverified",
            False,
            observed_date.isoformat(),
            f"{legacy_warning} 미래 관측일은 현재 근거로 인정하지 않습니다.",
        )
    if stale_after_days is not None and (today - observed_date).days > stale_after_days:
        return "stale", True, observed_date.isoformat(), stale_warning
    return "current", False, observed_date.isoformat(), ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _neutral_review_themes(value: Any) -> list[str]:
    """Keep aggregated themes descriptive and inside the public wording gate."""
    result: list[str] = []
    for item in _string_list(value):
        neutral = item
        for source, replacement in _REVIEW_THEME_REPLACEMENTS:
            neutral = neutral.replace(source, replacement)
        if neutral not in result:
            result.append(neutral)
    return result[:4]


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _public_bias_warning(*values: Any) -> str:
    """Keep public bias disclosure short; internal collection notes stay private."""
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text and len(text) <= 160 and "\n" not in text and "\r" not in text:
            return text
    return _REVIEW_BIAS_WARNING


def _load_reviews(reviews_path: str | Path | None) -> dict[str, Any]:
    if reviews_path is None:
        return {}
    path = Path(reviews_path)
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("reviews JSON top level must be an object")
    return loaded


def attach_living_context(
    ds: Mapping[str, Any],
    reviews_path: str | Path | None,
    today: str | date,
    evidence_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copied dataset with explicit living context on every row.

    Scores, rank fields, and row order are preserved.  Review text bodies and
    ``review_score`` are never copied into the normalized object.
    """
    today_date = _as_date(today)
    if today_date is None:
        raise ValueError("today must be an ISO date or datetime.date")
    if not isinstance(ds, Mapping):
        raise TypeError("ds must be a mapping")

    result = copy.deepcopy(dict(ds))
    rows = result.get("complexes")
    if not isinstance(rows, list):
        return result

    name_counts: Counter[str] = Counter(
        str(row.get("name") or "").strip()
        for row in rows
        if isinstance(row, Mapping) and str(row.get("name") or "").strip()
    )
    reviews = _load_reviews(reviews_path)
    metadata = evidence_metadata if isinstance(evidence_metadata, Mapping) else {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        school_evidence = _section_evidence(metadata, "school", row, name_counts)
        school_observed, school_source, school_url = _provenance(school_evidence)
        school_has_value = any(
            row.get(key) not in (None, "")
            for key in ("nearest_elem_school", "academy_exam", "school_achievement", "tukmokgo_pct")
        )
        school_status, school_stale, school_date, school_warning = _status(
            has_value=school_has_value,
            observed=school_observed,
            today=today_date,
            stale_after_days=90,
            missing_warning="최근접 학교·학원 근접성 자료가 수집되지 않았습니다.",
            legacy_warning="항목별 관측일이 없는 기존 근접성 값이며 현재 정보로 간주하지 않습니다.",
            stale_warning="학교·학원 근접성 관측 후 90일을 초과해 재확인이 필요합니다.",
        )

        terrain_evidence = _section_evidence(metadata, "terrain", row, name_counts)
        terrain_observed, terrain_source, terrain_url = _provenance(terrain_evidence)
        terrain_status, terrain_stale, terrain_date, terrain_warning = _status(
            has_value=row.get("slope_pct") is not None,
            observed=terrain_observed,
            today=today_date,
            stale_after_days=None,
            missing_warning="SRTM30m 경사 근사 자료가 수집되지 않았습니다.",
            legacy_warning="항목별 관측일이 없는 기존 SRTM30m 경사 근사값이며 현재 측정으로 간주하지 않습니다.",
            stale_warning="",
        )

        review_record, review_match_key = _match_named(reviews, row, name_counts)
        review_evidence = _section_evidence(metadata, "reviews", row, name_counts)
        review_record = review_record or {}
        review_observed, review_source, review_url = _provenance(review_evidence, review_record)
        nested_themes = review_record.get("themes")
        if not isinstance(nested_themes, Mapping):
            nested_themes = {}
        themes_pos = _neutral_review_themes(review_record.get("themes_pos"))
        if not themes_pos:
            themes_pos = _neutral_review_themes(nested_themes.get("pos"))
        themes_caution = _neutral_review_themes(review_record.get("themes_caution"))
        if not themes_caution:
            themes_caution = _neutral_review_themes(nested_themes.get("caution"))
        n_seen = _count(review_record.get("n_seen", review_record.get("n", 0)))
        review_has_value = review_match_key is not None
        review_status, review_stale, review_date, review_warning = _status(
            has_value=review_has_value,
            observed=review_observed,
            today=today_date,
            stale_after_days=30,
            missing_warning="이 단지에 정확히 연결할 수 있는 커뮤니티 후기 표본이 없습니다.",
            legacy_warning="항목별 관측일이 없는 기존 후기 표본이며 현재 상태로 간주하지 않습니다.",
            stale_warning="커뮤니티 후기 관측 후 30일을 초과해 현재 상태로 해석하면 안 됩니다.",
        )
        bias_warning = _public_bias_warning(
            review_evidence.get("bias_warning"),
            metadata.get("review_bias_warning"),
        )

        row["living_context"] = {
            "school": {
                "status": school_status,
                "stale": school_stale,
                "nearest_elem_school": row.get("nearest_elem_school"),
                "academy_exam": row.get("academy_exam"),
                "school_achievement": row.get("school_achievement"),
                "tukmokgo_pct": row.get("tukmokgo_pct"),
                "scope_label": "최근접 학교·학원 근접성 및 기존 학업 지표(배정학교 아님)",
                "source_label": school_source,
                "source_url": school_url,
                "observed_date": school_date,
                "warning": school_warning,
            },
            "terrain": {
                "status": terrain_status,
                "stale": terrain_stale,
                "slope_pct": row.get("slope_pct"),
                "method_label": "SRTM30m 중심±150m 표고 기반 경사 근사(보행 경사 아님)",
                "source_label": terrain_source,
                "source_url": terrain_url,
                "observed_date": terrain_date,
                "warning": terrain_warning,
            },
            "reviews": {
                "status": review_status,
                "stale": review_stale,
                "n_seen": n_seen,
                "themes_pos": themes_pos,
                "themes_caution": themes_caution,
                "source_label": review_source,
                "source_url": review_url,
                "observed_date": review_date,
                "bias_warning": str(bias_warning),
                "warning": review_warning,
            },
        }

    return result
