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
<meta name=description content="서울 자치구 단지 자체 결정론 구조점수 + 국토부 공공 실거래 band. 익명·통계·방법론 공개. 투자자문 아님.">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebSite","name":"서울 부동산 데이터 스냅샷","inLanguage":"ko","license":"https://creativecommons.org/licenses/by-nc/4.0/","description":"자체 결정론 구조점수 + 공공 실거래. 익명·통계."}}</script>
<style>body{{font:16px/1.7 -apple-system,Pretendard,sans-serif;max-width:760px;margin:0 auto;padding:28px;color:#1a1a1a}}a{{color:#0969da}}li{{margin:4px 0}}.d{{font-size:13px;color:#666;border-top:1px solid #ddd;margin-top:24px;padding-top:12px}}</style>
</head><body>
<h1>서울 부동산 데이터 스냅샷</h1>
<p>서울 자치구 단지의 <b>자체 결정론 10축 구조점수</b>(호가무관) + 국토부 공공 실거래 band. 단지명 익명·통계·방법론 공개.</p>
<p class=d style="border:0">⚖ 개인 연구·정보 공유이며 투자자문·매수권유 아님. 부동산은 자본시장법 금융투자상품이 아님. 수치는 게시 시점 기준 — 거래 전 원출처 재확인.</p>
<p><a href="methodology.html">방법론 — 왜 이 숫자를 믿을 수 있나</a></p>
<h2>최근 포스트</h2><ul>{items}</ul>
<div class=d>방법론: 결정론 파이프라인(LLM 재계산 0)·호가무관 fundamental 랭킹·국토부 RTMS 12개월 중위(이상치 −40%컷). 사설 시세 원본은 DB권 보호로 미재게시(band만). AI 인덱스: <a href="llms.txt">/llms.txt</a> · 라이선스 CC-BY-NC-4.0.</div>
</body></html>"""
    open(f"{SITE}/index.html","w").write(idx)
    # 2b) 방법론 고정 페이지 — 매 포스트가 링크하는 "왜 이 숫자를 믿을 수 있나" 앵커 (슈퍼샘플, 2026-06-11)
    open(f"{SITE}/methodology.html","w").write(f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>방법론 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="자체 결정론 10축 구조점수의 산식·출처·한계. LLM 재계산 없음, 모든 수치 provenance 동봉.">
<style>body{{font:15px/1.7 -apple-system,Pretendard,sans-serif;max-width:760px;margin:0 auto;padding:28px;color:#1a1a1a}}a{{color:#0969da}}h2{{font-size:17px;margin-top:26px}}.d{{font-size:13px;color:#666}}</style>
</head><body>
<h1>방법론</h1>
<p><a href="./">← 목록</a></p>
<h2>1. 무엇을 측정하나</h2>
<p>단지의 <b>구조점수</b> = 호가와 무관한 fundamental 10축(전세수요·환금성·가격방어·상승여력·토지지분·출퇴근·학군·경사·후기 등)의
가중 종합(0~5). 가격 축(가격메리트·전세수요)은 랭킹에서 분리 — "많이 빠져서 싸 보임 → 상위" 누수를 차단한다.
점수는 <b>같은 비교셋 안의 상대 적합도</b>이며 미래 수익률 예측이 아니다.</p>
<h2>2. 데이터 출처</h2>
<p>실거래 = 국토교통부 RTMS 공공데이터(12개월 동일평형 중위, 이상치 −40% 컷), 매일 자동 재수집.
네이버·KB 등 사설 시세 원본은 DB권 보호를 위해 <b>재게시하지 않으며</b> coarse band(○억대 초/중/후반)로만 표기.
단지명은 익명 식별자(구-생활권-순번) — 개별 단지 가치판정을 공개하지 않기 위함.</p>
<h2>3. 신뢰 규율</h2>
<p>모든 수치는 결정론 파이프라인(LLM 재계산 0)에서 나오며 <sup>F</sup>(사실)/<sup>I</sup>(추론) 라벨과 출처·확인일을 동반한다.
각 포스트에 머신리더블 provenance(<code>claims.jsonl</code>, 1행 1주장)와 JSON-LD Dataset 이 동봉된다.
동일 입력 → 동일 산출이 테스트(129)로 고정되고, 생성 전 커버리지·위생 게이트가 공백·중복을 차단한다.</p>
<h2>4. 한계 (정직 고지)</h2>
<p>구조점수는 순서형 휴리스틱이지 실측 보정(calibrated)된 객관 측정이 아니다. 발견 커버리지는 전수가 아니다.
데이터는 기준일 시점이며, 본 사이트는 개인 연구·정보 공유로 <b>투자자문·매수권유가 아니다</b>.</p>
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
        f"<description>서울 자치구 단지 자체 결정론 구조점수 + 국토부 공공 실거래 band. 익명·통계·방법론 공개. 투자자문 아님.</description>"
        f"<language>ko</language><lastBuildDate>{now}</lastBuildDate>{items}</channel></rss>")
    # 4) robots.txt (AI 크롤러 명시 허용) + ai.txt(사용정책)
    open(f"{SITE}/robots.txt","w").write(
        "User-agent: *\nAllow: /\n# AI crawlers explicitly allowed (educational, anonymized stats)\n"
        "User-agent: GPTBot\nAllow: /\nUser-agent: ClaudeBot\nAllow: /\nUser-agent: Google-Extended\nAllow: /\n"
        f"User-agent: PerplexityBot\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    open(f"{SITE}/ai.txt","w").write(
        "# AI usage policy\nlicense: CC-BY-NC-4.0\nattribution: required\n"
        "content: anonymized real-estate structure scores + public MOLIT transaction bands (no private price republication)\n"
        "training: allowed (non-commercial, with attribution)\nprovenance: per-post claims.jsonl\n")
    return {"posts":len(posts),"site":SITE}

if __name__=="__main__":
    print(build())
