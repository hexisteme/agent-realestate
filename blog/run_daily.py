"""일일 자동 발행 오케스트레이터 — 신선도-임계 이벤트 발행(매일 강제 X).

흐름(각 구):
  1) MOLIT 최신 실거래 refresh(공공데이터) + 네이버 호가 스냅샷 asof 확인
  2) 결정론 재채점(자체 10축) → 익명화 + 부정판정 제외
  3) daily_publisher 로 인간 HTML + JSON-LD + claims.jsonl + llms.txt 생성
  4) 신선도 게이트: asof 가 today-FRESH_DAYS 초과면 STALE 워터마크(차단 옵션 가능)
  5) publish hook: 정적사이트(git push) / 수동 cross-post 큐

가드(코드 강제, ConductionReport): 익명화·부정판정 제외·원본시세 미재게시(band)·면책·신선도.
cron 예: `5 7 * * *  cd <repo> && python -m blog.run_daily --asof <scan_date>`  (매일 07:05, 신선도 통과분만 발행)
"""
from __future__ import annotations
import json, re, os, statistics as st, argparse
from datetime import date
from agent_realestate.collectors.naver_live import load_candidates
from agent_realestate.analysts.scoring import score_candidates
from agent_realestate.analysts.redev import score_redev
from agent_realestate.domain import ExitStrategy
import blog.daily_publisher as dp
import blog.tistory_draft as td

# 기본 발행 구 — --districts "양천,강서,…" 인자로 override (범용화 2026-06-12, lawd 자동 해석)
GU_LAWD={"양천":"11470","강서":"11500","구로":"11530","동대문":"11230","마포":"11440",
         "성북":"11290","영등포":"11560","종로":"11110","동작":"11590"}
AXK={"전세수요":"전세","환금성":"환금","가격방어":"방어","상승여력":"상승","토지지분":"토지",
     "가격메리트":"메리트","출퇴근":"출퇴근","학군":"학군","경사":"경사","후기":"후기"}
def core(nm): return re.sub(r"[\(\[].*?[\)\]]","",nm).replace(" ","")

def gen_gu(gu, lawd, molit, uni, asof, today, outdir, block_stale=False):
    def med(c):
        cn=core(c.listing.complex_name); ar=c.listing.area_exclusive_m2
        px=[r['price'] for r in molit.get(lawd,[]) if r.get('price') and abs(r['area']-ar)<=3.5
            and (core(r['apt']) in cn or cn in core(r['apt']) or core(r['apt'])[:4]==cn[:4])]
        if len(px)<2: return None,0
        m0=st.median(px); px=[p for p in px if p>=m0*0.6]
        return (st.median(px) if px else None),len(px)
    pool=[c for c in uni if gu in c.district]
    if not pool: return None
    ax=score_candidates(pool,[score_redev(c) for c in pool],ExitStrategy.HOLD_AND_RENT,reference_candidates=uni)
    rows=[]
    for i,a in enumerate(sorted(ax,key=lambda x:-x.fundamental_total),1):
        c=a.candidate; md,n=med(c); eff=md if md else c.listing.price_krw
        rows.append({"id":dp.anonymize(gu,c.saenghwalgwon,i),"area":round(c.listing.area_exclusive_m2),
            "eff_eok":round(eff/1e8,2),"src":"실거래" if md else "호가","n":n,"fund":a.fundamental_total,
            "strong":[AXK[x] for x in dp.top_axes(a.scores,2)],"units":c.units,"built":c.built_year,
            "prov_eff":"F" if md else "I"})
    post=dp.build_post(gu,rows,asof,today)
    if post["stale"] and block_stale:
        return {"gu":gu,"skipped":"STALE","asof":asof}
    os.makedirs(f"{outdir}/posts",exist_ok=True)
    open(f"{outdir}/posts/{today}-{gu}.html","w").write(post["html"])
    with open(f"{outdir}/posts/{today}-{gu}.claims.jsonl","w") as f:
        for cl in post["claims"]: f.write(json.dumps(cl,ensure_ascii=False)+"\n")
    return {"gu":gu,"n":len(rows),"stale":post["stale"],"llms":post["llms_line"],
            "tistory_sec":td.build_tistory_section(gu,rows)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--asof",required=True); ap.add_argument("--today")
    ap.add_argument("--universe",default="examples/candidates_universe159_20260606.json")
    ap.add_argument("--molit",default="examples/molit_recent_11gu_20260606.json")
    ap.add_argument("--out",default="report/blog"); ap.add_argument("--block-stale",action="store_true")
    ap.add_argument("--districts",help="발행 구 쉼표구분(예: 양천,강서) — 미지정 시 기본 9구")
    a=ap.parse_args(); today=a.today or date.today().isoformat()
    gu_lawd=GU_LAWD
    if a.districts:
        from agent_realestate.collectors.lawd import lawd_for_district
        gu_lawd={}
        for g in a.districts.split(","):
            g=g.strip(); c=lawd_for_district(g)
            if c is None: raise SystemExit(f"[--districts] 알 수 없는 구: {g}")
            gu_lawd[g]=c
    molit=json.load(open(a.molit)); uni=load_candidates(a.universe)
    results=[]; llms_lines=[]
    for gu,lawd in gu_lawd.items():
        r=gen_gu(gu,lawd,molit,uni,a.asof,today,a.out,a.block_stale)
        if r: results.append(r); llms_lines+=[r["llms"]] if r.get("llms") else []
    # llms.txt 재작성
    hdr="# 서울 부동산 데이터 스냅샷 (개인 연구)\n\n> 자체 결정론 10축 구조점수 + 국토부 공공 실거래 band. 익명·통계·방법론. 투자자문 아님. CC-BY-NC.\n\n## Posts\n"
    open(f"{a.out}/llms.txt","w").write(hdr+"\n".join(llms_lines)+"\n")
    pub=[r for r in results if not r.get("skipped")]; sk=[r for r in results if r.get("skipped")]
    # 티스토리 반자동 원고 (일일 통합 1포스트 — 사용자는 헬퍼 페이지에서 복사 → 등록만)
    secs=[r["tistory_sec"] for r in pub if r.get("tistory_sec")]
    if secs:
        draft=td.write_daily_draft(secs,today,a.asof,a.out)
        print(f"티스토리 원고: {draft}  (브라우저로 열어 복사 → 티스토리 HTML 모드 붙여넣기 → 발행)")
    print(f"발행 {len(pub)}구 / 스킵(STALE) {len(sk)}구 · today={today} asof={a.asof}")
    for r in results: print(" ", r)

if __name__=="__main__": main()
