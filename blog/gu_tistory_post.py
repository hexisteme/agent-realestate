"""구 단위 티스토리 포스트 — 하루 5구 회전 발행(2026-10-20 분할 실험).

왜: 일간 다이제스트는 25구를 1편에 담아 제목이 `서울 아파트 실거래가 — 비교 N단지 …` 였다
(`daily_digest._build_digest_title`). 제목·URL 에 구명도 단지명도 없어 "노원구 아파트 실거래"
같은 롱테일 쿼리에 걸릴 페이지가 없었다. 벤치마크(report/intel-korea-re-bloggers-20261020.md §6)
에서 상위 블로거가 공통으로 쓰는 축이 구·단지 키워드를 제목에 박은 개별 URL 이다.

중복 가드: 사이트측 구 포스트는 2026-09-06 P0 이후 주 1회만 쓴다
(`build_explorer.should_write_weekly_posts` — 25편/일이 실질 중복이라는 진단). 이 모듈은 사이트가
아니라 티스토리 표면만 늘리고, 평일(월~금) 하루 5구 × 5일 = 25구로 구마다 정확히 주 1회다. 주말은
쉰다. 사이트 구 페이지는 방법론·출처의 원문 링크로만 건다.

고유 사실(2026-10-21 P1): 첫 5편(/135~/139)은 표 밖 산문이 편끼리 5-gram Jaccard 0.95(숫자 마스킹)였고
"허브가 정본" 문장이 사이트 한 곳으로 유도했다 — Google 스팸 정책 doorway 예시와 같은 형태
(report/2026-10-21-blogger-benchmark-followup.md §1.4·§1.5). 표 앞에 그 구에서만 나오는 사실을 싣는다:
최근 1주 새로 나타난 매매 기록(주간결산과 같은 diff_filings)·서울 대비 비중·집계 대상 단지명·기준 최고가를
넘긴 기록, 7일 전 스냅샷과 짝지은 구 중위(compute_gu_deltas), 사이트 구 허브와 같은 사실 리드(fact_lead).
원본 아카이브나 직전 스냅샷이 없으면 그 줄만 빠진다.

본문 예산: 티스토리 본문은 일간 다이제스트와 같은 28,000B(`daily_digest._TISTORY_TEXT_BUDGET`, 30KB 중
대표이미지 메타 2KB 예약)를 넘지 않는다. 단지가 많은 구(실측 노원 110곳 134KB)는 표 행을 실거래 중위가
큰 순서로 줄이고 "전체 N곳은 허브"를 밝힌다. 한 곳도 못 담으면 발행하지 않고 실패한다(period_delta 와
같은 fail-closed).

되돌리기: `RE_GU_TISTORY_POSTS=1` 일 때만 동작한다. 환경변수를 지우면 일간 다이제스트 1편 체제로
그대로 돌아간다 — 기존 출력물은 건드리지 않는다.
"""
from __future__ import annotations

import gzip
import html
import json
import math
import os
from collections import Counter
from datetime import date, timedelta
from urllib.parse import quote

import blog.build_explorer as be
from blog.build_site import BASE_URL
from blog.fact_lead import build_fact_leads, render_lead_lines
from blog.period_delta import (MIN_PAIRS, _all_carry_contract_day, _contract_age_buckets, _filing_key, compute_gu_deltas,
                               diff_filings, filing_age_txt, molit_window_start)
from blog.snapshots import _MOLIT_RE, _date_of, molit_archive_path_on
from blog.tistory_contract import DraftPayload, write_helper
from blog.tistory_draft import TISTORY_TAGS, _MUT, build_tistory_section
from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok

GU_POSTS_PER_DAY = 5
TISTORY_BUDGET = 28000   # daily_digest._TISTORY_TEXT_BUDGET 와 같은 값 — 30KB 중 대표이미지 메타 2KB 예약
_WEEKDAYS = 5            # 월~금
_FLAG = "RE_GU_TISTORY_POSTS"
_TAG_LIMIT = 10
_WEEK_DAYS = 7           # 구마다 주 1회 — 최근 기록은 7일 전 원본 아카이브(±1일)와 비교한다
_RECORD_LIMIT = 8        # 새 기록 목록 상한(최근 계약월부터) — 나머지는 건수만
_HIGHS_LIMIT = 3         # 기준 최고가를 넘긴 기록 상한


def enabled() -> bool:
    """실험 플래그 — 명시적으로 "1" 일 때만 구 단위 발행을 한다."""
    return os.environ.get(_FLAG) == "1"


def _gu_label(gu: str) -> str:
    """태그·제목용 — 데이터의 구 이름에 '구' 접미사가 없으면 붙인다(daily_digest 와 같은 규칙)."""
    return gu if gu.endswith("구") else gu + "구"


def select_rotation_gus(gus: list[str], today: str, n: int = GU_POSTS_PER_DAY) -> list[str]:
    """요일로 결정되는 회전 — 월~금 5일에 전체 구를 정확히 1회씩 돌고, 주말은 [] 다.

    구를 정렬해 블록으로 자르고 요일(월=0)로 블록을 고른다. 구가 n×5 를 넘으면 블록 크기를 늘려
    평일 5일 안에 모두 돈다. 같은 날짜는 항상 같은 블록이다(재현성)."""
    weekday = date.fromisoformat(today).weekday()
    if weekday >= _WEEKDAYS:
        return []
    ordered = sorted(gus)
    size = max(n, math.ceil(len(ordered) / _WEEKDAYS))
    return ordered[weekday * size:(weekday + 1) * size]


def _lead_complex(rows: list[dict]) -> dict:
    """제목에 세울 단지 — 실거래 중위가 가장 큰 단지. 서열 표현은 쓰지 않고 이름만 쓴다."""
    priced = [r for r in rows if r.get("molit_recent_eok") is not None]
    pool = priced or rows
    return max(pool, key=lambda r: (r.get("molit_recent_eok") or 0, r["name"]))


def _build_title(gu: str, rows: list[dict], today: str, asof: str) -> str:
    lead = _lead_complex(rows)
    head = f'{lead["name"]} 등 {len(rows)}곳' if len(rows) > 1 else lead["name"]
    stamp = f'국토부 {asof} 기준' + ('' if asof == today else f' ({today})')
    return f'{_gu_label(gu)} 아파트 실거래가 — {head}｜{stamp}'


def _build_tags(gu: str, rows: list[dict]) -> str:
    label = _gu_label(gu)
    tags = [f"{label}아파트", f"{label}실거래가"]
    tags += [t for t in TISTORY_TAGS.split(",") if t]
    for r in sorted(rows, key=lambda x: -(x.get("molit_recent_eok") or 0))[:3]:
        tags.append(r["name"])
    return ",".join(list(dict.fromkeys(tags))[:_TAG_LIMIT])


def _window(day: date) -> tuple[str, str]:
    """그날 수집기가 담는 계약월 범위('YYYYMM' 처음, 끝) — 당월 제외 직전 12개 완결월(be._month_windows)."""
    recent, prior = be._month_windows(day.isoformat())
    return min(recent | prior), max(recent | prior)


def _seen_archive(base: date, archive_dir: str) -> dict:
    """기준일까지 1주(base−6 … base) MOLIT 아카이브의 구별 multiset 합집합 — 같은 기록은 일자별 개수의 최대만큼.
    국토부 API 가 하루 이틀 일부 행을 빠뜨렸다 되돌린 경우(2026-10-15·16 송파 2026-09: 292→127→132→305건)
    그 기록을 '새로 나타난' 것으로 세지 않고 최고가 비교 기준에도 넣는다. 그 주 안에 빠졌다가 같은 키로
    다시 들어온 기록도 새 기록에서 빠진다(건수를 덜 세는 쪽 오차).
    기준일 수집 창 밖의 월(전달 아카이브에만 있는 13개월 전 월)은 뺀다 — '기준일 12개월 표본'과 맞춘다. 묶음 키는
    diff_filings 와 같은 규칙: 그 구의 모든 행에 계약일이 있을 때만 계약일까지(Codex R1 2건)."""
    start, end = _window(base)
    arcs = []
    for i in range(_WEEK_DAYS):
        p = molit_archive_path_on(base - timedelta(days=i), 0, archive_dir)
        if p:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                arcs.append(json.load(fh))
    seen = {}
    for lawd in sorted({k for a in arcs for k in a if k != "_done"}):
        days = [[r for r in a[lawd] if isinstance(r, dict) and start <= str(r.get("ym")) <= end]
                for a in arcs if isinstance(a.get(lawd), list)]
        with_day = _all_carry_contract_day(*days)
        cell: dict[tuple, list] = {}
        for recs in days:
            groups: dict[tuple, list] = {}
            for r in recs:
                groups.setdefault(_filing_key(r, with_day), []).append(r)
            for k, rs in groups.items():
                if len(rs) > len(cell.get(k, ())):
                    cell[k] = rs
        seen[lawd] = [r for rs in cell.values() for r in rs]
    return seen


def load_week_filings(today: str, molit_path: str | None, snap_dir: str) -> dict | None:
    """7일 전(±1일) MOLIT 원본 아카이브 대비 오늘 원본에 새로 나타난 기록 — 주간결산과 같은 diff_filings.
    기준은 그 아카이브 하루치가 아니라 기준일까지 1주 아카이브의 합집합(_seen_archive)이다. 오늘 원본 중 기준일 수집 창
    뒤의 월(월이 바뀌어 수집기가 처음 담은 직전 달)은 기준일 자료에 없던 달이라 비교에서 빼고 구별 건수만 남긴다
    (2026-10-04 주간결산 '새로 확인된 신고 2335건' 중 2239건이 이 경우).
    반환 {"base_date", "filings", "apt_names"(구 → 오늘 원본 단지명 목록)}. 아카이브·원본이 없거나 읽지 못하면 None
    (구 원고는 그 줄만 빼고 만든다 — 일간 발행 흐름을 막지 않는다)."""
    if not molit_path or not os.path.exists(molit_path):
        return None
    target = date.fromisoformat(today) - timedelta(days=_WEEK_DAYS)
    path = molit_archive_path_on(target, 1, f"{snap_dir}/molit")
    if not path:
        return None
    try:
        base = _date_of(path, _MOLIT_RE)
        prev = _seen_archive(base, f"{snap_dir}/molit")
        with open(molit_path, encoding="utf-8") as fh:
            cur = json.load(fh)
        end = _window(base)[1]
        lawd_gu = {v: k for k, v in be.GU_LAWD.items()}
        inside, entered, entered_yms = {}, Counter(), set()
        for lawd, recs in cur.items():
            if lawd == "_done" or not isinstance(recs, list):
                continue
            inside[lawd] = [r for r in recs if isinstance(r, dict) and str(r.get("ym")) <= end]
            late = [str(r.get("ym")) for r in recs if isinstance(r, dict) and str(r.get("ym")) > end]
            entered[lawd_gu.get(lawd, lawd)] += len(late)
            entered_yms.update(late)
        filings = diff_filings(prev, inside, window_start=molit_window_start(today))
        names = {lawd_gu.get(lawd, lawd): sorted({r["apt"] for r in recs if isinstance(r, dict) and r.get("apt")})
                 for lawd, recs in cur.items() if lawd != "_done" and isinstance(recs, list)}
    except Exception as e:                                   # noqa: BLE001 — 잘린 gzip(EOFError)·형식 이상도 이 줄만 뺀다(agy P1)
        print(f"[gu_tistory_post] 최근 1주 새 기록 생략: {type(e).__name__}")
        return None
    return {"base_date": base.isoformat(), "filings": filings, "apt_names": names,
            "entered": {gu: n for gu, n in entered.items() if n}, "entered_yms": sorted(entered_yms)}


def _eok_won(won: int) -> str:
    return f"{round(won / 1e8, 2):g}억"


def _ym_txt(ym) -> str:
    s = str(ym or "")
    return f"{s[:4]}-{s[4:6]}" if len(s) >= 6 else "—"


def _pool_names(rows: list[dict], apt_names: list[str]) -> dict[str, str]:
    """MOLIT 원본 단지명 → 표 표시명. 표와 같은 매칭 규칙(be.match_molit_names)이라 집계 대상 밖 단지는 빠진다."""
    idx = be.build_name_index(apt_names)
    out: dict[str, str] = {}
    for r in sorted(rows, key=lambda x: x["name"]):
        for raw in sorted(be.match_molit_names(r["name"], apt_names, idx)):
            out.setdefault(raw, r["name"])
    return out


def _filing_lines(gu: str, rows: list[dict], week: dict) -> list[str]:
    """최근 1주 새로 나타난 매매 기록 — 구 건수·서울 대비 비중·계약월 분포, 집계 대상 단지의 새 기록(계약월·전용·금액),
    기준 최고가를 넘긴 기록. 단지명은 표와 같은 집계 대상만 싣는다(세대수·안전제외 기준 유지)."""
    fil, base, label = week["filings"], week["base_date"], _gu_label(gu)
    mine = [r for r in fil["new"] if r.get("gu") == gu]
    total = fil["n_new"]
    skipped = f", 수집 누락 {fil['n_lawd_skipped']}개 구 제외" if fil.get("n_lawd_skipped") else ""
    n_late, yms = (week.get("entered") or {}).get(gu, 0), week.get("entered_yms") or []
    late = (f" {_ym_txt(yms[0])}{'~' + _ym_txt(yms[-1]) if len(yms) > 1 else ''} 계약 {n_late}건은 {base} 자료에 아직 없던 "
            f"달이라 셈에서 뺐습니다." if n_late else "")
    if not mine:
        return [f"{base} 이후 국토부 실거래 목록에 새로 나타난 {label} 아파트 매매 기록은 없습니다(서울 전체 {total}건{skipped})."
                + late]
    age = filing_age_txt({"new_by_age": _contract_age_buckets(mine, fil.get("ref_ym")), "ref_ym": fil.get("ref_ym")})
    pct = round(100 * len(mine) / total)
    lines = [f"{base} 이후 국토부 실거래 목록에 새로 나타난 {label} 아파트 매매 기록 {len(mine)}건{age} — "
             f"서울 전체 {total}건의 {f'{pct}%' if pct else '1% 미만'}{skipped}. "
             f"목록 반영 시점 기준이라 그 기간의 체결 건수가 아닙니다.{late}"]
    pool = _pool_names(rows, week["apt_names"].get(gu, []))
    recs = Counter((pool[r["apt"]], str(r.get("ym") or ""), r["area"], r["price"]) for r in mine if r.get("apt") in pool)
    items = sorted(recs.items(), key=lambda kv: (-int(kv[0][1] or 0) if kv[0][1].isdigit() else 0, kv[0][0], kv[0][2], kv[0][3]))
    if items:
        rest = sum(k for _, k in items[_RECORD_LIMIT:])
        lines.append("집계 대상 단지의 새 기록(계약월·전용·금액): "
                     + " · ".join(f"{n} {_ym_txt(ym)} {area:g}㎡ {_eok_won(price)}" + (f" ×{k}" if k > 1 else "")
                                  for (n, ym, area, price), k in items[:_RECORD_LIMIT])
                     + (f" 외 {rest}건" if rest else "") + ".")
    highs = sorted((h for h in fil.get("record_highs", []) if h.get("gu") == gu and h.get("apt") in pool),
                   key=lambda h: (pool[h["apt"]], h["area"], h["price"]))
    if highs:
        more = f" 외 {len(highs) - _HIGHS_LIMIT}건" if len(highs) > _HIGHS_LIMIT else ""
        lines.append(f"같은 단지·전용의 {base} 기준 12개월 표본 최고가를 넘긴 새 기록: "
                     + " · ".join(f'{pool[h["apt"]]} {h["area"]:g}㎡ {_eok_won(h["price"])}'
                                  f'(기준 최고 {_eok_won(h["prev_max"])}, n{h["n_prev"]})' for h in highs[:_HIGHS_LIMIT])
                     + more + ".")
    return lines


def _delta_line(gu: str, ds: dict, prev_ds: dict) -> str | None:
    """7일 전 스냅샷과 짝지은 단지(양쪽 집계 게이트 통과 — period_delta.valid_pairs)의 구 중위 변화. 짝이 MIN_PAIRS 미만이면 None."""
    row = next((x for x in compute_gu_deltas(ds, prev_ds) if x["gu"] == gu), None)
    if not row or row["n_valid"] < MIN_PAIRS or row["gu_median_delta_pct"] is None:
        return None
    base = prev_ds.get("generated") or prev_ds.get("data_asof") or "7일 전"
    pct = row["gu_median_delta_pct"]
    n_gu = sum(1 for r in ds["complexes"] if r.get("gu") == gu)
    return (f"{base} 스냅샷과 같은 단지·전용으로 짝지은 {row['n_valid']}단지(이 구 발행 {n_gu}단지 중) 12개월 중위의 중위 "
            f"{row['median_base']:g}억 → {row['median_cur']:g}억({'변화 없음' if pct == 0 else f'{pct:+.1f}%'}).")


def build_gu_facts(gu: str, ds: dict, prev_ds: dict | None, week: dict | None) -> dict[str, list[str]]:
    """구 1개의 고유 사실 — {"week": 최근 1주 줄, "leads": 사실 리드 줄}. 결정론(같은 입력 → 같은 줄).
    week 줄 = 새로 나타난 매매 기록(load_week_filings) + 7일 전 스냅샷 대비 짝 단지 구 중위.
    leads = 사이트 구 허브와 같은 fact_lead 사실 리드(서울 비교는 ds 전체가 필요해 구 행만으로는 못 만든다)."""
    rows = [r for r in ds["complexes"] if r.get("gu") == gu]
    week_lines = _filing_lines(gu, rows, week) if week else []
    line = _delta_line(gu, ds, prev_ds) if prev_ds else None
    if line:
        week_lines.append(line)
    for i, t in enumerate(week_lines):
        assert_lead_wording_ok(t, f"gu_tistory_post:week:{gu}:{i}")
    return {"week": week_lines, "leads": render_lead_lines(build_fact_leads(ds, "gu", gu, prev_ds=prev_ds))}


def _facts_html(gu: str, facts: dict | None) -> str:
    """표 앞 사실 블록 — 카카오 에디터 paste 에서 살아남는 p·b·br·span 만 쓴다(build_tistory_section 과 같은 제약)."""
    if not facts:
        return ""
    label, out = _gu_label(gu), ""
    if facts.get("week"):
        out += (f"<p><b>{label} 최근 1주 기록</b><br>"
                + "<br>".join(f"• {html.escape(t)}" for t in facts["week"]) + "</p>")
    if facts.get("leads"):
        out += (f'<p><b>{label} 단지 사실</b> <span style="{_MUT}">(국토부 12개월 실거래·공개 단지정보)</span><br>'
                + "<br>".join(f"• {html.escape(t)}" for t in facts["leads"]) + "</p>")
    return out


def _build_body(gu: str, rows: list[dict], today: str, asof: str, shown: int, facts: dict | None = None) -> str:
    hub = f'{BASE_URL}/gu/{quote(gu)}.html'
    label = _gu_label(gu)
    scope = (f'<p style="{_MUT}">표에는 {label} 전체 {len(rows)}곳 중 실거래 중위가 큰 {shown}곳을 담았습니다'
             f'(나머지는 출처의 사이트 {label} 페이지).</p>' if shown < len(rows) else '')
    method = (f'<hr><p style="{_MUT}"><b>방법론·출처</b><br>'
              f'• 실거래 = 국토교통부 RTMS 공공데이터(12개월 동일평형 중위), 데이터 기준일 {asof}. '
              f'세대수·연식·전용면적·유형은 공개정보입니다.<br>'
              f'• ᶠ=국토부 실거래 사실 · n=표본수 · P25–75=동일평형 실거래 분위수 · 평단가=실거래÷평형.<br>'
              f'• 자체 평가·점수·서열을 매기지 않습니다. 사설 시세는 게재하지 않습니다.<br>'
              f'• 원문·머신리더블 데이터: <a href="{hub}" target="_blank">사이트 {label} 페이지</a> · '
              f'<a href="{BASE_URL}/explorer.html" target="_blank">전체 탐색기</a> · '
              f'<a href="{BASE_URL}/methodology.html">방법론 전문</a> · 라이선스 CC-BY-NC-4.0.</p>'
              f'<p style="{_MUT}">⚖ {be.DISCLAIMER} {be._takedown()}</p>')
    ordered = sorted(rows, key=lambda r: (r.get("molit_recent_eok") is None, -(r.get("molit_recent_eok") or 0)))
    return _facts_html(gu, facts) + scope + build_tistory_section(gu, ordered[:shown]) + method


def _fit_body(gu: str, rows: list[dict], today: str, asof: str, facts: dict | None = None) -> str:
    """예산 안에 드는 가장 많은 행 수를 찾는다(행 수에 대해 본문 크기는 단조 증가 → 이분 탐색)."""
    def size(k: int) -> int:
        return len(_build_body(gu, rows, today, asof, k, facts).encode("utf-8"))

    if size(len(rows)) <= TISTORY_BUDGET:
        return _build_body(gu, rows, today, asof, len(rows), facts)
    lo, hi = 0, len(rows)          # size(lo) 는 예산 안(또는 lo=0), size(hi) 는 예산 밖
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if size(mid) <= TISTORY_BUDGET:
            lo = mid
        else:
            hi = mid
    if lo == 0:
        raise ValueError(f"[gu_tistory_post] {gu}: 단지 1곳도 티스토리 본문 예산 {TISTORY_BUDGET}B 에 못 듭니다")
    return _build_body(gu, rows, today, asof, lo, facts)


def build_gu_post(gu: str, rows: list[dict], today: str, asof: str, facts: dict | None = None) -> dict[str, str]:
    """구 1개의 티스토리 원고 — {"title","tags","body"}. facts = build_gu_facts 결과(없으면 사실 블록 없이).
    단지가 없으면 발행하지 않고 실패한다."""
    if not rows:
        raise ValueError(f"[gu_tistory_post] {gu}: 단지 0곳 — 발행할 내용이 없습니다")
    title = _build_title(gu, rows, today, asof)
    body = _fit_body(gu, rows, today, asof, facts)
    assert_wording_ok(title, f"gu_tistory_post:title:{gu}")
    assert_wording_ok(body, f"gu_tistory_post:body:{gu}")
    return {"title": title, "tags": _build_tags(gu, rows), "body": body}


def write_gu_posts(ds: dict, today: str, asof: str, outdir: str, n: int = GU_POSTS_PER_DAY,
                   prev_ds: dict | None = None, molit_path: str | None = None) -> list[str]:
    """회전에 걸린 구만 헬퍼로 쓴다. 플래그가 꺼져 있으면 아무것도 쓰지 않고 [] 를 돌려준다.
    prev_ds = 7일 전 dataset 스냅샷, molit_path = 오늘 MOLIT 원본(7일 전 아카이브는 {outdir}/snapshots/molit)."""
    if not enabled():
        return []
    by: dict[str, list[dict]] = {}
    for r in ds["complexes"]:
        by.setdefault(r["gu"], []).append(r)
    gus = select_rotation_gus(list(by), today, n)
    week = load_week_filings(today, molit_path, f"{outdir}/snapshots") if gus else None
    paths = []
    for gu in gus:
        post = build_gu_post(gu, by[gu], today, asof, facts=build_gu_facts(gu, ds, prev_ds, week))
        path = f"{outdir}/tistory/{today}-gu-{gu}-tistory-draft.html"
        paths.append(write_helper(path, DraftPayload(post["title"], post["tags"], post["body"]), today))
    return paths
