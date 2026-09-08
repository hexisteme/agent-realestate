"""Read-only periodic-post review packets; never call collectors or publication writers.

Run with ``python3 -m blog.review_periodic --help``. A future publication date
selects the comparison baseline, while every calculation retains observed dates.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

from blog import period_delta as pd
from blog.macro_cycles import build_macro_regime_section
from blog.snapshots import list_snapshot_dates, molit_archive_path_on
from blog.wording_guard import assert_wording_ok

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_STYLE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.I | re.S)
_OFFLINE = ('<meta name="robots" content="noindex,nofollow">'
            '<meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'script-src \'none\'; connect-src \'none\'; form-action \'none\'; base-uri \'none\'">')


class ReviewError(ValueError):
    """Controlled diagnostic; safe to print without input contents."""


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _output_path(path: str) -> Path:
    out, report = Path(path).resolve(), PROJECT_ROOT / "report"
    if (not out.is_relative_to(report) or out == report
            or out.is_relative_to((report / "blog").resolve())):
        raise ReviewError("--out must be a new directory under project report/, outside report/blog")
    if out.exists():
        raise ReviewError("--out already exists; choose a new review directory")
    return out


def _reject_constant(value: str):
    raise ReviewError("non-finite JSON number")


def _read(path: Path, inputs: dict) -> dict:
    path = path.resolve()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if str(path) in inputs and inputs[str(path)]["sha256"] != digest:
        raise ReviewError("input changed between reads; rerun against stable files")
    inputs.setdefault(str(path), {"path": str(path), "sha256": digest})
    data = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw,
                      parse_constant=_reject_constant)
    if not isinstance(data, dict):
        raise ReviewError("input must be a JSON object")
    return data


def _dataset_dates(ds: dict, ceiling: date) -> tuple[date, date]:
    generated, asof = date.fromisoformat(ds["generated"]), date.fromisoformat(ds["data_asof"])
    if asof > generated or generated > ceiling or not isinstance(ds["complexes"], list):
        raise ReviewError("dataset has future/inconsistent dates or invalid complexes")
    return generated, asof


def _visible(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", _STYLE.sub("", _SCRIPT.sub("", text))))


def _validate_post(post: dict, delta: dict) -> None:
    required = [pd.be.DISCLAIMER, f'짝지은 {delta["n_pairs"]}단지',
                f'양쪽 집계 게이트 통과 {delta["n_valid"]}단지', "밴드 이동으로 구성이 달라짐"]
    if delta["added"] or delta["removed"]:
        required += ["구성이 달라 기준과 비교하지 않음"]
    for key in ("html", "tistory_html"):
        body, text = post[key], _visible(post[key])
        assert_wording_ok(body, f"review_periodic:{key}")
        if re.search(r"(?<![A-Za-z])(?:None|NaN|Infinity)(?![A-Za-z])", text) or any(x not in text for x in required):
            raise ReviewError(f"{key}: invalid numeric text or missing sample/disclosure")
        mc = delta.get("macro_context")
        if mc and (key == "html" or pd.MACRO_SECTION_TISTORY):
            if not mc.get("sources") or not mc.get("note_line"):
                raise ReviewError("macro section has no sources or interpretation limit")
            if mc["note_line"] not in text:
                raise ReviewError(f"{key}: macro interpretation limit lost")
            for source in mc["sources"]:
                if (html.escape(source["url"]) not in body
                        or any(source[k] not in text for k in ("label", "source", "date"))):
                    raise ReviewError(f"{key}: macro source lost")
    if len(post["tistory_html"].encode("utf-8")) > pd.TISTORY_BUDGET:
        raise ReviewError("tistory body exceeds byte budget")


def _review_html(body: str, banner: str) -> str:
    body = _SCRIPT.sub("", body)
    if not re.search(r"<head\b", body, re.I):
        body = f'<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body>{body}</body></html>'
    body = re.sub(r"(<head\b[^>]*>)", lambda m: m[0] + _OFFLINE, body, count=1, flags=re.I)
    return re.sub(r"(<body\b[^>]*>)", lambda m: m[0] + banner, body, count=1, flags=re.I)


def review_periodic(*, today: str, dataset: str, snapshots: str, out: str,
                    macro_snapshot: str | None = None) -> dict:
    """Create an isolated review packet. READY_FOR_REVIEW never means approval."""
    destination, target = _output_path(out), date.fromisoformat(today)
    if target.weekday() != 6:
        raise ReviewError("--today must be a scheduled Sunday (environment overrides are ignored)")
    kind = "monthly" if pd.is_last_sunday(today) else "weekly"
    inputs, warnings = {}, []
    ds = _read(Path(dataset), inputs)
    generated, asof = _dataset_dates(ds, min(target, _today()))
    actual = generated.isoformat()
    state = "READY_FOR_REVIEW" if target == generated else "PREVIEW_ONLY"
    snap_dir = Path(snapshots).resolve()
    if destination.is_relative_to(snap_dir):
        raise ReviewError("--out must be separate from input snapshots")

    def load_base(resolved: tuple) -> dict:
        dt, path = resolved
        loaded = _read(Path(path), inputs)
        observed, _ = _dataset_dates(loaded, generated)
        if dt != observed:
            raise ReviewError("snapshot filename date does not match dataset.generated")
        if dt == generated and loaded != ds:
            raise ReviewError("same-date dataset and snapshot contents differ")
        return loaded

    base = pd.resolve_base(kind, today, str(snap_dir))
    if not base or base[0] >= generated:
        raise ReviewError("no earlier observed baseline for requested publication date")
    base_ds = load_base(base)
    if state == "PREVIEW_ONLY":
        warnings.append("예정 발행일 자료가 없어 현재 실측 자료로 구성한 프리뷰. 예정일 변화·신고·발행 준비 판정에 사용할 수 없음.")
    if kind == "monthly" and base[0] != pd.previous_month_last_sunday(today):
        warnings.append(f"전월 마지막 일요일 자료 부재: {base[0]} 대체 기준. 실제 비교 기간은 {(generated - base[0]).days}일.")
    filings = None
    previous = molit_archive_path_on(base[0], 1, str(snap_dir / "molit"))
    current = molit_archive_path_on(generated, 0, str(snap_dir / "molit"))
    if previous and current:
        filings = pd.diff_filings(_read(Path(previous), inputs), _read(Path(current), inputs))
    else:
        warnings.append("기준/이번 실측일 MOLIT 아카이브가 모두 있지 않아 신고 델타 미검수.")
    weekly, weekly_rows, macro = None, None, None
    if kind == "monthly":
        wb = pd.resolve_base("weekly", today, str(snap_dir))
        if wb and base[0] != wb[0] < generated:
            weekly = pd.build_period_delta(ds, load_base(wb), "weekly", actual, wb[0])
        for dt in list_snapshot_dates(str(snap_dir)):
            if dt.weekday() == 6 and base[0] < dt <= generated:
                load_base((dt, snap_dir / f"dataset-{dt}.json"))
        weekly_rows = pd.weekly_rows_between(base[0], actual, str(snap_dir))
        if not weekly_rows or not weekly:
            warnings.append("예정일 주차별/이번 주 자료 일부 부재. 존재하는 일요일 스냅샷만 사용.")
    if macro_snapshot:
        snap = _read(Path(macro_snapshot), inputs)
        if date.fromisoformat(snap["asof"]) > generated:
            raise ReviewError("macro snapshot is later than the observed dataset")
        if kind == "monthly":
            macro = build_macro_regime_section(snap, actual)
    if kind == "monthly" and not macro:
        warnings.append("사용 가능한 거시 절이 없어 월간 거시 출처·해석 제한 검수는 미완료.")
    delta = pd.build_period_delta(ds, base_ds, kind, actual, base[0], filings=filings,
                                  weekly=weekly, weekly_rows=weekly_rows, macro_section=macro)
    post = pd.render_period_post(delta)
    _validate_post(post, delta)
    for source in inputs.values():
        if hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ReviewError("input changed during review; rerun against stable files")
    review = {"status": state, "human_review": "pending", "kind": kind, "target_date": today,
              "generated": ds["generated"], "data_asof": ds["data_asof"], "base_date": str(base[0]),
              "n_cur": delta["n_cur"], "n_pairs": delta["n_pairs"], "n_valid": delta["n_valid"],
              "tistory_bytes": len(post["tistory_html"].encode("utf-8")), "tistory_level": post["tistory_level"],
              "inputs": list(inputs.values()), "snapshots_directory": str(snap_dir), "warnings": warnings}
    banner = (f'<aside style="border:4px solid #b45309;padding:20px;background:#fff7ed;color:#111">'
              f'<strong>검수본 · {state} · 발행 승인 없음</strong><p>예정 발행일 {today} · '
              f'실제 데이터 생성일 {generated} · 실거래 기준일 {asof} · 비교 기준 {base[0]}</p>'
              '<p>본문 날짜·변화량은 실제 보유 자료 기준. 향후 회차 자료를 생성하지 않았습니다.</p></aside>')
    packet = {"review": review, "delta": delta}
    report = (f'# 결산 검수 패킷 — {today}\n\n상태: **{state}**. 자동 검증 통과, 사람 검토 대기(승인 아님).\n\n'
              f'실제 생성일 {generated} / 실거래 기준일 {asof} / 기준 {base[0]}.\n\n'
              f'현재 {delta["n_cur"]}단지 · 짝 {delta["n_pairs"]} · 계산 {delta["n_valid"]}. '
              f'티스토리 본문 {review["tistory_bytes"]}/{pd.TISTORY_BUDGET} bytes · 축약 {post["tistory_level"]}.\n\n'
              '예산은 원래 본문 기준이며, 검수용 배너·오프라인 래퍼는 제외합니다.\n\n'
              + '\n'.join(f'- {w}' for w in warnings) + '\n\n입력(읽기 전후 SHA256 동일):\n\n'
              + '\n'.join(f'- `{s["path"]}` — `{s["sha256"]}`' for s in inputs.values()) + '\n')
    files = {"review.json": json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False),
             "review-claims.jsonl": ''.join(json.dumps(c, ensure_ascii=False, allow_nan=False) + '\n' for c in post["claims"]),
             "review-site.html": _review_html(post["html"], banner),
             "review-tistory.html": _review_html(post["tistory_html"], banner), "REVIEW.md": report}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".period-review-", dir=destination.parent) as staging:
        for name, contents in files.items():
            Path(staging, name).write_text(contents, encoding="utf-8")
        if destination.exists():
            raise ReviewError("--out appeared during review; refusing overwrite")
        os.rename(staging, destination)
    return {**review, "out": str(destination)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("today", "dataset", "snapshots", "out"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--macro-snapshot")
    args = parser.parse_args(argv)
    try:
        result = review_periodic(**vars(args))
    except ReviewError as exc:
        print(f"Review failed: {exc}. No approval or publication performed.")
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Review failed: unreadable/invalid input or render failure ({type(exc).__name__}). No approval or publication performed.")
        return 1
    print(f'{result["status"]}: {result["out"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
