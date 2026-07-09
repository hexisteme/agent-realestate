"""티스토리 반자동 발행 원고 생성 — 완성 원고 + 복사버튼 헬퍼 페이지.

경로 판정(IntelReport 2026-06-10): 티스토리 무인 자동발행은 불가(Open API 2024-02 종료
+ 카카오 로그인 캡차/기기인증으로 브라우저 자동화도 무인화 실패, 3+ 독립소스 교차).
→ 채택 패턴 = "원고 생성까지 자동, 사람은 등록만": 본 모듈이 붙여넣기 완성 원고(HTML)와
제목·태그를 복사버튼 헬퍼 페이지로 산출, 사용자는 티스토리 에디터(HTML 모드)에
붙여넣고 [발행] 만 누른다. 계정정지·약관 리스크 0 (사람이 직접 발행).

법적 가드는 build_explorer 와 *동일 데이터*(실명 사실 rows·공공 실거래·면책)를 그대로 사용
— 본 모듈은 표현만 바꾸고 어떤 값도 추가 가공하지 않는다.
구 9개 = 일일 통합 1포스트 (하루 붙여넣기 1회로 운영 부담 최소화).
"""
from __future__ import annotations
import os, html
import blog.build_explorer as be
from blog.build_site import BASE_URL

TISTORY_TAGS = "서울아파트,실거래가,부동산데이터,아파트시세,국토부실거래"
_TBL = "width:100%;border-collapse:collapse;font-size:13px;margin:10px 0"
_TH = "border:1px solid #ddd;padding:6px 8px;background:#f6f8fa;text-align:left"
_TD = "border:1px solid #ddd;padding:6px 8px;text-align:left"
_MUT = "font-size:12px;color:#666"


def build_tistory_section(gu: str, rows: list[dict]) -> str:
    """구 1개 섹션 HTML 조각 (실명 사실) — 카카오 에디터 paste 정규화 생존 요소만 사용.

    (2026-07-07 모바일 재설계 — 발행본 /16 실측 근거) 카카오 에디터는 paste 시:
      · <th> 행을 표 밖 <p> 텍스트로 방출("단지명대지지분층…" 뭉침의 원인)
      · <details>/<summary>·<sup>·사용자 스타일(padding·font-size·overflow-x·min-width) 제거
      · 표는 width:100% border=1 로 강제 — 모바일 스킨에 가로스크롤 없음 → 다열 표는 글자 세로쪼개짐
      · 생존: p·b·br·a·span(color)·td·table
    → 메인 표 7→4컬럼(셀 내 <br> 적층), 헤더는 <td><b>, 22컬럼 상세표는 폐지하고
      단지별 팩트라인(<p>, 확인된 항목만 라벨·값 나열)로 대체. 데이터·가드는 종전과 동일."""
    bluf = (f"{gu} {len(rows)}개 단지(세대수 200+ · 안전제외 반영)의 공공 실거래·단지정보. "
            f"국토부 RTMS 12개월 동일평형 중위. 자체 평가·점수 없음 — 공개된 사실 수치만.")
    srt = sorted(rows, key=lambda x: (x["molit_recent_eok"] is None, -(x["molit_recent_eok"] or 0)))
    head = (f'<tr><td style="{_TH}"><b>단지</b></td><td style="{_TH}"><b>스펙</b></td>'
            f'<td style="{_TH}"><b>실거래(중위)ᶠ</b></td><td style="{_TH}"><b>평단가</b></td></tr>')
    trs = ""
    for r in srt:
        if r["molit_recent_eok"] is not None:
            px = f'<b>{r["molit_recent_eok"]}억</b>ᶠ <span style="{_MUT}">n{r["molit_n"]}</span>'
            if r.get("molit_p25_eok") is not None:   # 분포 병기 — P25–P75 협상 레인지(중위는 헤드라인)
                px += f'<br><span style="{_MUT}">P25–75: {r["molit_p25_eok"]}–{r["molit_p75_eok"]}억</span>'
        else:
            px = f'<span style="{_MUT}">실거래 없음</span>'
        tf = be._tier_facts(r)                       # 추세·52주위치(무점수 사실)
        if tf:
            px += f'<br><span style="{_MUT}">{" · ".join(tf)}ᶠ</span>'
        pp = f'{r["pyeong_price_man"]:,}만' if r["pyeong_price_man"] is not None else "—"
        nm = f'<b>{html.escape(r["name"])}</b><br><span style="{_MUT}">{html.escape(r["saeng"])}</span>'
        spec = (f'{r["area_m2"]}㎡({r["pyeong"]}평)<br>'
                f'<span style="{_MUT}">{r["units_band"]}·{r["decade"]}·{r["product_type"]}</span>')
        trs += (f'<tr><td style="{_TD}">{nm}</td><td style="{_TD}">{spec}</td>'
                f'<td style="{_TD}">{px}</td><td style="{_TD}">{pp}</td></tr>')
    # ── 단지 상세 = 팩트라인(표 아님) — 확인된 항목만 표기, 미확인 항목은 생략 ──
    infra_ps = ""
    for r in srt:
        facts: list[str] = []
        if r.get("land_share_pyeong"):
            facts.append(f'대지 {r["land_share_pyeong"]}평')
        if r.get("floor") and str(r["floor"]).strip() not in ("-", "—", "–"):
            facts.append(f'층 {html.escape(str(r["floor"]))}')
        if r.get("subway_m") is not None:
            facts.append(f'지하철 {r["subway_m"]:,}m')
        if r.get("cbd_km") is not None:
            facts.append(f'{html.escape(r.get("cbd_name", ""))} {r["cbd_km"]}km')
        if r.get("slope_pct") is not None:
            facts.append(f'경사 {r["slope_pct"]}%')
        if r.get("far_pct") and r.get("bcr_pct") is not None:
            facts.append(f'용적/건폐 {r["far_pct"]}/{r["bcr_pct"]}%')
        elif r.get("far_pct"):
            facts.append(f'용적률 {r["far_pct"]}%')
        if r.get("tukmokgo_pct") is not None:
            facts.append(f'특목고 {r["tukmokgo_pct"]}%')
        if r.get("school_achievement") is not None:
            facts.append(f'학업성취 {r["school_achievement"]}%')
        if r.get("gu_jeonse_ratio_pct") is not None:
            facts.append(f'전세가율 {r["gu_jeonse_ratio_pct"]}%')
        if r.get("trade_annual") is not None:
            facts.append(f'연거래 {r["trade_annual"]}건')
        if r.get("academy_exam") is not None:
            facts.append(f'학원가 {r["academy_exam"]}곳')
        if r.get("review_score") is not None:
            facts.append(f'주민평점 {r["review_score"]}/5')
        conv = "·".join(filter(None, [
            f'마트{r["mart_800"]}' if r.get("mart_800") is not None else None,
            f'병원{r["hosp_800"]}' if r.get("hosp_800") is not None else None,
            f'공원{r["park_1k"]}' if r.get("park_1k") is not None else None,
            f'백화점{r["dept_1500"]}' if r.get("dept_1500") is not None else None,
        ]))
        if conv:
            facts.append(conv)
        if r.get("heating"):
            facts.append(html.escape(r["heating"]))
        if r.get("corridor_type"):
            facts.append(html.escape(r["corridor_type"]))
        if r.get("parking_per_unit") is not None:
            facts.append(f'주차 {r["parking_per_unit"]}대/세대')
        if r.get("builder"):
            facts.append(f'시공 {html.escape(r["builder"])}')
        if r.get("nearest_elem_school"):
            facts.append(f'초등 {html.escape(r["nearest_elem_school"])}')
        if r.get("gongsi_man") is not None:
            facts.append(f'공시가 {r["gongsi_man"]:,}만')
        # 1만원 미만은 K-apt 부분응답 의심(관리비 0만원/월 오표기 방지) — 미표기. round 는 탐색기와 동일.
        if r.get("maint_fee_won") and r["maint_fee_won"] >= 10000:
            facts.append(f'관리비 {round(r["maint_fee_won"] / 10000)}만원/월')
        tail = f'<span style="{_MUT}">{" · ".join(facts)}</span>' if facts else ""
        if r.get("complex_no"):
            link = (f'<a href="https://m.land.naver.com/complex/info/{r["complex_no"]}" '
                    f'target="_blank">네이버 매물</a>')
            tail = f"{tail} · {link}" if tail else link
        infra_ps += (f'<p style="margin:10px 0"><b>{html.escape(r["name"])}</b>'
                     f'{" — " + tail if tail else ""}</p>')
    detail_head = (f'<p style="margin:14px 0 2px"><b>▸ {gu} 단지 상세</b> '
                   f'<span style="{_MUT}">(대지지분·층·지하철·업무지구·경사도·용적률·학군·전세가율·'
                   f'생활편의·난방·복도·주차·시공사·인근초등·공시가·관리비 — 확인된 항목만 표기)</span></p>')
    return (f"<h2>{gu}</h2><p>{bluf}</p>"
            f'<table style="{_TBL}">{head}{trs}</table>'
            + detail_head + infra_ps)


def build_daily_body(sections: list[str], today: str, data_asof: str) -> str:
    """티스토리 본문(HTML 모드 붙여넣기용) — 일일 통합 1포스트."""
    method = (f'<hr><p style="{_MUT}"><b>방법론·출처</b><br>'
              "• 실거래 = 국토교통부 RTMS 공공데이터(12개월 동일평형 중위), 매일 자동 재수집. 세대수·연식·전용면적·유형 = 공개정보.<br>"
              "• 자체 평가·점수·순위를 매기지 않습니다. 사설 시세(호가)는 게재하지 않으며 공공 실거래가만 표기.<br>"
              f"• 출처표기: ᶠ=국토부 실거래 사실 · n=표본수 · 평단가=실거래÷평형. 데이터 기준일 {data_asof}.<br>"
              "• P25–75=동일평형 실거래 분위수(협상 레인지, 중위는 헤드라인 굵은 값). 추세=최근3개월 중위 vs 직전9개월 중위(과거 비교 사실 — 전망 아님). 52주=<b>최근 3개월 체결 중위</b>가 12개월(52주) 실거래 최저~최고 레인지 내 위치(%) — 헤드라인 중위(12개월)와 기준점 다름(최근 거래 없으면 —). 표본 부족 항목은 —.<br>"
              f'• 원문·머신리더블 데이터(JSON-LD·claims.jsonl)·방문자 탐색기: <a href="{BASE_URL}/">{BASE_URL}/</a> · '
              f'<a href="{BASE_URL}/methodology.html">방법론 전문</a> · '
              f'코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a> · 라이선스 CC-BY-NC-4.0.</p>'
              f'<p style="{_MUT}">⚖ {be.DISCLAIMER} {be._takedown()}</p>')
    explorer_link = (f'<p style="background:#f0f7ff;border-left:4px solid #0969da;padding:10px 14px;margin:12px 0;font-size:14px">'
                     f'🔍 <b>지역·세대수·평단가로 직접 필터해보기</b> → '
                     f'<a href="{BASE_URL}/explorer.html" target="_blank" style="color:#0969da">'
                     f'{BASE_URL}/explorer.html</a>'
                     f' <span style="font-size:12px;color:#667">(전체 단지 필터·정렬 탐색기 — 경사도·지하철·학원가·네이버 매물 포함)</span></p>')
    intro = (f"<p>서울 자치구 아파트의 <b>국토부 공공 실거래가 + 단지정보</b>(세대수·연식·평형) 스냅샷입니다 "
             f"({today} 기준, {len(sections)}개 구). 자체 평가·점수 없이 공개된 사실 수치만 제공합니다. 투자자문이 아닙니다.</p>"
             + explorer_link)
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
