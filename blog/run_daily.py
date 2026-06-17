"""일일 자동 발행 오케스트레이터 — A모델(실명 사실 레이어, 자체 점수 없음).

흐름(2026-06-17 재설계 — 익명+점수 → 실명 사실):
  1) build_explorer.build_dataset: 공공 실거래(국토부 RTMS) + 단지정보(세대수·연식·전용·유형) 실명 수집
     + 사용자 고정 제외규칙(세대수≥200·corridor) 적용.
  2) write_out: dataset.json + explorer.html(방문자 필터형 탐색기).
  3) write_posts: 자치구별 실명 사실 포스트(SEO 본체) + claims.jsonl + llms.txt.
  4) 신선도 게이트: data_asof 초과면 STALE(차단 옵션).
  5) 티스토리 완성원고 + 네이버 티저(실명 사실).

가드: 사설 호가 미게재(공공 실거래만)·자체 평가/점수/순위 없음·면책·출처·이의제기(takedown).
cron: `5 7 * * *  agent-realestate daily` (cli.cmd_daily 가 호출).
"""
from __future__ import annotations
import os, glob, argparse
from datetime import date
from collections import defaultdict

import blog.build_explorer as be
import blog.tistory_draft as td
import blog.naver_teaser as nt


def _latest_or(pattern: str, fallback: str) -> str:
    files = sorted(glob.glob(pattern))
    return files[-1] if files else fallback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True)
    ap.add_argument("--today")
    ap.add_argument("--universe", default=(os.environ.get("RE_UNIVERSE") or
                    _latest_or("examples/candidates_universe[0-9][0-9][0-9]_*.json",
                               "examples/candidates_universe159_20260606.json")))
    ap.add_argument("--molit", default=(os.environ.get("RE_MOLIT") or
                    _latest_or("examples/molit_recent*.json", "examples/molit_recent_11gu_20260606.json")))
    ap.add_argument("--out", default="report/blog")
    ap.add_argument("--block-stale", action="store_true")
    ap.add_argument("--districts", help="발행 구 쉼표구분(예: 양천,강서) — 미지정 시 기본 전체")
    a = ap.parse_args()
    today = a.today or date.today().isoformat()
    from agent_realestate import config
    config.load_env_file()   # .env 의 RE_EMAIL_TO(takedown 연락처) 주입 — standalone 실행 보장(cmd_daily 경유시는 이미 주입됨)

    ds = be.build_dataset(a.universe, a.molit, a.asof, today)
    if a.districts:
        keep = {g.strip() for g in a.districts.split(",")}
        ds["complexes"] = [r for r in ds["complexes"] if r["gu"] in keep]
        ds["count"] = len(ds["complexes"])

    stale = (date.fromisoformat(today) - date.fromisoformat(a.asof)).days > be.FRESH_DAYS
    if stale and a.block_stale:
        print(f"[run_daily] STALE (asof {a.asof}, D-{(date.fromisoformat(today)-date.fromisoformat(a.asof)).days}) + --block-stale → 발행 스킵")
        return

    be.write_out(ds, a.out)                 # dataset.json + explorer.html
    summaries = be.write_posts(ds, a.out)   # 자치구별 실명 포스트 + claims + llms.txt

    # 티스토리 완성원고 + 네이버 티저 (실명 사실)
    by = defaultdict(list)
    for r in ds["complexes"]:
        by[r["gu"]].append(r)
    secs = [td.build_tistory_section(gu, by[gu]) for gu in sorted(by) if by[gu]]
    if secs:
        draft = td.write_daily_draft(secs, today, a.asof, a.out)
        print(f"티스토리 원고: {draft}  (열어 복사 → 티스토리 HTML 모드 붙여넣기 → 발행)")
        naver = nt.write_naver_teaser(summaries, today, a.asof, outdir=a.out)
        print(f"네이버 티저: {naver}  (티스토리 발행 후 URL 입력 → 본문 복사 → 네이버 등록)")
    print(f"발행 {len(by)}구 / {ds['count']}단지 · today={today} asof={a.asof} · 제외 {ds.get('excluded')}"
          + (" · ⚠STALE" if stale else ""))


if __name__ == "__main__":
    main()
