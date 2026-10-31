"""짧은 주거지원 안내와 상세 페이지. 공고별 자격·신청일과 공통 확인 항목을 분리한다."""
from __future__ import annotations

import html
import os
import re
from dataclasses import fields
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from agent_realestate.collectors.housing_support import (
    GUIDANCE, MAX_SNAPSHOT_AGE_DAYS, OFFICIAL_SOURCES, VERIFIED_DATE, HousingNotice, is_seoul_relevant,
)

BRIEF_MAX_BYTES = 2200
_NOTICE_FIELDS = {field.name for field in fields(HousingNotice)}
_BASE_URL = "https://hexisteme.github.io/seoul-re-snapshot"


def _context(snapshot, today):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    try:
        raw_day = snapshot.get("target_date", "")
        if not isinstance(raw_day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_day): raise ValueError
        day = date.fromisoformat(raw_day)
    except (ValueError, TypeError):
        day = None
    if today is None:
        current = day or datetime.now(ZoneInfo("Asia/Seoul")).date()
    else:
        current = date.fromisoformat(today)
    age = (current - day).days if day else None
    fresh = age is not None and 0 <= age <= MAX_SNAPSHOT_AGE_DAYS
    asof = snapshot.get("collected_at")
    try:
        if not isinstance(asof, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00", asof): raise ValueError
        instant = datetime.fromisoformat(asof)
        if instant.date() != current or instant.utcoffset() != timedelta(hours=9): raise ValueError
    except ValueError:
        asof = None
    notices = []
    if fresh:
        raw_notices = snapshot.get("notices", [])
        for item in raw_notices if isinstance(raw_notices, list) else []:
            try:
                notice = HousingNotice(**{key: value for key, value in item.items() if key in _NOTICE_FIELDS})
                if notice.published_on <= current.isoformat() and is_seoul_relevant(notice.title):
                    notices.append(notice.as_dict(current.isoformat(), asof_datetime=asof))
            except (ValueError, TypeError, AttributeError):
                continue
    states = {}
    raw_sources = snapshot.get("sources", {})
    raw_sources = raw_sources if isinstance(raw_sources, dict) else {}
    for code in OFFICIAL_SOURCES:
        source = raw_sources.get(code, {})
        source = source if isinstance(source, dict) else {}
        states[code] = "확인" if fresh and source.get("state") == "확인" and source.get("fetched_on") == (day.isoformat() if day else "") else "미확인"
    return {"today": current.isoformat(), "day": day.isoformat() if day else "미확인", "stale": age is not None and age > MAX_SNAPSHOT_AGE_DAYS,
            "notices": notices, "states": states, "errors": snapshot.get("errors", {})}


def _e(value):
    return html.escape(str(value), quote=True)


def _format_application_period(item, reference_day):
    """현재 페이지 연도는 생략하고, 다른 해/해넘김은 양쪽 연도를 표시한다."""
    if item.get("apply_start_at") and item.get("apply_end_at"):
        start, end = item["apply_start_at"], item["apply_end_at"]
        full_year = start[:4] != reference_day[:4] or end[:4] != reference_day[:4]
        start_day = (start[:10] if full_year else start[5:10]).replace("-", "/")
        end_day = (end[:10] if full_year else end[5:10]).replace("-", "/")
        return f"{start_day} {start[11:16]} ~ {end_day} {end[11:16]} (한국시간)"
    start, end = item.get("apply_start"), item.get("apply_end")
    if start or end:
        full_year = any(value and value[:4] != reference_day[:4] for value in (start, end))
        start_day = (start if full_year else start[5:]).replace("-", "/") if start else "미확인"
        end_day = (end if full_year else end[5:]).replace("-", "/") if end else "미확인"
        return f"{start_day} ~ {end_day} (접수시각 미확인)"
    return "접수일 미확인"


def render_housing_support_brief(snapshot, *, today=None, detail_url=None, heading_level=3):
    """허용 태그 h2·h3(절 제목)/p/b/br/a만 사용; 짧은 원고는 상세 페이지와 공식 원문으로 이어진다.
    heading_level: Tistory 는 스킨 글 제목 h2 아래라 3, 사이트 일일 페이지는 h1 아래라 2."""
    ctx = _context(snapshot, today)
    detail_url = detail_url or os.environ.get("BLOG_BASE_URL", _BASE_URL).rstrip("/") + "/housing-support.html"
    heading = f'<h{heading_level}>청약·공공임대 안내</h{heading_level}><p>공고 확인 {_e(ctx["day"])}</p>'
    guidance = "<p>" + "<br>".join(f'<b>{_e(item["label"])}</b> {_e(item["brief"])}' for item in GUIDANCE) + "</p>"
    note = '<p>자격·소득/자산·거주지는 공고별 확인. 시작·마감일은 접수시각도 확인하세요.</p>'
    if ctx["stale"]:
        note = '<p>오래된 공고 스냅샷: 현재 접수 상태 미확인. 공식 공고에서 확인하세요.</p>' + note
    sources = " · ".join(f'<a href="{_e(source["list_url"])}">{_e(source["label"])} {ctx["states"][code]}</a>' for code, source in OFFICIAL_SOURCES.items())
    tail = f'<p>{sources}<br><a href="{_e(detail_url)}">공고별 일정·자격·공식 근거 보기</a></p>'
    status_rank = {"접수중": 0, "예정": 1, "미확인": 2, "마감": 3}
    ordered = sorted(ctx["notices"], key=lambda item: (status_rank[item["status"]], item.get("apply_start") or "9999-12-31", item["published_on"]))
    primary, others, issuers = [], [], set()
    for item in ordered:
        if item["issuer"] not in issuers:
            primary.append(item); issuers.add(item["issuer"])
        else:
            others.append(item)
    items = primary + others
    for limit, width in ((3, 36), (3, 24), (2, 32), (2, 24), (1, 24), (0, 24)):
        lines = []
        for item in items[:limit]:
            title = item["title"][:width] + ("…" if len(item["title"]) > width else "")
            lines.append(f'<a href="{_e(item["url"])}">{OFFICIAL_SOURCES[item["issuer"]]["label"]} {_e(title)}</a> · {_e(item["status"])} · {_e(_format_application_period(item, ctx["today"]))}')
        notices = "<p>" + "<br>".join(lines) + "</p>" if lines else "<p>실제 공고·접수 상태는 공식 원문에서 확인하세요.</p>"
        output = heading + guidance + note + notices + tail
        if len(output.encode("utf-8")) <= BRIEF_MAX_BYTES:
            return output
    return heading + guidance + note + tail


def render_housing_support_page(snapshot, *, today=None):
    ctx = _context(snapshot, today)
    warnings = []
    if ctx["stale"]: warnings.append("오래된 공고 스냅샷입니다. 현재 접수 상태는 미확인입니다.")
    if not ctx["notices"]: warnings.append("실제 모집공고를 확인하지 못했습니다. 아래 공식 공고 목록을 확인하세요.")
    introduction = (f'<p class="meta">공고 확인: {_e(ctx["day"])} · 상태 평가: {_e(ctx["today"])} · 공식 안내 경로 확인: {VERIFIED_DATE}</p>'
                    '<p>SH·LH는 공급기관의 공고, 청년안심주택은 서울시가 운영하는 역세권 청년·신혼 임대 공고 게시판, 청약홈은 한국부동산원이 운영하는 청약 접수·공고 시스템입니다.</p>'
                    '<p>공통 안내는 지원 자격 확정이 아닙니다. 공고별 연령·혼인기간·주택소유·소득·자산·거주지역·접수시각을 확인하세요.</p>')
    guidance = "".join(f'<article><h3>{_e(item["label"])}</h3><p>{_e(item["detail"])}</p><a href="{_e(item["url"])}">공식 안내 확인</a></article>' for item in GUIDANCE)
    notices = []
    for item in ctx["notices"]:
        period = _format_application_period(item, ctx["today"])
        if item.get("eligibility_brief"):
            eligibility = f'<p><b>공고별 대상</b> {_e(item["eligibility_brief"])}</p>'
        else:
            eligibility = '<p>공고별 자격·소득·자산·우선공급 조건은 원문 확인이 필요합니다.</p>'
        notices.append(f'<article><p class="tag">{OFFICIAL_SOURCES[item["issuer"]]["label"]} · {_e(item["scope"])} · {_e(item["status"])}</p>'
            f'<h3><a href="{_e(item["url"])}">{_e(item["title"])}</a></h3><p>게시일 {_e(item["published_on"])}<br>신청 기간 {_e(period)}</p>'
            f'<p class="meta">{_e(item["status_basis"])} · 목록 공고 상태 {_e(item["declared_state"] or "미확인")} · 공고 마감 {_e(item.get("announcement_end") or "미확인")}</p>{eligibility}</article>')
    sources = "".join(f'<li><a href="{_e(source["list_url"])}">{source["label"]} 공식 공고</a> · {ctx["states"][code]}</li>' for code, source in OFFICIAL_SOURCES.items())
    css = """
body{margin:0;background:#f5f7fa;color:#18222f;font:16px/1.7 system-ui,sans-serif}
.re-support-page,.re-support-page *{box-sizing:border-box}
.re-support-page{max-width:1120px;margin:auto;padding:24px 20px;word-break:keep-all;overflow-wrap:normal}
.re-support-page .re-support-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.re-support-page .re-support-notices{grid-template-columns:repeat(2,minmax(0,1fr))}
.re-support-page article{min-width:0;padding:18px;background:white;border:1px solid #dce3eb;border-radius:12px}
.re-support-page h1{font-size:28px;line-height:1.4}
.re-support-page h2{font-size:22px;margin-top:32px}
.re-support-page h3{font-size:18px;margin:0 0 8px;line-height:1.6}
.re-support-page a{color:#155b86;word-break:keep-all;overflow-wrap:normal}
.re-support-page a.re-support-url{overflow-wrap:anywhere}
.re-support-page .meta{font-size:13px;color:#536477}
.re-support-page .tag{font-size:14px;font-weight:600;color:#365d76}
.re-support-page .warning{padding:14px;border-left:4px solid #b58232;background:#fff8e8}
.re-support-page p{word-break:keep-all;overflow-wrap:normal}
@media(max-width:1000px){.re-support-page .re-support-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px){.re-support-page .re-support-grid{grid-template-columns:minmax(0,1fr)}.re-support-page h1{font-size:25px}}
"""
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>청년·신혼·생애최초 청약 및 공공임대 공식 안내 | 서울 부동산</title><meta name="description" content="SH·LH·서울시 청년안심주택·청약홈 공식 공고와 청년·신혼부부·생애최초·무순위·공공임대 확인 항목, 신청 일정과 접수 상태를 확인하세요.">'
        f'<style>{css}</style></head><body><main class="re-support-page"><a href="index.html">서울 부동산으로 돌아가기</a><h1>청약·공공임대 공식 안내</h1>{introduction}'
        + "".join(f'<p class="warning">{_e(warning)}</p>' for warning in warnings)
        + f'<h2>유형별 확인 항목</h2><section class="re-support-grid" aria-label="유형별 확인 항목">{guidance}</section>'
        + f'<h2>실제 모집공고</h2><section class="re-support-grid re-support-notices" aria-label="실제 모집공고">{"".join(notices)}</section><h2>공식 공고 목록</h2><ul>{sources}</ul>'
        '<p class="meta">청약홈 목록의 현재 공고는 자동 확인하지 못합니다. 무순위 공고를 모두 무주택자 대상이라고 일반화하지 않습니다.</p></main></body></html>')
