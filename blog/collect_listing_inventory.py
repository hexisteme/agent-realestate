"""Collect one complete Seoul listing-inventory observation.

This collector uses Naver's legal-dong complex list, not map bounding boxes.
Every row therefore belongs to the requested district before aggregation, and
the universe is independent of the published-complex pool, household count,
and MOLIT transaction samples.

Browser requests run asynchronously in bounded batches. A slow legal-dong
response can time out without freezing the Chrome page or the remaining 24
districts. Successful districts are checkpointed so an interrupted daily run
can resume without changing the observation method.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from agent_realestate.collectors.naver_region import SEOUL_GU, RegionComplex, parse_region
from blog.listing_inventory import METHOD_VERSION, build_inventory_snapshot, load_snapshot, save_snapshot

DEFAULT_READ_SCRIPT = "/Users/kimjonghyun/.codex/scripts/read-chrome-tab.sh"
DEFAULT_TAB_PATTERN = "new.land.naver.com"
NAVER_SEED_URL = "https://new.land.naver.com/"
NAVER_SEED_COMMAND = ("open", "-g", "-a", "Google Chrome", NAVER_SEED_URL)
NAVER_SEED_TIMEOUT_SECONDS = 10
NAVER_SEED_WAIT_SECONDS = 2.0
_NAVER_TAB_URL_RE = re.compile(r"<(https://new[.]land[.]naver[.]com/[^>]*)>")


def _inventory_js(cortar_no: str, slot: str = "__codex_listing_inventory") -> str:
    """Start one bounded asynchronous legal-dong collection in page memory."""
    if not cortar_no.isascii() or not cortar_no.isdigit():
        raise ValueError("cortar_no must contain ASCII digits only")
    gu_json = json.dumps(cortar_no)
    slot_json = json.dumps(slot)
    return f"""
(function(){{
  var GU={gu_json}, SLOT={slot_json}, BATCH_SIZE=6;
  window[SLOT]={{state:"pending"}};
  function failure(code){{
    var error=new Error(code); error.safeCode=code; throw error;
  }}
  function errorCode(error){{
    if(error&&error.name==="AbortError")return "REQUEST_TIMEOUT";
    if(error&&typeof error.safeCode==="string")return error.safeCode;
    return "REQUEST_FAILED";
  }}
  function getJson(url,timeoutMs){{
    var controller=new window.AbortController();
    var timer=window.setTimeout(function(){{controller.abort();}},timeoutMs);
    return window.fetch(url,{{credentials:"same-origin",signal:controller.signal}})
      .then(function(response){{
        if(!response.ok)failure("HTTP_"+response.status);
        return response.json();
      }})
      .then(function(value){{window.clearTimeout(timer);return value;}},
            function(error){{window.clearTimeout(timer);throw error;}});
  }}
  function fetchDong(dong,attempts){{
    var url="/api/regions/complexes?cortarNo="+encodeURIComponent(dong.cortarNo)+
      "&realEstateType=APT&order=rank";
    var timeoutMs=attempts>1?8000:20000;
    return getJson(url,timeoutMs).then(function(payload){{
      if(!payload||!Array.isArray(payload.complexList))failure("DONG_SCHEMA");
      return {{ok:true,rows:payload.complexList}};
    }},function(error){{
      if(attempts>1){{
        return new Promise(function(resolve){{window.setTimeout(resolve,400);}})
          .then(function(){{return fetchDong(dong,attempts-1);}});
      }}
      return {{ok:false,cortarNo:String(dong.cortarNo),code:errorCode(error)}};
    }});
  }}
  function fetchBatches(dongs,index,groups,failures){{
    if(index>=dongs.length)return Promise.resolve({{groups:groups,failures:failures}});
    var batch=dongs.slice(index,index+BATCH_SIZE);
    return Promise.all(batch.map(function(dong){{
      return fetchDong(dong,2);
    }})).then(function(results){{
      for(var i=0;i<results.length;i++){{
        if(results[i].ok)groups.push(results[i].rows);
        else failures.push(results[i]);
      }}
      return fetchBatches(dongs,index+BATCH_SIZE,groups,failures);
    }});
  }}
  getJson("/api/regions/list?cortarNo="+GU,15000).then(function(regions){{
    if(!regions||!Array.isArray(regions.regionList)||!regions.regionList.length)
      failure("REGION_LIST_SCHEMA");
    return fetchBatches(regions.regionList,0,[],[]);
  }}).then(function(result){{
    if(result.failures.length){{
      window[SLOT]={{state:"error",code:"DONG_REQUESTS_FAILED",
        failed:result.failures.map(function(item){{return item.cortarNo;}})}};
      return;
    }}
    var groups=result.groups;
    var seen={{}},out=[];
    for(var i=0;i<groups.length;i++){{
      var rows=groups[i];
      for(var j=0;j<rows.length;j++){{
        var c=rows[j],id=String(c.complexNo||"");
        if(!id||c.realEstateTypeCode!=="APT"||seen[id])continue;
        seen[id]=1;
        out.push({{complexNo:id,name:c.complexName,lat:c.latitude,lng:c.longitude,
          far:0,builtYm:c.useApproveYmd,households:c.totalHouseholdCount,dongs:c.totalBuildingCount,
          dealCount:c.dealCount,leaseCount:c.leaseCount,rentCount:c.rentCount,
          shortTermRentCount:c.shortTermRentCount}});
      }}
    }}
    out.sort(function(a,b){{return a.complexNo.localeCompare(b.complexNo);}});
    window[SLOT]={{state:"done",payload:out}};
  }}).catch(function(error){{
    var code="REQUEST_FAILED";
    code=errorCode(error);
    window[SLOT]={{state:"error",code:code}};
  }});
  return "STARTED";
}})()
""".strip()


def _poll_js(slot: str) -> str:
    key = json.dumps(slot)
    return f'(function(){{var value=window[{key}];return value?JSON.stringify(value):"MISSING";}})()'


def _cleanup_js(slot: str) -> str:
    key = json.dumps(slot)
    return f'(function(){{delete window[{key}];return "CLEARED";}})()'


def _run_expression(
    script: str,
    tab_pattern: str,
    expression: str,
    *,
    timeout: float,
) -> str | None:
    try:
        result = subprocess.run(
            [script, tab_pattern, expression],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _discover_tab_pattern_detail(script: str) -> tuple[str | None, bool]:
    """Return ``(pattern, listed)`` so a broken reader never seeds a tab."""
    try:
        result = subprocess.run(
            [script, "--list"], capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, False
    if result.returncode != 0:
        return None, False
    candidates = _NAVER_TAB_URL_RE.findall(result.stdout)
    for candidate in reversed(candidates):
        if _run_expression(script, candidate, "document.title", timeout=12):
            return candidate, True
    return None, True


def discover_tab_pattern(script: str = DEFAULT_READ_SCRIPT) -> str | None:
    """Choose the newest responsive Naver tab instead of the first stale tab."""
    return _discover_tab_pattern_detail(script)[0]


def seed_naver_tab(
    *,
    runner: Callable[..., object] | None = None,
    command: tuple[str, ...] = NAVER_SEED_COMMAND,
) -> bool:
    """Open one background Naver Land tab, without exposing command output.

    ``open -g`` avoids focusing Chrome under launchd.  This function has no
    retry: its caller may invoke it only after confirming that no responsive
    Naver tab is available.
    """
    run = runner or subprocess.run
    try:
        result = run(
            list(command), capture_output=True, text=True,
            timeout=NAVER_SEED_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return getattr(result, "returncode", 1) == 0


def prepare_naver_tab(
    script: str = DEFAULT_READ_SCRIPT,
    *,
    discover: Callable[[str], str | None] | None = None,
    seed: Callable[[], bool] | None = None,
    sleeper: Callable[[float], None] | None = None,
    wait_seconds: float = NAVER_SEED_WAIT_SECONDS,
) -> tuple[str | None, str | None]:
    """Find a live Naver tab, seeding at most one only when none is usable.

    The returned diagnostic is a fixed safe code.  No subprocess exception,
    stdout, stderr, URL query, or other command detail crosses this boundary.
    """
    if wait_seconds < 0:
        raise ValueError("wait_seconds must be nonnegative")
    if discover is None:
        def find() -> tuple[str | None, bool]:
            return _discover_tab_pattern_detail(script)
    else:
        def find() -> tuple[str | None, bool]:
            return discover(script), True

    tab_pattern, listed = find()
    if tab_pattern:
        return tab_pattern, None
    if not listed:
        return None, "NAVER_TAB_DISCOVERY_UNAVAILABLE"
    if not (seed or seed_naver_tab)():
        return None, "NAVER_TAB_SEED_UNAVAILABLE"
    try:
        (sleeper or time.sleep)(wait_seconds)
    except Exception:  # noqa: BLE001 - preserve the fixed diagnostic boundary
        return None, "NAVER_TAB_SEED_WAIT_UNAVAILABLE"
    tab_pattern, _listed = find()
    if tab_pattern:
        return tab_pattern, None
    return None, "NAVER_TAB_SEED_NOT_READY"


def scan_gu_inventory(
    gu: str,
    *,
    script: str = DEFAULT_READ_SCRIPT,
    tab_pattern: str = DEFAULT_TAB_PATTERN,
    timeout_seconds: float = 150,
    poll_interval: float = 0.5,
    max_attempts: int = 2,
) -> list[RegionComplex]:
    """Read all apartment complexes whose legal dong belongs to ``gu``."""
    cortar = SEOUL_GU.get(gu)
    if cortar is None:
        raise ValueError(f"unknown Seoul district: {gu}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    for attempt in range(1, max_attempts + 1):
        slot = f"__codex_listing_{uuid.uuid4().hex}"
        started = _run_expression(
            script, tab_pattern, _inventory_js(cortar, slot), timeout=20,
        )
        if started != "STARTED":
            print(f"[listing-inventory] {gu} 시도 {attempt}/{max_attempts}: BROWSER_START_UNAVAILABLE")
            continue
        deadline = time.monotonic() + timeout_seconds
        reason = "COLLECTION_DEADLINE"
        try:
            while time.monotonic() < deadline:
                raw = _run_expression(script, tab_pattern, _poll_js(slot), timeout=15)
                if raw in (None, "MISSING"):
                    reason = "BROWSER_POLL_UNAVAILABLE" if raw is None else "BROWSER_STATE_MISSING"
                    break
                try:
                    state = json.loads(raw)
                except json.JSONDecodeError:
                    reason = "BROWSER_STATE_INVALID"
                    break
                if state.get("state") == "done":
                    payload = state.get("payload")
                    if not isinstance(payload, list):
                        reason = "PAYLOAD_SCHEMA"
                        break
                    try:
                        return parse_region(payload, district=gu)
                    except (TypeError, ValueError):
                        reason = "PAYLOAD_VALUE"
                        break
                if state.get("state") == "error":
                    code = state.get("code")
                    reason = code if isinstance(code, str) and code.isascii() else "REQUEST_FAILED"
                    failed = state.get("failed")
                    if isinstance(failed, list):
                        safe_codes = [
                            str(value) for value in failed
                            if str(value).isascii() and str(value).isdigit()
                        ]
                        reason = f"{reason}_{len(failed)}"
                        if len(safe_codes) == len(failed):
                            reason = f"{reason}:{','.join(safe_codes)}"
                    break
                time.sleep(poll_interval)
        finally:
            _run_expression(script, tab_pattern, _cleanup_js(slot), timeout=10)
        print(f"[listing-inventory] {gu} 시도 {attempt}/{max_attempts}: {reason}")
    return []


def _row_payload(row: RegionComplex) -> dict:
    return {
        "complexNo": row.complex_no,
        "name": row.name,
        "far": row.far_pct,
        "builtYm": row.built_ym,
        "households": row.households,
        "dongs": row.dongs,
        "lat": row.lat,
        "lng": row.lng,
        "dealCount": row.deal_count,
        "leaseCount": row.lease_count,
        "rentCount": row.rent_count,
        "shortTermRentCount": row.short_term_rent_count,
    }


def _load_gu_checkpoint(path: Path, gu: str, observation_date: str) -> list[RegionComplex] | None:
    if not path.is_file():
        return None
    try:
        payload = load_snapshot(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        payload.get("method_version") != METHOD_VERSION
        or payload.get("observation_date") != observation_date
        or payload.get("district") != gu
        or not isinstance(payload.get("rows"), list)
    ):
        return None
    try:
        rows = parse_region(payload["rows"], district=gu)
    except (TypeError, ValueError):
        return None
    if not rows or any(
        value is None
        for row in rows
        for value in (row.deal_count, row.lease_count, row.rent_count, row.short_term_rent_count)
    ):
        return None
    return rows


def _save_gu_checkpoint(path: Path, gu: str, observation_date: str, rows: list[RegionComplex]) -> None:
    save_snapshot(path, {
        "method_version": METHOD_VERSION,
        "observation_date": observation_date,
        "district": gu,
        "rows": [_row_payload(row) for row in rows],
    })


def collect_inventory_snapshot(
    actual_gu_by_complex_no: dict[str, str] | None = None,
    *,
    observed_at: str,
    script: str = DEFAULT_READ_SCRIPT,
    tab_pattern: str = DEFAULT_TAB_PATTERN,
    districts: Iterable[str] = tuple(SEOUL_GU),
    scanner: Callable[..., list[RegionComplex]] = scan_gu_inventory,
    checkpoint_dir: str | Path | None = None,
) -> dict:
    """Collect all requested legal districts and completeness-gate the result."""
    expected = tuple(districts)
    rows_by_gu: dict[str, list[RegionComplex]] = {}
    success: dict[str, bool] = {}
    exact_map = dict(actual_gu_by_complex_no or {})
    preferred: dict[str, str] = {}
    observation_date = observed_at[:10]
    checkpoint_root = Path(checkpoint_dir) if checkpoint_dir is not None else None

    for gu in expected:
        checkpoint = checkpoint_root / f"{gu}.json" if checkpoint_root is not None else None
        rows = _load_gu_checkpoint(checkpoint, gu, observation_date) if checkpoint is not None else None
        resumed = rows is not None
        if rows is None:
            try:
                rows = scanner(gu, script=script, tab_pattern=tab_pattern)
            except Exception as exc:  # noqa: BLE001 - preserve one failed district as evidence
                print(f"[listing-inventory] {gu} 수집 실패: {type(exc).__name__}")
                rows = []
            if rows and checkpoint is not None:
                _save_gu_checkpoint(checkpoint, gu, observation_date, rows)
        rows_by_gu[gu] = rows
        success[gu] = bool(rows)
        for row in rows:
            previous = exact_map.get(row.complex_no)
            if previous is not None and previous != gu:
                raise ValueError(f"complex_no appears in two legal districts: {row.complex_no}")
            exact_map[row.complex_no] = gu
            preferred[row.complex_no] = gu
        suffix = " (재개)" if resumed else ""
        print(
            f"[listing-inventory] {gu}: {len(rows)}단지{suffix}"
            if rows else f"[listing-inventory] {gu}: 실패/빈 응답"
        )
    return build_inventory_snapshot(
        rows_by_gu,
        exact_map,
        observed_at=observed_at,
        districts_expected=expected,
        scan_success=success,
        preferred_scan_by_complex_no=preferred,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", required=True)
    parser.add_argument(
        "--snapshot-dir",
        default=os.environ.get("RE_LISTING_SNAPSHOT_DIR", "report/blog/snapshots/listings"),
    )
    parser.add_argument("--script", default=os.environ.get("RE_CHROME_READ_SCRIPT", DEFAULT_READ_SCRIPT))
    parser.add_argument("--tab-pattern", default=os.environ.get("RE_NAVER_TAB_PATTERN"))
    parser.add_argument("--district-map", help=argparse.SUPPRESS)  # v1/v2 compatibility; unused in v3
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not Path(args.script).is_file():
        print("[listing-inventory] Chrome 읽기 스크립트 없음 — 집계 중단")
        return 2

    tab_pattern = args.tab_pattern
    diagnostic = None
    if not tab_pattern:
        tab_pattern, diagnostic = prepare_naver_tab(args.script)
    if not tab_pattern:
        print(f"[listing-inventory] {diagnostic or 'NAVER_TAB_UNAVAILABLE'} — 집계 중단")
        return 2

    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    snapshot_dir = Path(args.snapshot_dir)
    checkpoint_dir = snapshot_dir / "raw" / args.today
    snapshot = collect_inventory_snapshot(
        observed_at=observed_at,
        script=args.script,
        tab_pattern=tab_pattern,
        checkpoint_dir=checkpoint_dir,
    )
    target = snapshot_dir / f"listing-inventory-{args.today}.json"
    save_snapshot(target, snapshot)
    if snapshot["complete"]:
        total = snapshot["total"]
        print(
            f"[listing-inventory] 완료: 전체 {total['total_article_count']:,} · "
            f"매매 {total['sale_article_count']:,} · 전세 {total['lease_article_count']:,} · "
            f"월세 {total['rent_article_count']:,} · 단기 {total['short_term_rent_article_count']:,} · "
            f"단지 {total['physical_complex_count']:,} → {target}"
        )
        return 0
    print(f"[listing-inventory] 불완전 관측 {len(snapshot['incomplete_reasons'])}건 → {target}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
