"""정적사이트 조립 — 생성된 일일 포스트 + 랜딩·sitemap·robots·ai.txt·RSS 를 묶어 배포가능 site/ 생성.
GitHub Pages 용. AI 친화: /llms.txt(root) + robots.txt(AI 크롤러 허용) + 각 포스트 JSON-LD + claims.jsonl.
"""
from __future__ import annotations
import os, shutil, glob, re, html, json
from datetime import date, datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import quote

import blog.build_explorer as be   # gu_hub.py 와 동일 관례(모듈 top-level import, 순환 없음 — be 는 build_site 를 지연import만 함)

# BLOG_SITE_DIR/BLOG_SRC_DIR(2026-09-05 P1) — 미설정 시 기존 경로 그대로(회귀 없음). 테스트·검증용
# 스크래치 빌드가 실제 site/ 를 건드리지 않도록 오버라이드 지점을 연다.
SITE=os.environ.get("BLOG_SITE_DIR","site"); SRC=os.environ.get("BLOG_SRC_DIR","report/blog")
# 네이버 서치어드바이저 RSS/sitemap 은 절대 URL 필수 (searchadvisor.naver.com/guide/request-feed)
BASE_URL=os.environ.get("BLOG_BASE_URL","https://hexisteme.github.io/seoul-re-snapshot").rstrip("/")
KST=timezone(timedelta(hours=9))
FEED_MAX=50
GA4_MEASUREMENT_ID=os.environ.get("GA4_MEASUREMENT_ID","G-J40FMJG903")


def ga4_snippet() -> str:
    """GA4 gtag.js 로더(2026-09-05 P1) — GA4_MEASUREMENT_ID 빈 문자열이면 빈 스니펫(계측 비활성)."""
    if not GA4_MEASUREMENT_ID:
        return ""
    mid = GA4_MEASUREMENT_ID
    return (f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>\n'
            "<script>window.dataLayer=window.dataLayer||[];"
            "function gtag(){dataLayer.push(arguments);}"
            "gtag('js',new Date());"
            f"gtag('config','{mid}');</script>\n")

def inject_ga4_tag(html: str) -> str:
    """레거시 포스트(2026-09-05 P1 이전 생성분)에 GA4 로더 주입 — 측정ID 가 이미 있으면 원문(멱등), <head> 없으면 원문."""
    snip = ga4_snippet()
    if not snip or GA4_MEASUREMENT_ID in html:
        return html
    m = re.search(r"<head[^>]*>", html, re.I)
    if not m:
        return html
    return html[:m.end()] + "\n" + snip + html[m.end():]

def _post_meta(p):
    """포스트 파일에서 (date, title, description) 추출 — 파일명 YYYY-MM-DD-구.html 규약(일간
    다이제스트 daily/*.html 은 파일명이 YYYY-MM-DD.html 이라 앞 10자 규칙이 그대로 통한다)."""
    nm=os.path.basename(p); d=nm[:10]
    txt=open(p).read()
    t=re.search(r"<title>(.*?)</title>",txt,re.S)
    desc=re.search(r'<meta name=description content="(.*?)">',txt)
    return d,(t.group(1).strip() if t else nm[:-5]),(desc.group(1) if desc else "")


def _fmt_eok(v: float | None) -> str:
    """구 중위(억) 표시 — 값 없으면 —(gu_hub._eok 와 동일 포맷, 별도 모듈이라 재구현)."""
    return f"{v:g}억" if v is not None else "—"


def _latest_gu_post_href(posts: list[str], gu: str) -> str | None:
    """구허브 푸터용 최신 주간 리포트 상대경로(gu/ 기준 ../posts/…). 주간 포스트는 월요일에만 생기므로
    (2026-09-06 P0) 존재하는 파일 중 최신을 고르고, 없으면 None → 허브가 링크를 생략(비월요일 404 차단)."""
    mine = sorted((p for p in posts if os.path.basename(p).endswith(f"-{gu}.html")), reverse=True)
    return f"../posts/{quote(os.path.basename(mine[0]))}" if mine else None


def _index_leads(ds_all: dict) -> str:
    """인덱스 리드 블록(2026-09-06 P0) — FactLead(별도 워커 병행 작업, blog/fact_lead.py)를 배선.
    모듈이 아직 없거나 무엇이든 예외를 내면 조용히 빈 문자열로 저하 — 인덱스 빌드가 이 때문에
    깨지면 안 된다."""
    try:
        from blog.fact_lead import build_fact_leads, render_lead_block
        return render_lead_block(build_fact_leads(ds_all, "seoul"))
    except Exception:
        return ""


def assert_dataset_not_shrunk(new_path: str, old_path: str, min_ratio: float = 0.5) -> None:
    """조립 직전 데이터셋 축소 가드(2026-09-05 실측 사고) — 인자 없는 수동 `python3 -m blog.run_daily` 가 레거시
    11구 기본값으로 돌아 117단지 dataset.json 을 만들었고, build_site 가 그것을 643단지 사이트 위에 그대로
    조립했다(push 전 발견). 기존 site/dataset.json 대비 단지 수가 min_ratio 미만이면 SystemExit 로 발행을 막는다.
    의도된 축소(풀 재편 등)는 RE_ALLOW_SHRINK=1 로 명시. 기존 파일이 없거나 파싱 불가면 가드 없음(첫 조립)."""
    if os.environ.get("RE_ALLOW_SHRINK") == "1" or not (os.path.exists(new_path) and os.path.exists(old_path)):
        return
    try:
        old = len(json.load(open(old_path, encoding="utf-8")).get("complexes") or [])
        new = len(json.load(open(new_path, encoding="utf-8")).get("complexes") or [])
    except (OSError, ValueError, AttributeError):
        return
    if old > 0 and new < old * min_ratio:
        raise SystemExit(f"[build_site] 데이터셋 축소 가드: 단지 {old} → {new} (<{min_ratio:.0%}) — 스코프 인자 누락(run_daily 11gu 기본값) 의심. "
                         "cmd_daily 와 같은 --molit/--jeonse/--public-frame/--survivors 로 재생성하거나, 의도된 축소면 RE_ALLOW_SHRINK=1")

def build(today=None, molit_path=None):
    today=today or date.today().isoformat()
    # molit_path(2026-09-05 P2) — 단지 페이지 월별차트용 raw MOLIT. 없으면(파일 부재) 차트만 생략.
    mp = molit_path or os.environ.get("RE_MOLIT") or "examples/molit_recent_25gu_20260710.json"
    os.makedirs(f"{SITE}/posts",exist_ok=True)
    os.makedirs(f"{SITE}/gu",exist_ok=True)
    os.makedirs(f"{SITE}/daily",exist_ok=True)
    # 1) 포스트·claims·llms.txt 복사
    for f in glob.glob(f"{SRC}/posts/*"): shutil.copy(f,f"{SITE}/posts/")
    # 1b) 레거시 포스트 GA4 주입(2026-09-05) — P1 이전 생성분 1,659개가 태그 없이 그대로 복사되던 계측 구멍. 파일별 멱등.
    for p in glob.glob(f"{SITE}/posts/*.html"):
        txt=open(p,encoding="utf-8").read(); tagged=inject_ga4_tag(txt)
        if tagged!=txt: open(p,"w",encoding="utf-8").write(tagged)
    # 일간 다이제스트({today}.html + latest.html, 2026-09-05 P1) — run_daily.py 산출물 복사.
    for f in glob.glob(f"{SRC}/daily/*"): shutil.copy(f,f"{SITE}/daily/")
    if os.path.exists(f"{SRC}/llms.txt"): shutil.copy(f"{SRC}/llms.txt",f"{SITE}/llms.txt")
    # 탐색기(방문자 필터형, 2026-06-16) — dataset.json + explorer.html 를 site/ 루트로 복사.
    #   posts/ 밖이라 sitemap/RSS 의 posts/*.html glob 에 안 걸려 자연 제외(JS 렌더=색인부적합, SEO 본체는 정적 포스트).
    assert_dataset_not_shrunk(f"{SRC}/dataset.json", f"{SITE}/dataset.json")   # 축소 가드(2026-09-05) — 11gu 기본값 데이터셋이 25gu 사이트 위에 조립되는 사고 차단
    for f in ("dataset.json","explorer.html"):
        if os.path.exists(f"{SRC}/{f}"): shutil.copy(f"{SRC}/{f}",f"{SITE}/{f}")
    # 1c) 일별 스냅샷(2026-09-06 P0) — 주간/기간 비교용 dataset.json 보관 + 14일 보존. dir 은 SRC 기준
    #   (report/blog/snapshots 기본값과 일치)이라 BLOG_SRC_DIR 오버라이드(테스트) 시 실 데이터를 안 건드린다.
    if os.path.exists(f"{SRC}/dataset.json"):
        from blog.snapshots import save_snapshot
        save_snapshot(f"{SRC}/dataset.json", today, dir=f"{SRC}/snapshots")
    from blog.snapshots import load_snapshot_days_ago
    prev_ds=load_snapshot_days_ago(7, dir=f"{SRC}/snapshots")   # 7일 전(±1일) 스냅샷 — 사실 리드 '패턴' 재현 판정용, 없으면 None('이번 주 관측' 표기)
    posts=sorted(glob.glob(f"{SITE}/posts/*.html"),reverse=True)
    # 2a) 구 허브 25개(2026-09-05 P1) — dataset.json 에서 직접 렌더(gu_hub.render_gu_hub), site/gu/ 로.
    gu_list=[]
    complex_count=0   # 2a-2 에서 채움(P2) — ds_path 없으면 0 유지
    ds_all=None        # 인덱스 재구성(2026-09-06 P0)이 아래 if 밖에서도 참조 — 없으면 None 유지
    ds_path=f"{SITE}/dataset.json"
    if os.path.exists(ds_path):
        import blog.gu_hub as gh
        import blog.complex_page as cp
        import blog.daily_digest as dd
        ds_all=json.load(open(ds_path,encoding="utf-8"))
        by_gu={}
        for r in ds_all["complexes"]: by_gu.setdefault(r["gu"],[]).append(r)
        asof=ds_all.get("data_asof",today)
        gen=ds_all.get("generated",today)   # 포스트 파일명(posts/{gen}-{gu}.html)과 일치시켜야 허브 링크가 안 깨짐
        for gu in sorted(by_gu):
            open(f"{SITE}/gu/{gu}.html","w").write(gh.render_gu_hub(gu,by_gu[gu],asof,gen,ds=ds_all,prev_ds=prev_ds,weekly_post_href=_latest_gu_post_href(posts,gu)))
            gu_list.append(gu)
        # 2a-2) 단지 개별 페이지(2026-09-05 P2) — 게이트(아파트·40㎡+·매매표본30건+) 통과 단지만.
        #   raw MOLIT(mp) 없으면 월별차트만 생략(render_complex_page 가 monthly=None 을 안내문으로 대체).
        gated_complexes = cp.select_page_complexes(ds_all)
        molit_raw = (json.load(open(mp, encoding="utf-8"))
                     if gated_complexes and mp and os.path.exists(mp) else None)   # 게이트 0개면 로드 스킵(테스트·소형빌드 절약)
        os.makedirs(f"{SITE}/complex", exist_ok=True)
        written_slugs: set[str] = set()
        for r in gated_complexes:
            gu = r["gu"]; gu_rows = by_gu.get(gu, [])
            peers = cp.select_peers(r, gu_rows)
            monthly = None
            if molit_raw is not None:
                lawd = be.GU_LAWD.get(gu)
                if lawd:
                    recs = be._match_records_public(r["name"], r["area_m2"], lawd, molit_raw)
                    monthly = cp.build_monthly_medians(recs, asof)
            row2 = {**r, "_gu_median_eok": be.compute_gu_median(gu_rows)}
            slug = cp.complex_slug(gu, r["name"])
            open(f"{SITE}/complex/{slug}.html","w").write(cp.render_complex_page(row2, peers, monthly, asof, gen, ds=ds_all, prev_ds=prev_ds))
            written_slugs.add(slug)
            complex_count += 1
        # 이번 회차에 쓰지 않은 단지 페이지 제거 — 단지가 다른 구로 정정되거나(2026-09-06 소재구 확정)
        # 게이트(n≥30) 아래로 내려가면 '{구}-{이름}' slug 이 바뀌어 옛 파일이 남는다. 남으면 잘못된 구의
        # 페이지가 사이트맵(complex/*.html glob)에 계속 실려 색인된다.
        for stale in glob.glob(f"{SITE}/complex/*.html"):
            if os.path.splitext(os.path.basename(stale))[0] not in written_slugs:
                os.remove(stale)
                print(f"  [정리] 옛 단지 페이지 삭제: {os.path.basename(stale)}")
    # 2b) 최신 일간 다이제스트 메타(랜딩 CTA용) — latest.html 의 <title>/<meta description> 재사용.
    digest_latest=f"{SITE}/daily/latest.html"
    digest_meta=_post_meta(digest_latest) if os.path.exists(digest_latest) else None
    # 2c) 랜딩 index.html 재구성(2026-09-06 P0) — 기존 3,448개 포스트 링크 나열(235KB)을
    #   헤더+리드+엔트리카드+25구 타일+최근 리포트+아카이브 링크+푸터로 대체(예산 <60KB). 전체 목록은
    #   archive.html 로 이전(claims.jsonl 링크 포함, 기존 items 마크업 그대로).
    asof_idx = ds_all.get("data_asof", today) if ds_all is not None else today
    lead_html = _index_leads(ds_all) if ds_all is not None else ""
    gu_summary = dd._gu_summary_rows(ds_all) if ds_all is not None else []
    chips = ('<span class=chip>기준 ' + asof_idx + '</span>'
             '<span class=chip>국토부 실거래 · 신고 지연 최대 30일</span>'
             '<span class=chip>매일 07:05 자동 갱신</span>')
    gu_tiles = "".join(
        f'<a class=gutile href="gu/{quote(g["gu"])}.html"><b>{g["gu"]}</b>'
        f'<span class=gk>{g["n"]}단지 · {_fmt_eok(g.get("gu_median"))}'
        f' · <span class=up>▲{g["up"]}</span>·<span class=down>▼{g["down"]}</span></span></a>'
        for g in gu_summary)
    digest_desc = html.escape(digest_meta[1]) if digest_meta else "일간 신규 신고 변화 요약"
    entry_cards = (
        '<div class=cards>'
        '<a class=card href="explorer.html"><h3>🔎 탐색기</h3>'
        '<p>예산·평형·연식·유형으로 단지를 필터하고 공공 실거래로 정렬</p></a>'
        f'<a class=card href="daily/latest.html"><h3>📰 오늘의 변화</h3><p>{digest_desc}</p></a>'
        '<a class=card href="methodology.html"><h3>📖 방법론</h3>'
        '<p>왜 이 숫자를 믿을 수 있나 — 측정·출처·한계</p></a>'
        '</div>')
    recent_items = "".join(
        f'<li><a href="posts/{os.path.basename(p)}">{html.escape(_post_meta(p)[1])}</a></li>'
        for p in posts[:5])
    if digest_meta:
        recent_items += f'<li><a href="daily/latest.html">{digest_desc} (최신 일간)</a></li>'
    idx=f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name="google-site-verification" content="mawCVnPZxYdhhtBgHlck2zvNYTTb7ydP6hg58_kBVCs">
<title>서울 부동산 데이터 스냅샷</title>
<meta name=description content="서울 자치구 아파트 단지의 국토부 공공 실거래 중위·분포·추세(단지 실명, 자체 점수·순위 없음). 방법론 공개. 투자자문 아님.">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebSite","name":"서울 부동산 데이터 스냅샷","inLanguage":"ko","license":"https://creativecommons.org/licenses/by-nc/4.0/","description":"국토부 공공 실거래 중위·분포·추세(단지 실명, 자체 점수·순위 없음)."}}</script>
<style>
:root{{--paper:#f7f5f0;--ink:#1b1a17;--accent:#1d6f6a;--mut:#5c584f;--line:#e6e2d9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"IBM Plex Sans KR",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;font-size:15px;line-height:1.6;font-variant-numeric:tabular-nums}}
a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 56px}}
h1{{font-size:27px;font-weight:700;letter-spacing:-.02em;margin:0 0 8px}}
.lead{{color:var(--mut);margin:0 0 14px}}
.chips{{margin-bottom:18px}}.chip{{display:inline-block;background:#fff;border:1px solid var(--line);border-radius:14px;padding:4px 12px;font-size:12px;color:var(--mut);margin:0 6px 6px 0}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 26px}}
.card{{flex:1 1 220px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;color:var(--ink);display:block}}
.card h3{{margin:0 0 6px;font-size:16px}}.card p{{margin:0;color:var(--mut);font-size:13px}}
h2{{font-size:18px;margin:28px 0 12px}}
.gugrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
.gutile{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:2px}}
.gutile b{{font-size:14px;color:var(--ink)}}.gk{{font-size:12px;color:var(--mut)}}
.up{{color:#c43d2f}}.down{{color:#2f5fc4}}
.reports{{list-style:none;padding:0;margin:0}}.reports li{{margin:4px 0;font-size:13px}}
.d{{font-size:12px;color:var(--mut);border-top:1px solid var(--line);margin-top:28px;padding-top:14px;line-height:1.7}}
@media(max-width:640px){{.cards{{flex-direction:column}}}}
</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<h1>서울 부동산 데이터 스냅샷</h1>
<p class=lead>서울 자치구 아파트 단지의 <b>국토부 공공 실거래 중위·분포·추세</b>(단지 실명 게재). 자체 평가·점수·순위 없는 사실 스냅샷.</p>
<div class=chips>{chips}</div>
{lead_html}
{entry_cards}
<h2>자치구 (25개)</h2>
<div class=gugrid>{gu_tiles}</div>
<h2>최근 리포트</h2>
<ul class=reports>{recent_items}</ul>
<p><a href="archive.html">전체 포스트 아카이브 →</a></p>
<div class=d>
⚖ {be.DISCLAIMER} 부동산은 자본시장법 금융투자상품이 아님.<br>
방법론: 국토부 RTMS 12개월 동일평형 실거래 중위·분포(P25–P75)·추세·52주 위치(이상치 −40%컷). 자체 평가·점수·순위 없음. 민간 시세(네이버·KB 등)는 사용·게재하지 않습니다.<br>
{be._takedown()}<br>
<a href="methodology.html">방법론 전문</a> · AI 인덱스: <a href="llms.txt">/llms.txt</a> · 라이선스 CC-BY-NC-4.0 · 코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a>
</div>
</div>
</body></html>"""
    open(f"{SITE}/index.html","w").write(idx)
    # 2d) archive.html — 기존 인덱스가 나열하던 전체 포스트 목록(같은 마크업·claims.jsonl 링크)을 이전.
    archive_items=""
    for p in posts:
        nm=os.path.basename(p); title=nm[:-5]
        archive_items+=f'<li><a href="posts/{nm}">{title}</a> · <a href="posts/{title}.claims.jsonl">claims.jsonl</a></li>\n'
    open(f"{SITE}/archive.html","w").write(f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>전체 포스트 아카이브 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="자치구별 실명 사실 포스트 전체 목록과 provenance(claims.jsonl). 자체 점수·순위 없음, 투자자문 아님.">
<style>body{{font:16px/1.7 -apple-system,Pretendard,sans-serif;max-width:760px;margin:0 auto;padding:28px;color:#1a1a1a}}a{{color:#0969da}}li{{margin:4px 0}}.d{{font-size:13px;color:#666;border-top:1px solid #ddd;margin-top:24px;padding-top:12px}}</style>
{ga4_snippet()}
</head><body>
<h1>전체 포스트 아카이브</h1>
<p><a href="./">← 인덱스</a></p>
<ul>{archive_items}</ul>
<div class=d>{len(posts)}편 · <a href="methodology.html">방법론</a> · <a href="llms.txt">/llms.txt</a></div>
</body></html>""")
    # 2b) 방법론 고정 페이지 — 매 포스트가 링크하는 "왜 이 숫자를 믿을 수 있나" 앵커 (슈퍼샘플, 2026-06-11)
    open(f"{SITE}/methodology.html","w").write(f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>방법론 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="국토부 공공 실거래 중위·분포·추세·52주 위치의 측정·출처·신뢰규율·한계. 자체 점수 없음, 모든 수치 provenance 동봉.">
<style>body{{font:15px/1.7 -apple-system,Pretendard,sans-serif;max-width:760px;margin:0 auto;padding:28px;color:#1a1a1a}}a{{color:#0969da}}h2{{font-size:17px;margin-top:26px}}.d{{font-size:13px;color:#666}}</style>
{ga4_snippet()}
</head><body>
<h1>방법론</h1>
<p><a href="./">← 목록</a></p>
<h2>1. 무엇을 측정하나</h2>
<p>자체 점수·순위·등급·평가는 <b>일절 산출하지 않는다</b>. 측정 대상은 단지별 <b>국토부 공공 실거래 사실 통계</b>:
동일평형(전용 ±3.5㎡)·이름매칭으로 묶은 12개월 실거래에서 ① <b>중위(억)</b>와 표본수 n,
② <b>분포</b>(P25–중위–P75 협상 레인지), ③ <b>추세</b>(최근 3개월 중위 vs 직전 9개월 중위의 방향·변화%),
④ <b>52주 위치</b>(최근 3개월 체결 중위가 12개월(52주) 실거래 최저~최고 레인지에서 차지하는 위치 % — 헤드라인 중위와 기준점이 다름). 모두 관측된 사실값이다.
세대수 200세대 미만 단지와 환금성 우려가 큰 일부 corridor 단지는 게재 전 제외한다.
이름매칭은 2026-09-05부터 단지명 canonical 완전일치만 적용한다(부분일치·접두 매칭 없음).</p>
<h2>2. 데이터 출처</h2>
<p>실거래 = 국토교통부 RTMS 공공데이터(12개월 동일평형 중위, 이상치 −40% 컷), 매일 자동 재수집.
세대수·준공연도·전용면적·유형은 공개 단지정보다. 단지는 <b>실명으로 게재</b>한다(공공 실거래의 named 재이용은 공공데이터법상 합법).
네이버·KB 등 사설 호가·민간시세는 <b>사용하지도 게재하지도 않으며</b>, 국토부 공공 실거래 중위(억)를 실제 수치로만 표기한다.</p>
<h2>3. 신뢰 규율</h2>
<p>모든 수치는 결정론 파이프라인(LLM 재계산 0)에서 나오며 모든 값은 국토부 실거래 <sup>F</sup>(사실)이고 표본수 n과 출처·확인일을 동반한다(추론 항목 없음).
각 포스트에 머신리더블 provenance(<code>claims.jsonl</code>, 1행 1주장)와 JSON-LD Dataset(variableMeasured)이 동봉된다.
동일 입력 → 동일 산출이 회귀 테스트로 고정되고, 생성 전 커버리지·위생 게이트가 공백·중복을 차단한다.</p>
<h2>4. 한계 (정직 고지)</h2>
<p>표본이 적은 단지는 중위·분포가 불안정해 분포·52주 위치를 표시하지 않을 수 있다(표본 부족 구간은 — 처리).
추세는 <b>과거 중위 비교(사실)일 뿐 미래 전망이 아니다</b>. 발견 커버리지는 전수가 아니며, 데이터는 기준일 시점이다.
본 사이트는 개인 연구·정보 공유로 <b>투자자문·매수권유가 아니다</b>.</p>
<h2>5. 시스템</h2>
<p class=d>생성: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a> (결정론 파이프라인, MIT) ·
콘텐츠 라이선스 CC-BY-NC-4.0 · AI 인덱스 <a href="llms.txt">/llms.txt</a></p>
</body></html>""")
    # 3) sitemap.xml (절대 URL — 서치어드바이저 제출용. lastmod=포스트 자체 날짜)
    #    ★한글 파일명 percent-encode 의무(sitemap 프로토콜 RFC-3986) — 미인코딩 시 구글 '가져올 수 없음'(2026-06-11 실측).
    urls="".join(f"<url><loc>{BASE_URL}/posts/{quote(os.path.basename(p))}</loc><lastmod>{_post_meta(p)[0]}</lastmod></url>" for p in posts)
    # 구 허브 + 일간 다이제스트 URL(2026-09-05 P1) — 둘 다 매일 재생성이라 lastmod=today.
    gu_urls="".join(f"<url><loc>{BASE_URL}/gu/{quote(gu)}.html</loc><lastmod>{today}</lastmod></url>" for gu in gu_list)
    digest_files=sorted(glob.glob(f"{SITE}/daily/*.html"))
    digest_urls="".join(f"<url><loc>{BASE_URL}/daily/{quote(os.path.basename(p))}</loc><lastmod>{today}</lastmod></url>" for p in digest_files)
    # 단지 페이지 URL(2026-09-05 P2)
    complex_files=sorted(glob.glob(f"{SITE}/complex/*.html"))
    complex_urls="".join(f"<url><loc>{BASE_URL}/complex/{quote(os.path.basename(p))}</loc><lastmod>{today}</lastmod></url>" for p in complex_files)
    def _urlset(body): return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    # sitemap.xml 은 sitemapindex 로 분할(2026-09-05 P2, urlset 항목 급증 대비) — 자식 3개: core(랜딩·방법론·구허브·다이제스트)·complex(단지)·posts(전체 포스트).
    core_body=(f"<url><loc>{BASE_URL}/</loc><lastmod>{today}</lastmod></url>"
               f"<url><loc>{BASE_URL}/methodology.html</loc><lastmod>{today}</lastmod></url>"
               f"<url><loc>{BASE_URL}/archive.html</loc><lastmod>{today}</lastmod></url>"
               f"{gu_urls}{digest_urls}")
    open(f"{SITE}/sitemap-core.xml","w").write(_urlset(core_body))
    open(f"{SITE}/sitemap-complex.xml","w").write(_urlset(complex_urls))
    open(f"{SITE}/sitemap-posts.xml","w").write(_urlset(urls))
    sub_sitemaps="".join(f"<sitemap><loc>{BASE_URL}/{fn}</loc><lastmod>{today}</lastmod></sitemap>"
                          for fn in ("sitemap-core.xml","sitemap-complex.xml","sitemap-posts.xml"))
    open(f"{SITE}/sitemap.xml","w").write(
        f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sub_sitemaps}</sitemapindex>')
    # 3b) feed.xml — RSS 2.0 (네이버 서치어드바이저 요구 item 필드: title/link/description/pubDate/guid)
    #     posts/*.html ∪ daily/*.html(latest.html 제외, 2026-09-06 P0) 합집합을 날짜 내림차순 정렬해 상위
    #     FEED_MAX — 예전엔 오늘자 다이제스트만 별도 특례로 맨 앞에 붙였으나(2026-09-05 P1), 과거 다이제스트가
    #     피드에서 통째로 빠지는 문제라 posts 와 동일 규칙(파일명 앞 10자=날짜, _post_meta)으로 병합한다.
    daily_dated=[p for p in glob.glob(f"{SITE}/daily/*.html") if os.path.basename(p)!="latest.html"]
    feed_srcs=sorted(posts+daily_dated, key=lambda p:_post_meta(p)[0], reverse=True)
    items=""
    for p in feed_srcs[:FEED_MAX]:
        is_daily=p in daily_dated
        d,t,desc=_post_meta(p)
        if is_daily:
            desc=t   # 다이제스트는 meta description 대신 제목을 그대로 재사용
        rel="daily" if is_daily else "posts"
        link=f"{BASE_URL}/{rel}/{quote(os.path.basename(p))}"
        pub=format_datetime(datetime.fromisoformat(d).replace(hour=7,minute=5,tzinfo=KST))
        items+=(f"<item><title>{html.escape(t)}</title><link>{link}</link>"
                f"<description>{html.escape(desc)}</description>"
                f"<pubDate>{pub}</pubDate><guid isPermaLink=\"true\">{link}</guid></item>")
    now=format_datetime(datetime.now(KST))
    open(f"{SITE}/feed.xml","w").write(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>서울 부동산 데이터 스냅샷</title><link>{BASE_URL}/</link>"
        f"<description>서울 자치구 아파트 단지의 국토부 공공 실거래 중위·분포·추세(단지 실명, 자체 점수·순위 없음). 방법론 공개. 투자자문 아님.</description>"
        f"<language>ko</language><lastBuildDate>{now}</lastBuildDate>{items}</channel></rss>")
    # 4) robots.txt (AI 크롤러 명시 허용) + ai.txt(사용정책)
    open(f"{SITE}/robots.txt","w").write(
        "User-agent: *\nAllow: /\n# AI crawlers explicitly allowed (educational; named public MOLIT transaction stats, no scores)\n"
        "User-agent: GPTBot\nAllow: /\nUser-agent: ClaudeBot\nAllow: /\nUser-agent: Google-Extended\nAllow: /\n"
        f"User-agent: PerplexityBot\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    open(f"{SITE}/ai.txt","w").write(
        "# AI usage policy\nlicense: CC-BY-NC-4.0\nattribution: required\n"
        "content: named public MOLIT transaction medians & distributions (no scores, no private prices)\n"
        "training: allowed (non-commercial, with attribution)\nprovenance: per-post claims.jsonl\n")
    return {"posts":len(posts),"site":SITE,"complex":complex_count}

if __name__=="__main__":
    from agent_realestate import config
    config.load_env_file()   # RE_EMAIL_TO(takedown 연락처) — standalone 실행 시에도 placeholder 방지
    print(build())
