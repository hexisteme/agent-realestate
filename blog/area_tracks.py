"""59㎡·84㎡ 관측 표 — 기존 신원·평형 검증값만 선택하며 새 시세를 계산하지 않는다."""
from __future__ import annotations

from collections import Counter
import math

from blog.build_explorer import SPREAD_MIN_N


def area_track_exclusion(row: dict) -> str | None:
    """듀얼 트랙의 제외 사유. spread_flag는 add_area_spread의 신원 판정 계약이다."""
    if row.get("product_type") != "아파트":
        return "not_apartment"
    if not row.get("gu") or not row.get("name"):
        return "missing_identity"
    if row.get("spread_flag") != "":
        return row.get("spread_flag") or "spread_unverified"
    units = row.get("kapt_area_units")
    if row.get("kapt_verified") is not True or not isinstance(units, dict) or not units:
        return "identity_unverified"
    unit_counts = list(units.values())
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in unit_counts):
        return "identity_unverified"
    if not any(v > 0 for v in unit_counts):
        return "identity_unverified"
    for key in ("n59", "n84"):
        n = row.get(key)
        if isinstance(n, bool) or not isinstance(n, int) or n < SPREAD_MIN_N:
            return "insufficient_samples"
    for key in ("med59_eok", "med84_eok"):
        value = row.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            return "missing_median"
    if row["med84_eok"] < row["med59_eok"]:
        return "total_inversion"
    return None


def select_area_tracks(ds: dict) -> dict:
    """검증된 동일 단지를 구·단지명 가나다순으로 선택하고 제외 수를 보존한다."""
    rows = []
    exclusions: Counter = Counter()
    identities = set()
    ordered = sorted(ds.get("complexes", []), key=lambda r: (
        str(r.get("gu") or ""), str(r.get("name") or ""), str(r.get("complex_no") or ""),
    ))
    for row in ordered:
        reason = area_track_exclusion(row)
        if reason:
            exclusions[reason] += 1
            continue
        identity = (str(row["gu"]), str(row.get("complex_no") or row["name"]))
        if identity in identities:
            exclusions["duplicate_identity"] += 1
            continue
        identities.add(identity)
        rows.append(row)
    return {"rows": rows, "eligible_count": len(rows),
            "excluded_count": sum(exclusions.values()), "exclusions": dict(exclusions)}
