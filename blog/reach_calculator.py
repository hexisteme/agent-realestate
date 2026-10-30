"""예산닿는곳계산기(ReachCalculator) — reach.html. 모아둔 돈·연소득·기존 대출로 지금 대출 규칙상 닿는 집값과 그 안에 드는
서울 단지(공공 실거래 12개월 중위 대비)를 보여 준다. DIKW 검토 2026-10-30 P1(케이스1 v1). P2(같은 날): 선택 입력 '도착역'을
고르면 `commute_reach` 가 미리 계산한 도착역별 파일(reach-commute/<역ID>.json)을 받아 45분 이내·모형상 초과·계산자료 없음을 센다.
통근 묶음이 없으면 페이지는 P1 그대로다.

산식은 하나만 둔다: 구 원고 W 층의 `gu_dikw.compute_reachable_price` 를 격자(자기자본 0.5억 × 연소득 1천만원)로 미리 계산해
페이지에 싣고, JS 는 조회만 한다. 기존 대출 원리금은 DSR 에만 걸리므로 'DSR 계산 소득 = 연소득 − 연 원리금 ÷ DSR 한도'로 바꿔
같은 격자를 읽는다(LTV·절대한도는 소득과 무관). 입력은 격자 아래쪽으로 내림 — 닿는 가격을 부풀리지 않는다.
점수·순위·권유 없음. 단지 목록은 가격 내림차순(닿는 가격에 가까운 단지부터)이며 평가 순서가 아니다.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

from agent_realestate.analysts import finance as fin
from blog import complex_page as cp
from blog import gu_dikw as gd
from blog.wording_guard import assert_wording_ok

EQUITY_STEP_EOK = 0.5
EQUITY_MAX_EOK = 50.0
INCOME_STEP_MAN = 1000
INCOME_MAX_MAN = 30000             # 3억 — 이 위에서는 스트레스 DSR 한도가 절대한도(최대 6억)를 넘어 결과가 같다
MATRIX_EQUITY_EOK = (3, 5, 7, 10, 15, 20)
MATRIX_INCOME_MAN = (5000, 7000, 10000, 15000)
GU_TABLE_EQUITY_EOK = (3, 5, 10, 15)
GU_TABLE_INCOME_MAN = 10000
ROW_LIMIT = 150
_EPS = 1e-9


def snap_inputs(equity_eok: float, income_man: float, debt_man: float) -> tuple[float, int]:
    """(격자 자기자본 억, 격자 DSR 계산 소득 만원) — 둘 다 아래로 내림, 범위 밖은 끝값. JS reachLookup 과 같은 규칙."""
    eff = max(0.0, income_man - max(0.0, debt_man) / gd.DSR_LIMIT)
    e = min(EQUITY_MAX_EOK, max(0.0, int(max(0.0, equity_eok) / EQUITY_STEP_EOK + _EPS) * EQUITY_STEP_EOK))
    i = min(INCOME_MAX_MAN, max(0, int(eff / INCOME_STEP_MAN + _EPS) * INCOME_STEP_MAN))
    return e, i


def build_reach_grid() -> dict:
    """닿는가격격자 — cells[자기자본][소득] = [닿는 가격(백만원), 대출(만원, 내림), 이유 번호, 전용 85㎡ 초과 닿는 가격(백만원)].
    이유 문장은 reasons. 85㎡ 초과는 농특세가 붙어 닿는 가격이 조금 낮다(Codex R3)."""
    equities = [round(k * EQUITY_STEP_EOK, 1) for k in range(int(EQUITY_MAX_EOK / EQUITY_STEP_EOK) + 1)]
    incomes = list(range(0, INCOME_MAX_MAN + 1, INCOME_STEP_MAN))
    reasons: list[str] = []
    cells = []
    for e in equities:
        row = []
        for inc in incomes:
            r = gd.compute_reachable_price(int(round(e * gd.EOK)), inc * 10_000)
            reason = gd._plain_binding_reason(r.binding, r.price_krw, polite=True)
            if reason not in reasons:
                reasons.append(reason)
            big = gd.compute_reachable_price(int(round(e * gd.EOK)), inc * 10_000, gd.TAX_AREA_LARGE_M2)
            row.append([r.price_krw // gd.PRICE_STEP_KRW, r.loan_krw // 10_000, reasons.index(reason),
                        big.price_krw // gd.PRICE_STEP_KRW])
        cells.append(row)
    return {"equity_eok": equities, "income_man": incomes, "equity_step": EQUITY_STEP_EOK,
            "income_step": INCOME_STEP_MAN, "reasons": reasons, "cells": cells}


def select_reach_rows(ds: dict, written_slugs: set[str]) -> list[dict]:
    """12개월 실거래 중위가 있는 단지(구 원고 W 층과 같은 모집단). 단지 페이지가 실제로 쓰인 경우만 링크를 단다."""
    out = []
    for r in gd._priced(ds.get("complexes") or []):
        slug = cp.complex_slug(r["gu"], r["name"])
        out.append({"gu": r["gu"], "name": r["name"], "area": r.get("area_m2"), "eok": r["molit_recent_eok"],
                    "n": r.get("molit_n"), "p25": r.get("molit_p25_eok"), "p75": r.get("molit_p75_eok"),
                    "type": None if r.get("product_type") == "아파트" else r.get("product_type"),
                    "href": f"complex/{quote(slug)}.html" if slug in written_slugs else None})
    return out


def reach_row_ids(ds: dict) -> list[str]:
    """select_reach_rows 와 같은 순서의 단지 번호 — 통근 묶음(commute_reach.build_commute_bundle)의 값 배열 순서."""
    return [str(r.get("complex_no")) for r in gd._priced(ds.get("complexes") or [])]


def _is_large(area_m2) -> bool:
    """농특세 대상(전용 85㎡ 초과)으로 판정할지. 면적을 모르면 큰 평형으로 본다 — 닿는 범위를 부풀리지 않는다."""
    return not (area_m2 is not None and area_m2 <= gd.FARM_TAX_EDGE_M2)


def count_reach(rows: list[dict], price_krw: int, price_large_krw: int) -> int:
    """닿는 단지 수 — 85㎡ 이하는 price_krw, 초과(또는 면적 모름)는 price_large_krw 와 12개월 실거래 중위를 견준다."""
    return sum(1 for r in gd._priced(rows)
               if round(r["molit_recent_eok"] * gd.EOK) <= (price_large_krw if _is_large(r.get("area_m2")) else price_krw))


def _default_rows(rows: list[dict]) -> list[dict]:
    """정적 표 모집단 — 아파트·주상복합(도생 제외). 페이지 제목 '서울 아파트'와 맞춘다(Codex R3)."""
    return [r for r in gd._priced(rows) if r.get("product_type") != "도생"]


def _prices(e_eok: float, income_man: int) -> tuple[int, int]:
    eq, inc = int(round(e_eok * gd.EOK)), income_man * 10_000
    return (gd.compute_reachable_price(eq, inc).price_krw,
            gd.compute_reachable_price(eq, inc, gd.TAX_AREA_LARGE_M2).price_krw)


def build_static_matrix(rows: list[dict]) -> dict[tuple[int, int], tuple[int, int]]:
    """대표 조합 미리 계산 — {(자기자본 억, 연소득 만원): (닿는 가격 원, 닿는 단지 수)}. JS 없이 읽히는 본문용."""
    base = _default_rows(rows)
    out = {}
    for e in MATRIX_EQUITY_EOK:
        for inc in MATRIX_INCOME_MAN:
            price, price_large = _prices(e, inc)
            out[(e, inc)] = (price, count_reach(base, price, price_large))
    return out


def _gu_table(rows: list[dict]) -> list[tuple[str, int, list[int]]]:
    base = _default_rows(rows)
    prices = [_prices(e, GU_TABLE_INCOME_MAN) for e in GU_TABLE_EQUITY_EOK]
    out = []
    for gu in sorted({r["gu"] for r in base}):
        gu_rows = [r for r in base if r["gu"] == gu]
        out.append((gu, len(gu_rows), [count_reach(gu_rows, p, pl) for p, pl in prices]))
    return out


def _man_txt(man: int) -> str:
    return gd._income_txt(man * 10_000)


_JS = r"""
function reachLookup(D,equityEok,incomeMan,debtMan){
  var g=D.grid,E=g.equity_eok,I=g.income_man,eps=1e-9;
  var eff=Math.max(0,incomeMan-Math.max(0,debtMan)/D.dsr_limit);
  var e=Math.min(E[E.length-1],Math.max(0,Math.floor(Math.max(0,equityEok)/g.equity_step+eps)*g.equity_step));
  var i=Math.min(I[I.length-1],Math.max(0,Math.floor(eff/g.income_step+eps)*g.income_step));
  var c=g.cells[Math.round(e/g.equity_step)][Math.round(i/g.income_step)];
  return {equity_eok:e,income_man:i,price_m:c[0],loan_man:c[1],reason:g.reasons[c[2]],price_l_m:c[3]};
}
function manTxt(v){v=Math.round(v);var e=Math.floor(v/10000),r=v%10000;
  return e?e+'억'+(r?' '+r.toLocaleString()+'만원':''):v.toLocaleString()+'만원';}   // 12000 → '1억 2,000만원'
function eokTxt(v){var t=Math.floor((Math.round(v*1e8)+5e6)/1e7);return Math.floor(t/10)+'.'+(t%10)+'억';}   // 억 → '18.3억', gu_dikw._eok 와 같은 정수 사사오입
function reachRender(){
  var D=window.REACH_D||(window.REACH_D=JSON.parse(document.getElementById('reach-data').textContent));
  if(!D.rows_indexed){D.rows.forEach(function(x,i){x._i=i;});D.rows_indexed=1;}
  var q=function(id){return document.getElementById(id);};
  var num=function(id){var v=parseFloat(q(id).value);return isFinite(v)?v:0;};
  var r=reachLookup(D,num('eq'),num('inc'),num('debt'));
  var area=q('area').value,gu=q('gu').value,pt=q('ptype').value;
  var rows=D.rows.filter(function(x){
    if(gu&&x.gu!==gu)return false;
    if(pt!=='all'&&x.type==='도생')return false;
    if(area==='s'&&!(x.area!=null&&x.area<60))return false;
    if(area==='m'&&!(x.area!=null&&x.area>=60&&x.area<=D.farm_edge_m2))return false;
    if(area==='l'&&!(x.area!=null&&x.area>D.farm_edge_m2))return false;
    return true;});
  var lim=function(x){return (x.area!=null&&x.area<=D.farm_edge_m2?r.price_m:r.price_l_m)*1e6;};   // 85㎡ 초과·면적 모름은 농특세 포함 가격
  var reach=function(x){return Math.round(x.eok*1e8)<=lim(x);};
  var hit=rows.filter(reach);
  var dsrNote=num('debt')>0?'(기존 대출을 빼고 DSR 계산에 쓴 소득 '+manTxt(r.income_man)+')':'';
  q('reach-head').textContent='모아둔 돈 '+r.equity_eok+'억, 연소득 '+manTxt(num('inc'))+dsrNote+
    '이면 대출 '+eokTxt(r.loan_man/10000)+'을 더해 약 '+eokTxt(r.price_m/100)+'짜리 집까지 닿습니다 — '+r.reason+'.'+
    (r.price_l_m<r.price_m?' 전용 85㎡ 초과는 농어촌특별세가 더해져 약 '+eokTxt(r.price_l_m/100)+'까지입니다.':'');
  q('reach-count').textContent=(gu?gu+'구':'서울')+' 단지 '+rows.length.toLocaleString()+'곳 중 '+hit.length.toLocaleString()+
    '곳이 이 안입니다(단지마다 대표 평형의 최근 12개월 실거래 가운데 값과 견줌). 실제 한도는 은행 심사로 정해집니다.';
  var by={};rows.forEach(function(x){var b=by[x.gu]||(by[x.gu]={n:0,k:0});b.n++;if(reach(x))b.k++;});
  var esc=function(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
  q('reach-gu').innerHTML=D.gus.filter(function(g){return by[g];}).map(function(g){var b=by[g],w=Math.round(b.k/b.n*100);
    return '<div class=bar><span class=bl>'+esc(g)+'</span><span class=bt><span class=bf style="width:'+w+'%"></span></span>'+
      '<span class=bn>'+b.k+' / '+b.n+'</span></div>';}).join('');
  hit.sort(function(a,b){return b.eok-a.eok||(a.gu<b.gu?-1:1);});
  var cm=window.commuteAfter?commuteAfter(D,hit,esc):null;   // 통근(P2) — 도착역을 고르지 않았으면 null
  q('reach-rows').innerHTML=hit.slice(0,150).map(function(x){
    var nm=x.href?'<a href="'+x.href+'">'+esc(x.name)+'</a>':esc(x.name);
    var rg=(x.p25&&x.p75)?eokTxt(x.p25)+'~'+eokTxt(x.p75):'—';
    var sub=esc(x.gu)+(x.type?' · '+esc(x.type):'')+' · 거래 '+(x.n||'—')+'건'+(x.n&&x.n<5?'(표본 적음)':'');
    return '<tr><td>'+nm+'<br><span class=mut>'+sub+'</span></td><td>'+(x.area?x.area.toFixed(1)+'㎡':'—')+'</td><td><b>'+
      eokTxt(x.eok)+'</b><br><span class=mut>'+rg+'</span></td>'+(cm?'<td>'+cm(x)+'</td>':'')+'</tr>';}).join('');
  q('reach-more').textContent=hit.length>150?'가격이 높은 순으로 150곳까지만 보여 줍니다(전체 '+hit.length+'곳). 구를 고르면 좁혀집니다.':'';
}
"""

_JS_COMMUTE = r"""
var CMT={id:null,vals:null,req:0,err:''};
function commuteTally(hit,vals,limit){   // commute_reach.tally_commute 와 같은 규칙 — 이내+초과+없음=행 수
  var o={m:hit.length,within:0,over:0,gap:{},dongs:{}};
  hit.forEach(function(x){var v=vals[x._i];
    if(v>=0&&v<=limit){o.within++;var g=o.dongs[x.gu]||(o.dongs[x.gu]={});var d=x.dong||'법정동 미상';g[d]=(g[d]||0)+1;}
    else if(v>limit){o.over++;}
    else{o.gap[String(v)]=(o.gap[String(v)]||0)+1;}});
  return o;}
function commuteLine(){
  var D=window.REACH_D||(window.REACH_D=JSON.parse(document.getElementById('reach-data').textContent));
  var q=function(id){return document.getElementById(id);};
  var esc=function(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
  var st=(D.commute.lines[q('cline').value]||[]);
  q('cstn').innerHTML='<option value="">역을 고르세요</option>'+st.map(function(s){return '<option value="'+esc(s[0])+'">'+esc(s[1])+'</option>';}).join('');
  q('cstn').disabled=!st.length;CMT.id=null;CMT.vals=null;CMT.err='';CMT.req++;reachRender();}
function commuteLoad(){
  var D=window.REACH_D,id=document.getElementById('cstn').value,my=++CMT.req;
  CMT.id=null;CMT.vals=null;CMT.err='';
  if(!id){reachRender();return;}
  fetch('reach-commute/'+id+'.json').then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(j){
    if(my!==CMT.req)return;                                  // 더 나중에 고른 역이 있으면 버린다
    if(j.v!==D.commute.v||!j.t||j.t.length!==D.rows.length){CMT.err='ver';}else{CMT.id=id;CMT.vals=j.t;}
    reachRender();
  }).catch(function(){if(my!==CMT.req)return;CMT.err='load';reachRender();});}
function commuteAfter(D,hit,esc){
  var q=function(id){return document.getElementById(id);};
  if(!D.commute)return null;
  var box=q('cmt-box'),th=q('cmt-th');
  if(CMT.err){box.style.display='';th.style.display='none';q('cmt-dongs').innerHTML='';
    q('cmt-head').textContent=CMT.err==='ver'?'통근 자료가 이 페이지와 다른 날의 것이라 쓰지 않았습니다. 새로고침해 주세요.':'통근 자료를 불러오지 못했습니다.';
    return null;}
  if(!CMT.vals){box.style.display='none';th.style.display='none';return null;}
  var C=D.commute,lim=C.limit,t=commuteTally(hit,CMT.vals,lim),u=t.m-t.within-t.over,nm=C.names[CMT.id];
  var gaps=C.gap_order.filter(function(k){return t.gap[k];}).map(function(k){return C.gap[k]+' '+t.gap[k];}).join(' · ');
  box.style.display='';th.style.display='';
  q('cmt-head').textContent='예산 범위의 과거 실거래 단지 '+t.m.toLocaleString()+'곳 중, 선택역('+nm+')까지 모형 추정 45분 이내 '+
    t.within.toLocaleString()+'곳 · 모형상 초과 '+t.over.toLocaleString()+'곳 · 계산자료 없음 '+u.toLocaleString()+'곳'+(gaps?'('+gaps+')':'');
  q('cmt-dongs').innerHTML=Object.keys(t.dongs).sort().map(function(g){var ds=t.dongs[g];
    return '<p><b>'+esc(g)+'구</b> '+Object.keys(ds).sort().map(function(d){return esc(d)+' '+ds[d];}).join(' · ')+'</p>';}).join('');
  return function(x){var v=CMT.vals[x._i];
    if(v>=0)return '약 '+Math.ceil(v/60)+'분<br><span class=mut>'+(v<=lim?'45분 이내':'모형상 초과')+'</span>';
    return '<span class=mut>'+esc(C.gap[String(v)]||'계산자료 없음')+'</span>';};}
"""

_CSS = (
    "label{display:inline-block;margin:4px 12px 4px 0;font-size:13px}"
    "input,select{width:110px;padding:5px 7px;border:1px solid #cfc9bc;border-radius:6px;font:inherit;background:#fff}"
    "button{padding:6px 14px;border:0;border-radius:6px;background:#1d6f6a;color:#fff;font:inherit;cursor:pointer}"
    ".answer{margin:14px 0;padding:14px 16px;background:#fff;border:1px solid #e3ded3;border-radius:10px}"
    ".answer p{margin:4px 0}#reach-head{font-size:16px;font-weight:600}"
    ".bar{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:13px}.bl{width:44px}"
    ".bt{flex:1;height:10px;background:#ece8df;border-radius:5px;overflow:hidden}.bf{display:block;height:100%;background:#1d6f6a}"
    ".bn{width:72px;text-align:right}table{width:100%;border-collapse:collapse;font-size:13px}"
    "td,th{border-bottom:1px solid #e3ded3;padding:6px 4px;text-align:left}h2{margin:22px 0 8px;font-size:17px}"
    "ol{padding-left:20px}ol li{margin:4px 0}.tblwrap table.reach-tbl{min-width:0}"
)


def _commute_payload(bundle) -> dict:
    from blog import commute_reach as cr
    lines: dict[str, list[list[str]]] = {}
    for s in bundle.stations:
        for line in s["lines"]:
            lines.setdefault(line, []).append([s["id"], s["name"]])
    return {"v": bundle.version, "limit": cr.LIMIT_S, "lines": {k: lines[k] for k in sorted(lines, key=int)},
            "names": {s["id"]: s["name"] for s in bundle.stations},
            "gap": {str(k): v for k, v in cr.GAP_TEXT.items()}, "gap_order": [str(k) for k in sorted(cr.GAP_TEXT, reverse=True)]}


def commute_notice(bundle) -> str:
    """결과 옆 고지 — 기준 시각표 날짜·창·일반열차·걸음 미포함·초과의 뜻(R2 Codex·agy)."""
    m = re.search(r"\((\d{2})(\d{2})(\d{2})\)", bundle.meta.get("timetable_file", ""))
    tt = f"20{m.group(1)}-{m.group(2)}-{m.group(3)} 시각표" if m else "운행시각표"
    return (f"지원 철도 구간(서울교통공사 운행시각표 1~9호선, 평일 07~09시 출발·일반열차, {tt}) 기준이며, 회사까지의 걸음과 실제 도보 경로는 "
            "반영하지 않습니다. 모형상 초과는 실제 통근 가능 여부가 아니며, 지원 구간 밖 노선·급행을 반영하지 않은 계산입니다.")


def render_reach_page(ds: dict, today: str, written_slugs: set[str], commute=None) -> str:
    """reach.html — 입력 → 닿는 가격·이유 → 구별 막대 → 단지 사실표 → 대표 조합(정적) → 계약 전 확인 → 가정·출처.
    commute(CommuteBundle)가 있으면 도착역 입력·통근 집계·표 열·방법 고지를 더한다. 묶음의 행 순서가 이 페이지 행과 다르면 ValueError."""
    from blog.build_site import BASE_URL, ga4_snippet
    from blog.daily_digest import _SITE_CSS
    rows = ds.get("complexes") or []
    payload = {"grid": build_reach_grid(), "rows": select_reach_rows(ds, written_slugs), "dsr_limit": gd.DSR_LIMIT,
               "gus": sorted({r["gu"] for r in gd._priced(rows)}), "farm_edge_m2": gd.FARM_TAX_EDGE_M2}
    cmt_form = cmt_box = cmt_th = cmt_basis = ""
    js = _JS
    if commute is not None:
        ids = reach_row_ids(ds)
        if ids != commute.row_ids:
            raise ValueError("통근 묶음의 행 순서가 reach 행과 다름")
        for r, cn in zip(payload["rows"], ids):
            r["id"], r["dong"] = cn, commute.dongs.get(cn)
        payload["commute"] = _commute_payload(commute)
        line_opts = "".join(f'<option value="{k}">{k}호선</option>' for k in payload["commute"]["lines"])
        cmt_form = (f'<label>도착역 호선 <select id=cline onchange="commuteLine()"><option value="">고르지 않음</option>{line_opts}</select></label>'
                    '<label>도착역 <span class=mut>(회사까지 걸음 미포함)</span> <select id=cstn onchange="commuteLoad()" disabled>'
                    '<option value="">호선을 먼저 고르세요</option></select></label>')
        cmt_box = (f'<div class=answer id=cmt-box style="display:none"><p id=cmt-head></p><p class=mut>{html.escape(commute_notice(commute))}</p>'
                   '<div id=cmt-dongs class=mut></div></div>')
        cmt_th = '<th id=cmt-th style="display:none">선택역까지 모형 추정</th>'
        cmt_basis = ("<p class=mut>도착역 통근은 모형 추정입니다. 단지에서 직선 1km 안의 1~9호선 역 전부를 후보로 두고, 걷기는 직선거리 × 1.3 ÷ 분속 72m"
                     "(환승 통로 산정 기준 1.2m/s)로, 열차는 평일 07~09시에 출발하는 일반열차의 실제 운행시각으로, 기다림은 그 시간대 배차 간격의 절반으로, "
                     "갈아타기는 공사가 공개한 환승 통로 걷기 시간으로 셉니다. 45분 이내는 2,700초 이하입니다. 단지 위치는 계산에만 쓰고 싣지 않습니다. "
                     "위치가 확인되지 않은 단지(주로 도시형생활주택·소형 주상복합)와 1km 안에 지원 역이 없는 단지는 '계산자료 없음'으로 따로 셉니다. "
                     "출처: 서울 열린데이터광장 — 서울교통공사 열차운행시각표·환승역거리 소요시간·역사마스터, 서울시 공동주택 아파트 정보(공공누리 제1유형).</p>")
        js = _JS + _JS_COMMUTE
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    mat = build_static_matrix(rows)
    n_all = len(_default_rows(rows))
    mat_rows = "".join(
        f"<tr><td>{e}억</td>" + "".join(f"<td>{gd._eok(mat[(e, inc)][0])} · {mat[(e, inc)][1]:,}곳</td>" for inc in MATRIX_INCOME_MAN)
        + "</tr>" for e in MATRIX_EQUITY_EOK)
    gu_rows = "".join(f"<tr><td>{html.escape(gu)}</td>" + "".join(f"<td>{k}</td>" for k in ks) + f"<td>{n}</td></tr>"
                      for gu, n, ks in _gu_table(rows))
    mat_head = "".join(f"<th>{_man_txt(i)}</th>" for i in MATRIX_INCOME_MAN)
    gu_head = "".join(f"<th>모아둔 돈 {e}억</th>" for e in GU_TABLE_EQUITY_EOK)
    gu_opts = "".join(f'<option value="{html.escape(g)}">{html.escape(g)}구</option>' for g in payload["gus"])
    checks = (
        "은행에서 실제 대출 한도와 금리를 먼저 확인한다 — 신용·소득 증빙·기존 대출에 따라 이 계산과 달라진다.",
        "집값이 15억·25억 언저리인지 본다 — 넘는 순간 대출 한도가 6억에서 4억, 4억에서 2억으로 줄어 필요한 자기자본이 크게 뛴다.",
        "서울 전역은 토지거래허가구역이라 취득 후 2년 실거주가 원칙이다. 세입자가 있는 집은 실거주 유예 요건과 기한"
        "(일반 주택 신청 기한 2027-12-31, 일부 등록임대 아파트는 별도 기한)을 확인한다.",
        "생애최초(LTV 70%)·정책대출(디딤돌·보금자리론) 대상이면 결과가 달라진다 — 이 계산은 일반 무주택 기준이다.",
        "표의 가격은 지난 12개월 실거래 가운데 값이다. 지금 나온 매물 가격이 그 단지의 25~75% 범위 안팎인지 먼저 대조한다.",
    )
    check_html = "".join(f"<li>{html.escape(c)}</li>" for c in checks)
    basis = gd.policy_basis_text()
    body_text = " ".join(checks) + " " + basis + (" " + commute_notice(commute) + " " + cmt_basis if commute is not None else "")
    assert_wording_ok(body_text, "reach_calculator:page")
    return f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>내 예산으로 닿는 서울 아파트 — 모아둔 돈·연소득·대출 규칙 계산기</title>
<meta name=description content="모아둔 돈·연소득·기존 대출을 넣으면 지금 대출 규칙(LTV 40%·15억/25억 한도·스트레스 DSR)으로 닿는 집값과 그 안에 드는 서울 아파트 단지를 국토부 실거래로 보여 줍니다.">
<link rel=canonical href="{BASE_URL}/reach.html">
<style>{_SITE_CSS}{_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="index.html">구 허브</a><a href="explorer.html">탐색기</a><a href="daily/latest.html">오늘의 변화</a><a href="macro.html">거시 지표</a><a href="calc.html">전달 계산기</a><a href="methodology.html">방법론</a></nav>
<div class=crumb><a href="index.html">서울</a> › 예산 계산기</div>
<h1>내 예산으로 닿는 서울 아파트</h1>
<p class=meta>{today} · 국토부 실거래 12개월 · 대출 규칙 확인일 {gd.POLICY_CHECKED}</p>
<p class=lead>모아둔 돈과 연소득을 넣으면, 지금 대출 규칙으로 닿는 집값과 그 값 안에 드는 서울 아파트 단지를 보여 줍니다. 점수나 순위는 없고, 단지마다 공공 실거래 가운데 값과 견준 결과입니다.</p>
<h2>내 조건 넣기</h2>
<form id=reach-form onsubmit="reachRender();if(window.gtag)gtag('event','reach_calc',{{surface:'reach'}});return false">
<label>모아둔 돈(억) <input id=eq type=number step=0.5 min=0 max={EQUITY_MAX_EOK:g} value=5></label>
<label>연소득(만원) <input id=inc type=number step=100 min=0 value=8000></label>
<label>기존 대출 연간 원리금(만원) <input id=debt type=number step=100 min=0 value=0></label>
<label>전용 면적 <select id=area><option value="">전체</option><option value=s>60㎡ 미만</option><option value=m>60~85㎡</option><option value=l>85㎡ 초과</option></select></label>
<label>구 <select id=gu><option value="">서울 전체</option>{gu_opts}</select></label>
<label>유형 <select id=ptype><option value="">아파트·주상복합</option><option value=all>도생(도시형생활주택)도 포함</option></select></label>
{cmt_form}<button type=submit>계산</button>
</form>
<div class=answer><p id=reach-head>계산 버튼을 누르면 결과가 여기에 나옵니다.</p><p id=reach-count class=mut></p></div>
{cmt_box}<noscript><p class=notice>자바스크립트를 켜면 직접 계산할 수 있습니다. 아래 '대표 조합' 표는 미리 계산한 값입니다.</p></noscript>
<h2>구별로 닿는 단지 수</h2>
<div id=reach-gu></div>
<h2>닿는 단지 <span class=mut>(가격이 높은 순 — 평가 순서가 아님)</span></h2>
<div class=tblwrap><table class=reach-tbl><thead><tr><th>단지 <span class=mut>구·유형·12개월 거래 수</span></th><th>대표 전용</th><th>12개월 실거래 가운데 값 <span class=mut>25~75% 범위</span></th>{cmt_th}</tr></thead><tbody id=reach-rows></tbody></table></div>
<p id=reach-more class=mut></p>
<h2>대표 조합 미리 계산 <span class=mut>(닿는 가격 · 아파트·주상복합 기준 서울 {n_all:,}곳 중 닿는 단지 수)</span></h2>
<div class=tblwrap><table><thead><tr><th>모아둔 돈 \\ 연소득</th>{mat_head}</tr></thead><tbody>{mat_rows}</tbody></table></div>
<h2>연소득 {_man_txt(GU_TABLE_INCOME_MAN)}일 때 구별로 닿는 단지 수 <span class=mut>(아파트·주상복합 기준)</span></h2>
<div class=tblwrap><table><thead><tr><th>구</th>{gu_head}<th>실거래 가격이 있는 단지</th></tr></thead><tbody>{gu_rows}</tbody></table></div>
<h2>계약 전에 확인할 것</h2>
<ol>{check_html}</ol>
<h2>계산 가정과 출처</h2>
<p class=mut>{html.escape(basis)} 단, 전용 85㎡ 초과 단지는 농어촌특별세(0.2%)를 더한 닿는 가격으로 따로 견주고, 면적을 모르는 단지도 그렇게 봅니다. 기존 대출은 연간 원리금을 DSR 한도에서 먼저 빼는 방식으로 반영했습니다. 모아둔 돈은 0.5억, 소득은 1천만원 단위로 내려 계산합니다. 대출금리 {gd.RATE_PCT:.2f}%·만기 {gd.TERM_YEARS}년 가정. 실거래는 국토교통부 실거래가 공개시스템(공공데이터), 단지마다 대표 평형 하나만 견줍니다.</p>
{cmt_basis}<p class=foot><a href="methodology.html">방법론 전문</a> · <a href="{BASE_URL}/reach.html">{BASE_URL}/reach.html</a></p>
</div>
<script id=reach-data type="application/json">{data}</script>
<script id=reach-js>{js}</script>
<script>reachRender();</script>
</body></html>"""
