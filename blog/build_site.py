"""정적사이트 조립 — 생성된 일일 포스트 + 랜딩·sitemap·robots·ai.txt·RSS 를 묶어 배포가능 site/ 생성.
GitHub Pages 용. AI 친화: /llms.txt(root) + robots.txt(AI 크롤러 허용) + 각 포스트 JSON-LD + claims.jsonl.
"""
from __future__ import annotations
import os, shutil, glob, re, html
from datetime import date, datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import quote

SITE="site"; SRC="report/blog"
# 네이버 서치어드바이저 RSS/sitemap 은 절대 URL 필수 (searchadvisor.naver.com/guide/request-feed)
BASE_URL=os.environ.get("BLOG_BASE_URL","https://hexisteme.github.io/seoul-re-snapshot").rstrip("/")
KST=timezone(timedelta(hours=9))
FEED_MAX=50

def _post_meta(p):
    """포스트 파일에서 (date, title, description) 추출 — 파일명 YYYY-MM-DD-구.html 규약."""
    nm=os.path.basename(p); d=nm[:10]
    txt=open(p).read()
    t=re.search(r"<title>(.*?)</title>",txt,re.S)
    desc=re.search(r'<meta name=description content="(.*?)">',txt)
    return d,(t.group(1).strip() if t else nm[:-5]),(desc.group(1) if desc else "")

def build(today=None):
    today=today or date.today().isoformat()
    os.makedirs(f"{SITE}/posts",exist_ok=True)
    # 1) 포스트·claims·llms.txt 복사
    for f in glob.glob(f"{SRC}/posts/*"): shutil.copy(f,f"{SITE}/posts/")
    if os.path.exists(f"{SRC}/llms.txt"): shutil.copy(f"{SRC}/llms.txt",f"{SITE}/llms.txt")
    # 탐색기(방문자 필터형, 2026-06-16) — dataset.json + explorer.html 를 site/ 루트로 복사.
    #   posts/ 밖이라 sitemap/RSS 의 posts/*.html glob 에 안 걸려 자연 제외(JS 렌더=색인부적합, SEO 본체는 정적 포스트).
    for f in ("dataset.json","explorer.html"):
        if os.path.exists(f"{SRC}/{f}"): shutil.copy(f"{SRC}/{f}",f"{SITE}/{f}")
    posts=sorted(glob.glob(f"{SITE}/posts/*.html"),reverse=True)
    # 2) 랜딩 index.html
    items=""
    for p in posts:
        nm=os.path.basename(p); title=nm[:-5]
        items+=f'<li><a href="posts/{nm}">{title}</a> · <a href="posts/{title}.claims.jsonl">claims.jsonl</a></li>\n'
    idx=f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name="google-site-verification" content="mawCVnPZxYdhhtBgHlck2zvNYTTb7ydP6hg58_kBVCs">
<title>서울 부동산 데이터 스냅샷 — 개인 연구</title>
<meta name=description content="서울 자치구 아파트 단지의 국토부 공공 실거래 중위·분포·추세(단지 실명, 자체 점수·순위 없음). 방법론 공개. 투자자문 아님.">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebSite","name":"서울 부동산 데이터 스냅샷","inLanguage":"ko","license":"https://creativecommons.org/licenses/by-nc/4.0/","description":"국토부 공공 실거래 중위·분포·추세(단지 실명, 자체 점수·순위 없음)."}}</script>
<style>body{{font:16px/1.7 -apple-system,Pretendard,sans-serif;max-width:760px;margin:0 auto;padding:28px;color:#1a1a1a}}a{{color:#0969da}}li{{margin:4px 0}}.d{{font-size:13px;color:#666;border-top:1px solid #ddd;margin-top:24px;padding-top:12px}}</style>
</head><body>
<h1>서울 부동산 데이터 스냅샷</h1>
<p>서울 자치구 아파트 단지의 <b>국토부 공공 실거래 중위·분포·추세</b>(단지 실명 게재 — 공공 실거래 named 게재는 합법). 자체 평가·점수·순위 없는 <b>사실 스냅샷</b>. 방법론 공개.</p>
<p class=d style="border:0">⚖ 개인 연구·정보 공유이며 투자자문·매수권유 아님. 부동산은 자본시장법 금융투자상품이 아님. 수치는 게시 시점 기준 — 거래 전 원출처 재확인.</p>
<p style="font-size:17px"><a href="explorer.html"><b>🔎 탐색기 — 내 기준으로 필터</b></a> <span style="color:#666;font-size:13px">예산·평형·연식·유형으로 단지를 필터하고 공공 실거래로 정렬</span></p>
<p><a href="methodology.html">방법론 — 왜 이 숫자를 믿을 수 있나</a></p>
<h2>최근 포스트</h2><ul>{items}</ul>
<div class=d>방법론: 국토부 RTMS 12개월 동일평형 실거래 중위·분포(P25–P75)·추세·52주 위치(이상치 −40%컷). 자체 평가·점수·순위 없음. 사설 호가·민간시세는 사용·게재하지 않습니다. AI 인덱스: <a href="llms.txt">/llms.txt</a> · 라이선스 CC-BY-NC-4.0.</div>
</body></html>"""
    open(f"{SITE}/index.html","w").write(idx)
    # 2b) 방법론 고정 페이지 — 매 포스트가 링크하는 "왜 이 숫자를 믿을 수 있나" 앵커 (슈퍼샘플, 2026-06-11)
    open(f"{SITE}/methodology.html","w").write(f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>방법론 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="국토부 공공 실거래 중위·분포·추세·52주 위치의 측정·출처·신뢰규율·한계. 자체 점수 없음, 모든 수치 provenance 동봉.">
<style>body{{font:15px/1.7 -apple-system,Pretendard,sans-serif;max-width:760px;margin:0 auto;padding:28px;color:#1a1a1a}}a{{color:#0969da}}h2{{font-size:17px;margin-top:26px}}.d{{font-size:13px;color:#666}}</style>
</head><body>
<h1>방법론</h1>
<p><a href="./">← 목록</a></p>
<h2>1. 무엇을 측정하나</h2>
<p>자체 점수·순위·등급·평가는 <b>일절 산출하지 않는다</b>. 측정 대상은 단지별 <b>국토부 공공 실거래 사실 통계</b>:
동일평형(전용 ±3.5㎡)·이름매칭으로 묶은 12개월 실거래에서 ① <b>중위(억)</b>와 표본수 n,
② <b>분포</b>(P25–중위–P75 협상 레인지), ③ <b>추세</b>(최근 3개월 중위 vs 직전 9개월 중위의 방향·변화%),
④ <b>52주 위치</b>(최근 3개월 체결 중위가 12개월(52주) 실거래 최저~최고 레인지에서 차지하는 위치 % — 헤드라인 중위와 기준점이 다름). 모두 관측된 사실값이다.
세대수 200세대 미만 단지와 환금성 우려가 큰 일부 corridor 단지는 게재 전 제외한다.</p>
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
    open(f"{SITE}/sitemap.xml","w").write(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{BASE_URL}/</loc><lastmod>{today}</lastmod></url>{urls}</urlset>')
    # 3b) feed.xml — RSS 2.0 (네이버 서치어드바이저 요구 item 필드: title/link/description/pubDate/guid)
    items=""
    for p in posts[:FEED_MAX]:
        d,t,desc=_post_meta(p); link=f"{BASE_URL}/posts/{quote(os.path.basename(p))}"
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
    return {"posts":len(posts),"site":SITE}

if __name__=="__main__":
    print(build())
