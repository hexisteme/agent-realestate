"""이중목적 일일 포스트 생성기 — 인간 가독 + AI 인제스천(LLM-readable).

agent-council/intel 수렴 결과를 *코드로 강제*한다(가드를 관례 아닌 타입/로직으로):
  G-법적: 공개트랙은 ① 단지명 익명화(구-생활권-seq) ② 부정 가치판정 제외(통계·점수만)
          ③ 네이버/KB 원본 시세 *재게시 금지* — MOLIT 공공데이터 + 자체점수만, 시세는 coarse band
          ④ 모든 포스트 투자자문 면책 의무.
  G-신선도: data_asof 가 today-FRESH_DAYS 초과면 STALE 워터마크(박제 노후호가 차단).
  G-AI: 동일 콘텐츠 2표현 — 인간 HTML + 머신 레이어(JSON-LD Dataset + claims.jsonl + llms.txt).
근거: ConductionReport(허브 ledger #115) + IntelReport(2026-06-09). 자본시장법 투자자문업 비해당(부동산 제외),
      DB권(잡코리아 v 사람인 특허법원) — 원본 대량 재게시 금지.
"""
from __future__ import annotations
import json, re, statistics as st
from datetime import date
from pathlib import Path

FRESH_DAYS = 2  # 호가 스냅샷 신선도 임계(D-n)
DISCLAIMER = ("본 글은 개인 연구·정보 공유 목적이며 투자자문·매수권유가 아닙니다. "
              "부동산은 자본시장법상 금융투자상품이 아니어 투자자문업 규율 대상이 아니나, "
              "수치는 게시 시점 기준이며 실제 거래 전 반드시 원출처(국토부 실거래가·현장)에서 재확인하십시오.")
LICENSE = "CC-BY-NC-4.0"

SAENG_ABBR = {"길음":"GM","장위":"JW","상계":"SG","신길":"SK","화곡":"HG","등촌":"DC",
              "신도림":"SD","청량리":"CR","공덕":"GD","도심":"DS","마곡":"MG"}

def _eok_band(eok: float) -> str:
    """원본 시세 verbatim 재게시 회피 — coarse band(자체 가공)."""
    if eok is None: return "—"
    lo = int(eok); frac = eok - lo
    half = "초반" if frac < 0.34 else "중반" if frac < 0.67 else "후반"
    return f"{lo}억대 {half}"

def _decade(y: int) -> str:
    return f"{(y//10)*10}년대" if y else "—"

def _unit_band(u: int) -> str:
    return ("2천세대급" if u>=2000 else "1천세대급" if u>=1000 else
            "5백세대급" if u>=500 else "2백세대급" if u>=200 else "소규모")

def anonymize(gu: str, saeng: str, idx: int) -> str:
    sg = saeng.split("-")[-1] if saeng else ""
    ab = SAENG_ABBR.get(sg, (sg[:2] if sg else "X"))
    return f"{gu}-{ab}{idx:02d}"

def top_axes(scores: dict, k=2) -> list[str]:
    order = sorted(scores.items(), key=lambda x: -x[1])
    return [a for a,_ in order[:k]]

def build_post(gu: str, rows: list[dict], data_asof: str, today: str) -> dict:
    """rows: [{id, area, eff_eok, src, n, fund, strong, units, built, prov_eff}] (익명화·점수 완료, 부정판정 제외)
    반환: {html, jsonld(dict), claims(list), llms_line}"""
    stale = (date.fromisoformat(today) - date.fromisoformat(data_asof)).days > FRESH_DAYS
    fresh_badge = (f'<span class="badge stale">⚠ STALE · 데이터 {data_asof} (D-{(date.fromisoformat(today)-date.fromisoformat(data_asof)).days})</span>'
                   if stale else f'<span class="badge">데이터 {data_asof} · 신선</span>')
    funds = [r["fund"] for r in rows]
    bluf = (f"{gu} {len(rows)}개 단지(세대수≥200) 데이터 스냅샷. 자체 구조점수(호가무관) 중위 {st.median(funds):.2f}/5, "
            f"최고 {max(funds):.2f}. 실거래는 국토부 공공데이터 기준 coarse band 표기.")
    # ── 인간 레이어 ──
    trs = ""
    for r in sorted(rows, key=lambda x:-x["fund"]):
        trs += (f"<tr><td>{r['id']}</td><td>전용{r['area']}㎡</td><td>{_unit_band(r['units'])}·{_decade(r['built'])}</td>"
                f"<td><b>{r['fund']:.2f}</b></td><td>{'·'.join(r['strong'])}</td>"
                f"<td>{_eok_band(r['eff_eok'])} <sup>{r['prov_eff']}</sup></td></tr>")
    html = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>서울 {gu} 부동산 데이터 스냅샷 — {today}</title>
<meta name=description content="{gu} 단지 자체 구조점수·실거래 band 스냅샷 ({today}). 익명·통계·방법론 공개. 투자자문 아님.">
<script type="application/ld+json">{{JSONLD}}</script>
<style>body{{font:15px/1.6 -apple-system,Pretendard,sans-serif;max-width:840px;margin:0 auto;padding:24px;color:#1a1a1a}}
.badge{{display:inline-block;background:#e7f5e9;color:#1a7f37;border-radius:6px;padding:2px 9px;font-size:12px}}
.badge.stale{{background:#fff4e5;color:#b54708}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}}th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left}}th{{background:#f6f8fa}}
.disc{{font-size:12px;color:#666;border-top:1px solid #ddd;margin-top:24px;padding-top:12px}}sup{{color:#0969da}}</style>
</head><body>
<h1>서울 {gu} 부동산 데이터 스냅샷 <small>{today}</small></h1>
<p>{fresh_badge} · 라이선스 {LICENSE}</p>
<p class=disc style="border:0;margin:6px 0">⚖ {DISCLAIMER}</p>
<h2>한 줄 요약(BLUF)</h2><p>{bluf}</p>
<h2>익명 구조점수 랭킹</h2>
<p style="font-size:12px;color:#666">단지명은 익명 식별자(구-생활권-순번)로 표기. 점수=자체 결정론 산출(10축·호가무관). 실거래=국토부 공공데이터 12개월 중위 coarse band.</p>
<table><tr><th>식별자</th><th>평형</th><th>규모·연식</th><th>구조점수</th><th>강점축</th><th>실거래대</th></tr>{trs}</table>
<p style="font-size:11px">출처표기: <sup>F</sup>=사실(국토부 실거래 공공데이터) · <sup>I</sup>=추론(표본·가공). 자체점수는 결정론 계산(LLM 재계산 없음).</p>
<div class=disc>
<b>방법론·한계</b><br>
• 자체 10축 구조점수(전세수요·환금성·가격방어·상승여력·토지지분·가격메리트·출퇴근·학군·경사·후기)는 호가무관 fundamental 만으로 랭킹(가격 누수 차단).<br>
• 실거래는 국토부 RTMS 공공데이터 12개월 동일평형 중위(이상치 −40% 컷). 네이버·KB 등 사설 시세 원본은 DB권 보호로 <b>재게시하지 않으며</b> band(대역)로만 표기.<br>
• 단지명 익명화 + 개별 가치판정(매수권유·부적합 등) 비공개. 본 글은 통계·방법론 공유.<br>
• 데이터는 {data_asof} 기준. {('<b>현재 STALE</b> — 신선도 임계 초과.' if stale else '신선도 임계 내.')} 거래 전 원출처 재확인 필수.<br>
• 생성: 결정론 파이프라인(agent_realestate). 자동 생성 콘텐츠.<br>
• <a href="../methodology.html">방법론 전문(산식·출처·한계)</a> · 코드: <a href="https://github.com/hexisteme/agent-realestate">github.com/hexisteme/agent-realestate</a>
</div>
</body></html>"""
    # ── 머신 레이어: JSON-LD ──
    jsonld = {
        "@context":"https://schema.org","@type":"Dataset",
        "name":f"서울 {gu} 부동산 구조점수 스냅샷 {today}",
        "description":bluf,"dateModified":today,"datePublished":today,
        "license":f"https://creativecommons.org/licenses/by-nc/4.0/",
        "creator":{"@type":"Organization","name":"agent_realestate (개인 연구)"},
        "isBasedOn":[{"@type":"Dataset","name":"국토교통부 아파트 실거래가(RTMS) 공공데이터","url":"https://www.data.go.kr"}],
        "variableMeasured":[{"@type":"PropertyValue","name":"structure_score","description":"자체 10축 호가무관 구조점수(0~5)"}],
        "measurementTechnique":"deterministic 10-axis scoring (LLM-free), 12-month MOLIT median (outlier -40% cut)",
        "isAccessibleForFree":True,"keywords":["부동산","구조점수","실거래","서울",gu]}
    html = html.replace("{JSONLD}", json.dumps(jsonld, ensure_ascii=False))
    # ── 머신 레이어: claims.jsonl (1행1주장, provenance/grade) ──
    claims=[]
    for r in sorted(rows, key=lambda x:-x["fund"]):
        claims.append({"id":r["id"],"claim":"structure_score","value":round(r["fund"],2),"grade":"computed",
                       "method":"deterministic_10axis_hoga_free","asof":data_asof,"unit_m2":r["area"]})
        claims.append({"id":r["id"],"claim":"recent_transaction_band","value":_eok_band(r["eff_eok"]),
                       "grade":"fact" if r["prov_eff"]=="F" else "inference","source":"MOLIT_RTMS_public" if r["prov_eff"]=="F" else "sample",
                       "asof":data_asof,"n":r["n"],"note":"coarse band; original private prices not republished (DB-right)"})
    llms_line = f"- [{today} {gu} 스냅샷](/posts/{today}-{gu}.html): {gu} {len(rows)}단지 자체 구조점수·실거래 band. 익명·통계·CC-BY-NC. provenance 동봉(claims.jsonl)."
    return {"html":html,"jsonld":jsonld,"claims":claims,"llms_line":llms_line,"stale":stale}
