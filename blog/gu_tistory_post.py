"""구 단위 티스토리 포스트 — 하루 5구 회전 발행(2026-10-20 분할 실험).

왜: 일간 다이제스트는 25구를 1편에 담아 제목이 `서울 아파트 실거래가 — 비교 N단지 …` 였다
(`daily_digest._build_digest_title`). 제목·URL 에 구명도 단지명도 없어 "노원구 아파트 실거래"
같은 롱테일 쿼리에 걸릴 페이지가 없었다. 벤치마크(report/intel-korea-re-bloggers-20261020.md §6)
에서 상위 블로거가 공통으로 쓰는 축이 구·단지 키워드를 제목에 박은 개별 URL 이다.

중복 가드: 사이트측 구 포스트는 2026-09-06 P0 이후 주 1회만 쓴다
(`build_explorer.should_write_weekly_posts` — 25편/일이 실질 중복이라는 진단). 이 모듈은 사이트가
아니라 티스토리 표면만 늘리고, 평일(월~금) 하루 5구 × 5일 = 25구로 구마다 정확히 주 1회다. 주말은
쉰다. 본문은 사이트의 구 허브를 canonical 로 가리킨다.

본문 예산: 티스토리 본문은 일간 다이제스트와 같은 28,000B(`daily_digest._TISTORY_TEXT_BUDGET`, 30KB 중
대표이미지 메타 2KB 예약)를 넘지 않는다. 단지가 많은 구(실측 노원 110곳 134KB)는 표 행을 실거래 중위가
큰 순서로 줄이고 "전체 N곳은 허브"를 밝힌다. 한 곳도 못 담으면 발행하지 않고 실패한다(period_delta 와
같은 fail-closed).

되돌리기: `RE_GU_TISTORY_POSTS=1` 일 때만 동작한다. 환경변수를 지우면 일간 다이제스트 1편 체제로
그대로 돌아간다 — 기존 출력물은 건드리지 않는다.
"""
from __future__ import annotations

import math
import os
from datetime import date
from urllib.parse import quote

import blog.build_explorer as be
from blog.build_site import BASE_URL
from blog.tistory_contract import DraftPayload, write_helper
from blog.tistory_draft import TISTORY_TAGS, _MUT, build_tistory_section
from blog.wording_guard import assert_wording_ok

GU_POSTS_PER_DAY = 5
TISTORY_BUDGET = 28000   # daily_digest._TISTORY_TEXT_BUDGET 와 같은 값 — 30KB 중 대표이미지 메타 2KB 예약
_WEEKDAYS = 5            # 월~금
_FLAG = "RE_GU_TISTORY_POSTS"
_TAG_LIMIT = 10


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


def _build_body(gu: str, rows: list[dict], today: str, asof: str, shown: int) -> str:
    hub = f'{BASE_URL}/gu/{quote(gu)}.html'
    scope = (f'이 글의 표는 {_gu_label(gu)} 전체 {len(rows)}곳 중 실거래 중위가 큰 순서로 {shown}곳만 담았습니다. '
             if shown < len(rows) else '')
    canonical = (f'<p style="background:#f0f7ff;border-left:4px solid #0969da;padding:10px 14px;margin:12px 0">'
                 f'{scope}이 구의 전체 단지·필터는 <a href="{hub}" target="_blank">{_gu_label(gu)} 허브</a>가 정본입니다 '
                 f'(<a href="{BASE_URL}/explorer.html" target="_blank">전체 탐색기</a>).</p>')
    method = (f'<hr><p style="{_MUT}"><b>방법론·출처</b><br>'
              f'• 실거래 = 국토교통부 RTMS 공공데이터(12개월 동일평형 중위), 데이터 기준일 {asof}. '
              f'세대수·연식·전용면적·유형은 공개정보입니다.<br>'
              f'• ᶠ=국토부 실거래 사실 · n=표본수 · P25–75=동일평형 실거래 분위수 · 평단가=실거래÷평형.<br>'
              f'• 자체 평가·점수·서열을 매기지 않습니다. 사설 시세는 게재하지 않습니다.<br>'
              f'• 원문·머신리더블 데이터: <a href="{BASE_URL}/">{BASE_URL}/</a> · '
              f'<a href="{BASE_URL}/methodology.html">방법론 전문</a> · 라이선스 CC-BY-NC-4.0.</p>'
              f'<p style="{_MUT}">⚖ {be.DISCLAIMER} {be._takedown()}</p>')
    ordered = sorted(rows, key=lambda r: (r.get("molit_recent_eok") is None, -(r.get("molit_recent_eok") or 0)))
    return canonical + build_tistory_section(gu, ordered[:shown]) + method


def _fit_body(gu: str, rows: list[dict], today: str, asof: str) -> str:
    """예산 안에 드는 가장 많은 행 수를 찾는다(행 수에 대해 본문 크기는 단조 증가 → 이분 탐색)."""
    def size(k: int) -> int:
        return len(_build_body(gu, rows, today, asof, k).encode("utf-8"))

    if size(len(rows)) <= TISTORY_BUDGET:
        return _build_body(gu, rows, today, asof, len(rows))
    lo, hi = 0, len(rows)          # size(lo) 는 예산 안(또는 lo=0), size(hi) 는 예산 밖
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if size(mid) <= TISTORY_BUDGET:
            lo = mid
        else:
            hi = mid
    if lo == 0:
        raise ValueError(f"[gu_tistory_post] {gu}: 단지 1곳도 티스토리 본문 예산 {TISTORY_BUDGET}B 에 못 듭니다")
    return _build_body(gu, rows, today, asof, lo)


def build_gu_post(gu: str, rows: list[dict], today: str, asof: str) -> dict[str, str]:
    """구 1개의 티스토리 원고 — {"title","tags","body"}. 단지가 없으면 발행하지 않고 실패한다."""
    if not rows:
        raise ValueError(f"[gu_tistory_post] {gu}: 단지 0곳 — 발행할 내용이 없습니다")
    title = _build_title(gu, rows, today, asof)
    body = _fit_body(gu, rows, today, asof)
    assert_wording_ok(title, f"gu_tistory_post:title:{gu}")
    assert_wording_ok(body, f"gu_tistory_post:body:{gu}")
    return {"title": title, "tags": _build_tags(gu, rows), "body": body}


def write_gu_posts(ds: dict, today: str, asof: str, outdir: str,
                   n: int = GU_POSTS_PER_DAY) -> list[str]:
    """회전에 걸린 구만 헬퍼로 쓴다. 플래그가 꺼져 있으면 아무것도 쓰지 않고 [] 를 돌려준다."""
    if not enabled():
        return []
    by: dict[str, list[dict]] = {}
    for r in ds["complexes"]:
        by.setdefault(r["gu"], []).append(r)
    paths = []
    for gu in select_rotation_gus(list(by), today, n):
        post = build_gu_post(gu, by[gu], today, asof)
        path = f"{outdir}/tistory/{today}-gu-{gu}-tistory-draft.html"
        paths.append(write_helper(path, DraftPayload(post["title"], post["tags"], post["body"]), today))
    return paths
