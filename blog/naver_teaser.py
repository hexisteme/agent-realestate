"""네이버 블로그 안내용 티저 생성 — 150-200자 요약 + 티스토리 전문 링크.

네이버 블로그 = 티스토리 안내(guide) 채널:
- 짧은 데이터 티저 (날짜·구수·상위 구조점수·대표 실거래대)
- "전체 분석 보러가기 →" 티스토리 링크 (발행 후 URL 입력 → 동적 반영)
- 해시태그 (네이버 검색 노출용)

설정: RE_TISTORY_BLOG_URL 환경변수에 블로그 루트 URL 입력
      (예: export RE_TISTORY_BLOG_URL=https://hexisteme.tistory.com).
운영: ① 티스토리 draft 발행 → URL 확인 → ② 헬퍼 페이지에서 URL 입력 후 텍스트 복사
      → ③ 네이버 블로그 새 글 → 붙여넣기 → 발행 (3분 작업).
출력: report/blog/naver/{today}-naver-teaser.html
"""
from __future__ import annotations
import html, os

NAVER_TAGS = "#서울아파트 #부동산데이터 #아파트실거래가 #국토부실거래 #서울부동산"
TISTORY_BASE = os.environ.get("RE_TISTORY_BLOG_URL", "https://YOUR_BLOG.tistory.com")
_PH = "[TISTORY_URL]"   # placeholder — JS 가 실제 URL 로 치환


def build_teaser_text(today: str, gu_results: list[dict], tistory_url: str = "") -> str:
    """150-200자 네이버 블로그 본문 텍스트. tistory_url 미입력 시 placeholder 사용.

    gu_results 각 항목에서 사용하는 키:
      n      : 구 내 단지 수
      gu     : 구 이름
      top_eok : 구 내 최고 공공 실거래(억, float)
    """
    active = [r for r in gu_results if not r.get("skipped")]
    n_gu = len(active)
    n_complex = sum(r.get("n", 0) for r in active)
    top = max((r for r in active if r.get("top_eok")), key=lambda r: r.get("top_eok") or 0, default=None)

    url = tistory_url or _PH
    lines = [
        f"[{today}] 서울 아파트 국토부 공공 실거래 + 단지정보 스냅샷 📊",
        f"{n_gu}개 구 {n_complex}개 단지(세대수 200+ · 안전제외 반영).",
    ]
    if top:
        lines.append(f"실거래 상위: {top['gu']} {top['top_eok']}억대.")
    lines += [
        "자체 평가·점수 없이 공개된 사실 수치만. 투자자문 아님.",
        "",
        f"👉 전체 분석:\n{url}",
        "",
        NAVER_TAGS,
    ]
    return "\n".join(lines)


def write_naver_teaser(
    gu_results: list[dict],
    today: str,
    data_asof: str,
    tistory_url: str = "",
    outdir: str = "report/blog",
) -> str:
    """복사 헬퍼 HTML 생성 → 경로 반환.

    헬퍼 페이지 사용법:
      1. 브라우저로 열기
      2. 티스토리 URL 입력 → 미리보기 자동 갱신
      3. '본문 복사' → 네이버 블로그 에디터에 붙여넣기
      4. 제목·태그도 복사 → 각 입력란 붙여넣기
      5. 발행 클릭
    """
    base_text = build_teaser_text(today, gu_results, "")  # placeholder 포함
    title = f"서울 아파트 실거래 데이터 스냅샷 — {today}"
    ph_escaped = html.escape(_PH)

    helper = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>네이버 블로그 티저 {today}</title>
<style>
body{{font:15px/1.6 -apple-system,Pretendard,sans-serif;max-width:720px;margin:0 auto;padding:24px}}
textarea{{width:100%;font:13px/1.5 ui-monospace,monospace;border:1px solid #ddd;border-radius:6px;padding:8px}}
input[type=url]{{width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:14px;box-sizing:border-box}}
button{{margin:4px 0 14px;padding:6px 14px;border:1px solid #0969da;background:#0969da;color:#fff;border-radius:6px;cursor:pointer}}
button.ok{{background:#1a7f37;border-color:#1a7f37}}
.box{{background:#f6f8fa;border:1px solid #ddd;border-radius:8px;padding:12px 16px;font-size:13px}}
.hint{{font-size:12px;color:#666;margin-top:4px}}
label{{font-weight:600;display:block;margin-bottom:4px}}
</style></head><body>
<h1>네이버 블로그 티저 <small>{today}</small></h1>
<div class=box><b>등록 절차 (3복사 + 발행 1클릭)</b><ol style="margin:6px 0">
<li>티스토리 원고 발행 후 생성된 <b>포스트 URL 입력</b> → 본문 텍스트에 자동 반영</li>
<li><b>본문·제목·태그 순서로 복사</b></li>
<li>네이버 블로그 → 글쓰기 → 각 입력란 붙여넣기 → <b>발행</b></li></ol>
<p class=hint>티스토리 URL 예: https://hexisteme.tistory.com/42</p></div>

<h3>티스토리 포스트 URL</h3>
<input type=url id=url placeholder="{html.escape(TISTORY_BASE)}/42"
  oninput="updateBody()" value="{html.escape(tistory_url)}">

<h3>제목</h3>
<textarea id=ttl rows=1 readonly>{html.escape(title)}</textarea>
<button onclick="cp('ttl',this)">제목 복사</button>

<h3>태그</h3>
<textarea id=tags rows=1 readonly>{html.escape(NAVER_TAGS)}</textarea>
<button onclick="cp('tags',this)">태그 복사</button>

<h3>본문 텍스트</h3>
<textarea id=body rows=10 readonly>{html.escape(base_text)}</textarea>
<button onclick="cp('body',this)">본문 복사</button>
<p class=hint>※ 네이버 블로그 에디터에 일반 텍스트로 붙여넣기. 약 {len(base_text.replace(_PH, TISTORY_BASE))}자</p>

<script>
const BASE_TEXT = {repr(base_text)};
const PH = "{ph_escaped}";
function updateBody() {{
  const url = document.getElementById('url').value.trim() || PH;
  document.getElementById('body').value = BASE_TEXT.replace(PH, url);
}}
function cp(id, btn) {{
  navigator.clipboard.writeText(document.getElementById(id).value)
    .then(() => {{ btn.textContent = '복사됨 ✓'; btn.className = 'ok'; }});
}}
updateBody();
</script>
</body></html>"""

    os.makedirs(f"{outdir}/naver", exist_ok=True)
    path = f"{outdir}/naver/{today}-naver-teaser.html"
    open(path, "w").write(helper)
    return path
