"""기존 운영 산출물만 읽는 일간 점검. 수집·발행·마커 변경 없음.

종료코드: PASS=0, FAIL=1, NOT_DUE=2. 미래 날짜의 부재를 성공이나 실패로 오인하지 않는다.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _CardRows(HTMLParser):
    """같은 행의 개별 셀 텍스트를 비교해 값의 앞자리 추가도 검출한다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row, self.cell = None, None


def check_operations(run_date: str, source: Path, site: Path, *, expected_count: int | None = None,
                     m2_month: str | None = None, filing_base: str | None = None,
                     today: date | None = None) -> dict:
    target = date.fromisoformat(run_date)
    now = today or date.today()
    if m2_month and not re.fullmatch(r"\d{4}-\d{2}", m2_month):
        raise ValueError("M2 월은 YYYY-MM 형식이어야 합니다")
    expected_m2 = date.fromisoformat(m2_month + "-01").isoformat() if m2_month else None
    if filing_base:
        date.fromisoformat(filing_base)
    result = {"run_date": run_date, "checked_on": now.isoformat(), "status": "NOT_DUE", "checks": []}
    if target > now:
        return result

    def record(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append({"name": name, "passed": bool(ok), "detail": detail})

    def read_json(path: Path, name: str) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("object required")
        except (OSError, ValueError):
            record(name, False, "파일 없음 또는 JSON 객체 아님")
            return {}
        record(name, True)
        return value

    ds = read_json(source / "snapshots" / f"dataset-{run_date}.json", "dataset_snapshot")
    rows = ds.get("complexes")
    count = len(rows) if isinstance(rows, list) else None
    record("dataset_date", ds.get("generated") == run_date and ds.get("data_asof") == run_date)
    record("dataset_count", count is not None and count > 0 and ds.get("count") == count
           and (expected_count is None or count == expected_count), f"count={count}, expected={expected_count}")
    live = read_json(site / "dataset.json", "site_dataset")
    record("site_dataset_matches", bool(ds) and live == ds)

    macro_path = source / "snapshots" / "macro" / f"macro-{run_date}.json"
    macro = read_json(macro_path, "macro_snapshot")
    record("macro_date", macro.get("asof") == run_date)
    indicators = macro.get("indicators")
    errors = macro.get("errors")
    record("macro_errors", isinstance(errors, dict) and not errors)
    record("macro_count", isinstance(indicators, dict) and len(indicators) > 0
           and macro.get("n_ok") == len(indicators))
    m2 = indicators.get("kr_m2") if isinstance(indicators, dict) else None
    record("kr_m2_present", isinstance(m2, dict))
    if isinstance(m2, dict):
        value = m2.get("value")
        record("kr_m2_value", isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) and value > 0)
        observed = m2.get("date")
        try:
            valid_date = isinstance(observed, str) and date.fromisoformat(observed).isoformat() == observed
            valid_date = valid_date and observed.endswith("-01") and observed <= run_date
        except (TypeError, ValueError):
            valid_date = False
        record("kr_m2_month", valid_date and (not expected_m2 or observed == expected_m2),
               f"observed={observed if valid_date else 'INVALID'}, expected={expected_m2}")
        record("kr_m2_series", m2.get("source") == "한국은행 ECOS 161Y005/BBHS00"
               and m2.get("unit") == "십억원" and m2.get("freq") == "monthly")

    pages = {}
    for name in ("macro", "calc", "cycles"):
        try:
            pages[name] = (site / f"{name}.html").read_text(encoding="utf-8")
            record(f"{name}_page", bool(pages[name].strip()))
        except OSError:
            record(f"{name}_page", False, "파일 없음")
    combined = "\n".join(pages.values()) + json.dumps(macro, ensure_ascii=False)
    record("no_ecos_api_url", "ecos.bok.or.kr/api" not in combined.lower())
    key = os.environ.get("ECOS_API_KEY")
    result["key_scan"] = "CHECKED" if key else "NOT_CHECKED_KEY_NOT_LOADED"
    if key:
        record("no_ecos_key", key not in combined)
    card = None
    try:
        from blog.macro_context import build_macro_context
        ctx = build_macro_context(macro, run_date)
        card = next((c for c in (ctx or {}).get("cards", []) if c["code"] == "kr_m2"), None)
    except (KeyError, TypeError, ValueError, OverflowError):
        card = None
    # 운영 HTML의 같은 행에 라벨·값·날짜가 함께 있어야 한다.
    parsed = _CardRows()
    parsed.feed(pages.get("macro", ""))
    record("kr_m2_card", bool(card) and any(
        len(row) >= 2 and row[0] == card["label"]
        and row[1] == f'{card["value_txt"]} {card["date"]}' for row in parsed.rows))
    if filing_base:
        for stamp in (filing_base, run_date):
            try:
                with gzip.open(source / "snapshots" / "molit" / f"molit-{stamp}.json.gz", "rt", encoding="utf-8") as f:
                    archive = json.load(f)
                ok = isinstance(archive, dict) and any(isinstance(v, list) and v for k, v in archive.items() if k != "_done")
            except (OSError, ValueError, EOFError):
                ok = False
            record(f"molit_archive_{stamp}", ok)
        kept = source / "snapshots" / "dataset-2026-09-06.json"
        record("first_sunday_snapshot_retained", kept.is_file())
    result["status"] = "PASS" if all(c["passed"] for c in result["checks"]) else "FAIL"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--source", type=Path, default=ROOT / "report/blog")
    ap.add_argument("--site", type=Path, default=ROOT / "site")
    ap.add_argument("--expected-count", type=int)
    ap.add_argument("--m2-month")
    ap.add_argument("--filing-base")
    args = ap.parse_args()
    result = check_operations(args.date, args.source, args.site, expected_count=args.expected_count,
                              m2_month=args.m2_month, filing_base=args.filing_base)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "FAIL": 1, "NOT_DUE": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
