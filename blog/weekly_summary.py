"""텔레그램 주간 요약(2026-09-05 P1) — 매주 1회(기본 월요일) 25구 다이제스트를 압축 발송.
A모델 무점수: 사실 수치 요약뿐, 평가·추천 문구 없음(wording_guard 로 강제).
멱등: marker_path 에 '전송한 ISO 연-주'(YYYY-Www)를 남겨 같은 주 재실행 시 중복 전송하지 않는다
(cmd_daily 가 07:05/09:05/12:05… 하루 여러 번 실행되므로 날짜가 아닌 marker 파일로 멱등 보장).
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import date

import blog.build_explorer as be
from blog.build_site import BASE_URL
from blog.wording_guard import assert_wording_ok

_TOP_N = 3


def build_weekly_summary(ds: dict, today: str) -> str:
    """텔레그램 HTML parse_mode 본문(≤3,500자) — 발행 단지 수·표본, ▲/▼ 카운트,
    12개월 범위 상단/하단 top3, 전세가율 top3, 다이제스트·인덱스 링크."""
    cx = ds["complexes"]
    n_total = ds.get("count", len(cx))
    n_sample = sum(r.get("molit_n") or 0 for r in cx)
    up = sum(1 for r in cx if r.get("molit_trend_dir") == "▲")
    down = sum(1 for r in cx if r.get("molit_trend_dir") == "▼")
    base = [r for r in cx if be.passes_rank_gate(r)]
    hi = sorted([r for r in base if (r.get("molit_pos_52w") or 0) >= 99],
                key=lambda r: (-(r["molit_pos_52w"]), -(r.get("molit_trend_pct") or 0)))[:_TOP_N]
    lo = sorted([r for r in base if r.get("molit_pos_52w") is not None and r["molit_pos_52w"] <= 6],
                key=lambda r: r["molit_pos_52w"])[:_TOP_N]
    jr = sorted([r for r in base if be.passes_jeonse_gate(r)],
                key=lambda r: -r["jeonse_ratio_complex_pct"])[:_TOP_N]

    def line(r: dict, extra: str) -> str:
        return f'· {r["gu"]} {r["name"]} {r["molit_recent_eok"]:g}억 n{r["molit_n"]} {extra}'

    lines = [
        "<b>서울 아파트 주간 요약</b>",
        f'기준일 {ds.get("data_asof", today)} · 발행 {n_total}단지 · 표본 {n_sample}건',
        f'▲{up} · ▼{down}',
        "",
        "<b>12개월 범위 상단 근접</b>",
        *([line(r, f'52주 {r["molit_pos_52w"]:g}%') for r in hi] or ["· 해당 없음"]),
        "",
        "<b>12개월 범위 하단 근접</b>",
        *([line(r, f'52주 {r["molit_pos_52w"]:g}%') for r in lo] or ["· 해당 없음"]),
        "",
        "<b>전세가율 상위</b>",
        *([line(r, f'전세가율 {r["jeonse_ratio_complex_pct"]:g}%') for r in jr] or ["· 해당 없음"]),
        "",
        f'전체: {BASE_URL}/daily/latest.html',
        f'인덱스: {BASE_URL}/',
        "",
        "자체 점수·순위 없음 — 국토부 공공 실거래 사실만.",
    ]
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3480] + "\n…(요약 생략)"
    assert_wording_ok(text, "weekly_summary")
    return text


def send_weekly_summary(dataset_path: str, today: str, marker_path: str, weekday: int = 0) -> bool:
    """weekday(기본 0=월) 와 오늘이 일치하고, marker_path 에 이번 ISO 연-주 기록이 없을 때만 전송.
    전송 성공 후에만 marker 를 갱신한다(실패 시 같은 주 다음 슬롯에서 재시도 가능)."""
    d = date.fromisoformat(today)
    if d.weekday() != weekday:
        return False
    iso_year, iso_week, _ = d.isocalendar()
    wk = f"{iso_year}-W{iso_week:02d}"
    if os.path.exists(marker_path) and open(marker_path, encoding="utf-8").read().strip() == wk:
        return False   # 이번 주 이미 전송 — 멱등
    ds = json.load(open(dataset_path, encoding="utf-8"))
    text = build_weekly_summary(ds, today)
    from agent_realestate.notify.telegram import send_message
    ok = send_message(text, chat_id=os.environ.get("TELEGRAM_WEEKLY_CHAT_ID"))
    if ok:
        open(marker_path, "w", encoding="utf-8").write(wk)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--dry-run", action="store_true",
                     help="본문만 출력하고 전송하지 않음 — 본 CLI 는 --dry-run 여부와 무관하게 전송하지 않는다"
                          "(실전송은 cmd_daily → send_weekly_summary 경유).")
    a = ap.parse_args()
    ds = json.load(open(a.dataset, encoding="utf-8"))
    print(build_weekly_summary(ds, a.today))


if __name__ == "__main__":
    main()
