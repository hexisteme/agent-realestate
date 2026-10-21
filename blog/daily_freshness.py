"""오늘 새로 확인(DailyFreshness) — 외부 리뷰(2026-10-13) C14.

일간 글이 매일 같은 표·문장을 되풀이해 그 날 무엇이 새로 들어왔는지 알 수 없었다. 직전 MOLIT 원본 아카이브와
직전 거시 스냅샷을 오늘 것과 비교해 새로 나타난 실거래 기록(계약월 분포)·사라진 기록·값이 바뀐 지표·같은 값·
수집 실패를 적는다. '그 날 체결'이 아니라 '그 날 목록에서 확인'이다 — 신고·정정·해제 구분은 원본에 없어 단정하지 않는다.
전일 기준이 없으면 그 줄을 비교 불가로 남기지 않고 생략한다(지어내지 않음).
"""
from __future__ import annotations

import glob
import gzip
import html
import json
import os
import re

from blog import period_delta as pdl

_MOLIT_RE = re.compile(r"molit-(\d{4}-\d{2}-\d{2})\.json\.gz$")
_MAX_LABELS = 5


def latest_molit_archive_before(today: str, dir: str) -> tuple[str, dict] | None:
    """today 보다 이른 MOLIT 아카이브 중 가장 최근 것(날짜, 원본). 없으면 None — 오늘 아카이브는 고르지 않는다."""
    days = sorted(m.group(1) for p in glob.glob(os.path.join(dir, "molit-*.json.gz"))
                  if (m := _MOLIT_RE.search(p)) and m.group(1) < today)
    if not days:
        return None
    with gzip.open(os.path.join(dir, f"molit-{days[-1]}.json.gz"), "rt", encoding="utf-8") as fh:
        return days[-1], json.load(fh)


def compare_macro(prev: dict | None, cur: dict | None) -> dict | None:
    """직전 거시 스냅샷 대비 — 값이 바뀐 지표, 같은 값 지표 수, 직전에 값이 없어 비교할 수 없는 지표(no_base),
    오늘 수집 실패 지표. 직전에 없던 지표를 '바뀜'으로 세지 않는다(R2 Codex). cur 가 없으면 None,
    prev 가 없으면 has_base=False(비교 불가)."""
    if not cur:
        return None
    before, now = (prev or {}).get("indicators") or {}, cur.get("indicators") or {}
    changed, no_base, same = [], [], 0
    for code, ind in now.items():
        label = ind.get("label") or code
        if code not in before:
            no_base.append(label)
        elif before[code].get("value") == ind.get("value"):
            same += 1
        else:
            changed.append(label)
    failed = [(before.get(code) or {}).get("label") or code for code in sorted(cur.get("errors") or {})]
    return {"changed": changed, "n_unchanged": same, "no_base": no_base, "failed": failed,
            "has_base": prev is not None, "base_asof": (prev or {}).get("asof")}


def build_daily_freshness(today: str, molit_cur: dict | None, molit_prev: tuple[str, dict] | None,
                          macro_cur: dict | None, macro_prev: dict | None) -> dict | None:
    """DailyFreshness — {"today","filings","molit_base","molit_available","macro"}. 오늘 자료가 하나도 없으면 None.
    직전 기준이 없으면 비교 불가로 남기고(R2 Codex — 수집 실패·비교 불가 고지가 사라지지 않게) 지어내지 않는다.
    사라진 기록의 창 이탈은 오늘 수집 창 시작월로 가르고, 직전 아카이브 수집 창 뒤의 월(월 첫날 새로 든 직전 달)은
    새로 나타난 기록에서 뺀다."""
    if molit_cur is None and macro_cur is None:
        return None
    filings = None
    if molit_cur is not None and molit_prev:
        filings = pdl.diff_filings(molit_prev[1], molit_cur, window_start=pdl.molit_window_start(today),
                                   base_window_end=pdl.molit_window_end(molit_prev[0]))
    return {"today": today, "filings": filings, "molit_base": molit_prev[0] if molit_prev else None,
            "molit_available": molit_cur is not None, "macro": compare_macro(macro_prev, macro_cur)}


def _labels(xs: list[str]) -> str:
    shown = "·".join(xs[:_MAX_LABELS])
    return shown + (f" 외 {len(xs) - _MAX_LABELS}개" if len(xs) > _MAX_LABELS else "")


def render_freshness_html(fr: dict | None) -> str:
    """첫머리 블록(티스토리·사이트 공용 인라인 HTML). 줄마다 자기 비교 기준일을 적는다. fr 이 None 이면 빈 문자열."""
    if not fr:
        return ""
    lines = []
    f = fr.get("filings")
    if f is not None:
        lines.append(f"국토부 실거래 목록({fr['molit_base']} 자료 대비): 새로 나타난 기록 {f['n_new']:,}건"
                     f"{pdl.filing_age_txt(f)}{pdl.filing_window_txt(f)} · 사라진 기록 {f['n_gone']:,}건{pdl.filing_gone_txt(f)}.")
    elif fr.get("molit_available"):
        lines.append("국토부 실거래 목록: 직전 아카이브 없음(비교 불가).")
    m = fr.get("macro")
    if m is not None:
        if m["has_base"]:
            part = (f"거시 지표({m['base_asof'] or '직전'} 스냅샷 대비): 값이 바뀐 지표 {len(m['changed'])}개"
                    + (f": {_labels(m['changed'])}" if m["changed"] else "") + f" · 직전과 같은 값 {m['n_unchanged']}개")
            if m["no_base"]:
                part += f" · 직전 값 없음 {len(m['no_base'])}개: {_labels(m['no_base'])}"
        else:
            part = "거시 지표: 직전 스냅샷 없음(비교 불가)"
        if m["failed"]:
            part += f" · 오늘 수집 실패 {len(m['failed'])}개: {_labels(m['failed'])}"
        lines.append(part + ".")
    body = "<br>".join(html.escape(line) for line in lines)
    return f"<p data-daily-freshness><b>오늘 새로 확인</b><br>{body}</p>"
