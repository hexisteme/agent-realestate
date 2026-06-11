"""티스토리 반자동 발행 원고 생성 — 완성 원고 + 복사버튼 헬퍼 페이지.

경로 판정(IntelReport 2026-06-10): 티스토리 무인 자동발행은 불가(Open API 2024-02 종료
+ 카카오 로그인 캡차/기기인증으로 브라우저 자동화도 무인화 실패, 3+ 독립소스 교차).
→ 채택 패턴 = "원고 생성까지 자동, 사람은 등록만": 본 모듈이 붙여넣기 완성 원고(HTML)와
제목·태그를 복사버튼 헬퍼 페이지로 산출, 사용자는 티스토리 에디터(HTML 모드)에
붙여넣고 [발행] 만 누른다. 계정정지·약관 리스크 0 (사람이 직접 발행).

법적 가드는 daily_publisher 와 *동일 데이터*(익명화 rows·coarse band·면책)를 그대로 사용
— 본 모듈은 표현만 바꾸고 어떤 값도 추가 가공하지 않는다.
구 9개 = 일일 통합 1포스트 (하루 붙여넣기 1회로 운영 부담 최소화).
"""
from __future__ import annotations
import os, html, statistics as st
import blog.daily_publisher as dp
from blog.build_site import BASE_URL

TISTORY_TAGS = "서울아파트,실거래가,부동산데이터,아파트시세,국토부실거래"
_TBL = "width:100%;border-collapse:collapse;font-size:13px;margin:10px 0"
_TH = "border:1px solid #ddd;padding:6px 8px;background:#f6f8fa;text-align:left"
_TD = "border:1px solid #ddd;padding:6px 8px;text-align:left"
_MUT = "font-size:12px;color:#666"


def build_tistory_section(gu: str, rows: list[dict]) -> str:
    """구 1개 섹션 HTML 조각 — 티스토리 에디터가 <style> 블록을 보존하지 않으므로 전부 인라인 스타일."""
    funds = [r["fund"] for r in rows]
    bluf = (f"{gu} {len(rows)}개 단지(세대수≥200) 데이터 스냅샷. 자체 구조점수(호가무관) 중위 {st.median(funds):.2f}/5, "
            f"최고 {max(funds):.2f}. 실거래는 국토부 공공데이터 기준 coarse band 표기.")
    trs = ""
    for r in sorted(rows, key=lambda x: -x["fund"]):
        trs += (f'<tr><td style="{_TD}">{r["id"]}</td><td style="{_TD}">전용{r["area"]}㎡</td>'
                f'<td style="{_TD}">{dp._unit_band(r["units"])}·{dp._decade(r["built"])}</td>'
                f'<td style="{_TD}"><b>{r["fund"]:.2f}</b></td><td style="{_TD}">{"·".join(r["strong"])}</td>'
                f'<td style="{_TD}">{dp._eok_band(r["eff_eok"])} <sup>{r["prov_eff"]}</sup></td></tr>')
    return (f"<h2>{gu}</h2><p>{bluf}</p>"
            f'<table style="{_TBL}"><tr><th style="{_TH}">식별자</th><th style="{_TH}">평형</th>'
            f'<th style="{_TH}">규모·연식</th><th style="{_TH}">구조점수</th><th style="{_TH}">강점축</th>'
            f'<th style="{_TH}">실거래대</th></tr>{trs}</table>')


def build_daily_body(sections: list[str], today: str, data_asof: str) -> str:
    """티스토리 본문(HTML 모드 붙여넣기용) — 일일 통합 1포스트."""
    method = (f'<hr><p style="{_MUT}"><b>방법론·한계</b><br>'
              "• 단지명은 익명 식별자(구-생활권-순번). 자체 10축 구조점수(호가무관 fundamental)는 결정론 계산.<br>"
              "• 실거래는 국토부 RTMS 공공데이터 12개월 동일평형 중위(이상치 −40% 컷). "
              "네이버·KB 등 사설 시세 원본은 DB권 보호로 재게시하지 않으며 band(대역)로만 표기.<br>"
              f"• 출처표기: <sup>F</sup>=사실(국토부 공공데이터) · <sup>I</sup>=추론(표본·가공). 데이터 기준일 {data_asof}.<br>"
              f'• 원문·머신리더블 데이터(JSON-LD·claims.jsonl): <a href="{BASE_URL}/">{BASE_URL}/</a> · '
              f'<a href="{BASE_URL}/methodology.html">방법론 전문</a> · '
              f'코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a> · 라이선스 CC-BY-NC-4.0.</p>'
              f'<p style="{_MUT}">⚖ {dp.DISCLAIMER}</p>')
    intro = (f"<p>서울 자치구 단지의 <b>자체 결정론 구조점수</b>(호가무관)와 국토부 공공 실거래 band 스냅샷입니다 "
             f"({today} 기준, {len(sections)}개 구). 단지명 익명·통계·방법론 공개. 투자자문이 아닙니다.</p>")
    return intro + "".join(sections) + method


def write_daily_draft(sections: list[str], today: str, data_asof: str, outdir: str = "report/blog") -> str:
    """복사버튼 헬퍼 페이지 산출 — 브라우저로 열어 제목/태그/본문 복사 → 티스토리 등록."""
    title = f"서울 아파트 실거래 데이터 스냅샷 — {today} ({len(sections)}개 구)"
    body = build_daily_body(sections, today, data_asof)
    helper = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>티스토리 발행 원고 {today}</title>
<style>body{{font:15px/1.6 -apple-system,Pretendard,sans-serif;max-width:900px;margin:0 auto;padding:24px}}
textarea{{width:100%;font:12px/1.5 ui-monospace,monospace;border:1px solid #ddd;border-radius:6px;padding:8px}}
button{{margin:4px 0 14px;padding:6px 14px;border:1px solid #0969da;background:#0969da;color:#fff;border-radius:6px;cursor:pointer}}
button.ok{{background:#1a7f37;border-color:#1a7f37}}
.box{{background:#f6f8fa;border:1px solid #ddd;border-radius:8px;padding:12px 16px;font-size:13px}}
details{{border:1px solid #ddd;border-radius:8px;padding:10px 16px;margin-top:14px}}</style></head><body>
<h1>티스토리 발행 원고 <small>{today}</small></h1>
<div class=box><b>등록 절차 (3복사 + 발행 1클릭)</b><ol style="margin:6px 0">
<li>티스토리 → 글쓰기 → 에디터 우상단 <b>기본모드 ▾ → HTML</b> 전환</li>
<li>아래 <b>본문 HTML 복사</b> → 에디터에 붙여넣기 (기본모드로 되돌리면 표 미리보기 확인 가능)</li>
<li><b>제목·태그 복사</b> → 각 입력란에 붙여넣기</li>
<li><b>발행</b>(공개) 클릭 — 끝</li></ol></div>
<h3>제목</h3><textarea id=t rows=1 readonly>{html.escape(title)}</textarea>
<button onclick="cp('t',this)">제목 복사</button>
<h3>태그</h3><textarea id=g rows=1 readonly>{html.escape(TISTORY_TAGS)}</textarea>
<button onclick="cp('g',this)">태그 복사</button>
<h3>본문 HTML</h3><textarea id=b rows=16 readonly>{html.escape(body)}</textarea>
<button onclick="cp('b',this)">본문 HTML 복사</button>
<details><summary>본문 미리보기</summary>{body}</details>
<script>function cp(id,btn){{navigator.clipboard.writeText(document.getElementById(id).value)
.then(()=>{{btn.textContent='복사됨 ✓';btn.className='ok';}});}}</script>
</body></html>"""
    os.makedirs(f"{outdir}/tistory", exist_ok=True)
    path = f"{outdir}/tistory/{today}-tistory-draft.html"
    open(path, "w").write(helper)
    return path
