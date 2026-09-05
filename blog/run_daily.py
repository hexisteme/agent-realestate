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
from pathlib import Path

import blog.build_explorer as be
import blog.tistory_draft as td
import blog.naver_teaser as nt
import blog.daily_digest as dd


def _latest_or(pattern: str, fallback: str) -> str:
    files = sorted(glob.glob(pattern))
    return files[-1] if files else fallback


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 파서 구성 — --public-frame/--survivors/--jeonse/--molit/--public-gu-allow 기본값은
    scope_inputs.resolve_scope_inputs(RE_SCAN_SCOPE)에서 온다(2026-09-05 단일소스화).

    사고 배경: 이 함수가 분리되기 전엔 run_daily 를 인자 없이 단독 실행하면(오늘 실제로 발생)
    cmd_daily(cli.py)가 RE_SCAN_SCOPE=25gu 일 때 넘기는 값을 전혀 몰라 legacy 11gu 기본값(public-frame/
    survivors 미지정 → 구 universe 경로)으로 조립됐다. 우선순위: 명시 CLI 인자 > 환경변수
    (RE_MOLIT/RE_JEONSE_RECENT/RE_PUBLIC_GU_ALLOW) > scope_inputs > 기존 legacy 폴백(_latest_or)."""
    from agent_realestate.scope_inputs import current_scope, resolve_scope_inputs
    root = Path(__file__).resolve().parents[1]
    inputs = resolve_scope_inputs(current_scope(), root)

    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True)
    ap.add_argument("--today")
    ap.add_argument("--universe", default=(os.environ.get("RE_UNIVERSE") or
                    _latest_or("examples/candidates_universe[0-9][0-9][0-9]_*.json",
                               "examples/candidates_universe159_20260606.json")))
    ap.add_argument("--molit", default=(os.environ.get("RE_MOLIT") or str(inputs["molit"]) or
                    _latest_or("examples/molit_recent*.json", "examples/molit_recent_11gu_20260606.json")))
    ap.add_argument("--out", default="report/blog")
    ap.add_argument("--block-stale", action="store_true")
    ap.add_argument("--districts", help="발행 구 쉼표구분(예: 양천,강서) — 미지정 시 기본 전체")
    ap.add_argument("--public-frame",
                    default=(str(inputs["public_frame"]) if inputs["public_frame"] else None),
                    help="WS-0 public-only 경로: 지정 시 frame(공공 enumeration JSON) + --molit 로 "
                         "build_dataset_public 사용(호가 Listing 불필요). 미지정 시 scope=25gu 면 "
                         "resolve_scope_inputs 기본값, 아니면(legacy) 기존 --universe 경로 그대로.")
    ap.add_argument("--survivors",
                    default=(str(inputs["survivors"]) if inputs["survivors"] else None),
                    help="public 경로 발행 풀 제한 — 스캔 생존 JSON(screen_25gu_survivors 등)의 complexNo 만 발행. "
                         "미지정 시 scope=25gu 면 resolve_scope_inputs 기본값.")
    ap.add_argument("--public-gu-allow", default=(os.environ.get("RE_PUBLIC_GU_ALLOW") or
                    inputs["public_gu_allow"] or ""),
                    help="구별 단계오픈 안전판 — public(B) 신규유입에만 적용되는 구 쉼표목록 "
                         "(예: 성동,강동). 미지정 시 frame 의 모든 신규구 생성(제한 없음). "
                         "기존 발행(A)엔 영향 없음 — enrichment 백필 완료된 구만 여기 추가할 것.")
    ap.add_argument("--enrich-overlay", default=(os.environ.get("RE_ENRICH_OVERLAY") or
                    _latest_or("examples/enrich_overlay_*.json", "")),
                    help="public 신규단지 K-apt/공시가/관리비/카카오 overlay(collect_public_enrich.py 산출). 없으면 스킵.")
    ap.add_argument("--jeonse", default=(os.environ.get("RE_JEONSE_RECENT") or
                    (str(inputs["jeonse"]) if inputs["jeonse"] else None) or
                    _latest_or("examples/molit_jeonse_recent*.json", "")),
                    help="전세 recent 12개월 MOLIT(D 파생용, fetch_molit_jeonse_recent_25gu.py 산출). 없으면 스킵.")
    return ap


def main():
    from agent_realestate import config
    config.load_env_file()   # .env 주입 — scope-aware 기본값(build_arg_parser)이 파서 구성 시점에 보려면
                              # parse_args 이전에 필요하다(2026-09-05 사고 수정: 예전엔 parse_args 뒤에
                              # 호출돼 단독 실행 시 .env 의 RE_SCAN_SCOPE 가 기본값에 반영되지 않았다).
    ap = build_arg_parser()
    a = ap.parse_args()
    today = a.today or date.today().isoformat()
    from agent_realestate.scope_inputs import current_scope
    print(f"[run_daily] scope={current_scope()} public_frame={a.public_frame} survivors={a.survivors}")

    if a.public_frame:
        gu_allow = ({g.strip() for g in a.public_gu_allow.split(",") if g.strip()}
                    if a.public_gu_allow else None)
        # anchor_universe=a.universe — 기존 발행 단지의 면적 앵커(수치 연속성). 신규 단지는 최다거래 평형.
        ds = be.build_dataset_public(a.public_frame, a.molit, a.asof, today,
                                     survivors_path=a.survivors, anchor_universe=a.universe,
                                     gu_allowlist=gu_allow)
        ds = be.add_enrich_overlay(ds, a.enrich_overlay)   # K-apt·공시가·관리비·카카오(신규단지, 없으면 스킵)
    else:
        ds = be.build_dataset(a.universe, a.molit, a.asof, today)
    # ── 가격세그먼트(F)·유동성(C)·전세갭(D) — 경로 무관 단일 후처리(풀확대 2단계, 2026-07-10) ──
    ds = be.add_price_segment(ds)
    ds = be.add_liquidity_facts(ds, a.molit)
    ds = be.add_jeonse_facts(ds, a.jeonse)              # jeonse 파일 없으면 조용히 스킵
    if a.districts:
        keep = {g.strip() for g in a.districts.split(",")}
        ds["complexes"] = [r for r in ds["complexes"] if r["gu"] in keep]
        ds["count"] = len(ds["complexes"])

    stale = (date.fromisoformat(today) - date.fromisoformat(a.asof)).days > be.FRESH_DAYS
    if stale and a.block_stale:
        print(f"[run_daily] STALE (asof {a.asof}, D-{(date.fromisoformat(today)-date.fromisoformat(a.asof)).days}) + --block-stale → 발행 스킵")
        return

    be.assert_no_duplicate_signatures(ds)   # 동일시그니처(매칭결함) 게이트 — 회귀 시 발행 대신 예외(2026-09-05)
    be.write_out(ds, a.out)                 # dataset.json + explorer.html
    summaries = be.write_posts(ds, a.out)   # 자치구별 실명 포스트 + claims + llms.txt

    # 일간 다이제스트(2026-09-05 P1) — 25구 통합 덤프(build_tistory_section 반복) 대신 다이제스트 1편.
    #   build_tistory_section/build_daily_body/write_daily_draft 는 back-compat·테스트용으로 존치.
    by = defaultdict(list)
    for r in ds["complexes"]:
        by[r["gu"]].append(r)
    if ds["complexes"]:
        digest = dd.build_daily_digest(ds, today, a.asof)
        draft = td.write_digest_draft(digest, today, a.out)
        print(f"티스토리 원고: {draft}  (열어 복사 → 티스토리 HTML 모드 붙여넣기 → 발행)")
        os.makedirs(f"{a.out}/daily", exist_ok=True)
        open(f"{a.out}/daily/{today}.html", "w").write(digest["site_html"])
        open(f"{a.out}/daily/latest.html", "w").write(digest["site_html"])
        print(f"일간 다이제스트: {a.out}/daily/{today}.html · {a.out}/daily/latest.html")
        naver = nt.write_naver_teaser(summaries, today, a.asof, outdir=a.out)
        print(f"네이버 티저: {naver}  (티스토리 발행 후 URL 입력 → 본문 복사 → 네이버 등록)")
    print(f"발행 {len(by)}구 / {ds['count']}단지 · today={today} asof={a.asof} · 제외 {ds.get('excluded')}"
          + (" · ⚠STALE" if stale else ""))


if __name__ == "__main__":
    main()
