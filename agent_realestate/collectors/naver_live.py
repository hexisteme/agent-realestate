"""Live 계층 — 네이버부동산 실호가 수집 시임.

순수 standalone 으로 SPA 호가를 긁을 수 없으므로(RDU-061 라이브 필수), agent_money 의
온체인 MCP-주입 패턴과 동일하게: Claude 가 read-chrome-tab.sh 로 단지 페이지를 읽어
구조화 JSON 으로 만들어 `report --input` 으로 주입한다. 이 모듈은 그 JSON 을 *타입으로
검증된* Candidate 로 변환한다 (4요소 누락·추정 출처는 도메인 생성자가 거부 = G1).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path

from ..domain import Candidate, DataSource, Listing, PriceKind, RedevStage

READ_CHROME = Path.home() / ".claude" / "scripts" / "read-chrome-tab.sh"


def read_tab(pattern: str) -> str:
    """열린 Chrome 탭 innerText 추출 (Claude 가 파싱용으로 사용). 실패 시 빈 문자열."""
    try:
        out = subprocess.run([str(READ_CHROME), pattern], capture_output=True,
                             text=True, timeout=30)
        return out.stdout
    except Exception:
        return ""


_FACE = r"(남향|남동향|남서향|동향|서향|북향|북동향|북서향)"


def parse_eok(s: str) -> int | None:
    """'9억 2,000' → 920000000, '12억' → 1200000000, '8억 5,000'→850000000 (만원 단위)."""
    m = re.search(r"(\d+)\s*억\s*([\d,]+)?", s)
    if not m:
        return None
    eok = int(m.group(1)) * 100_000_000
    man = int(m.group(2).replace(",", "")) * 10_000 if m.group(2) else 0
    return eok + man


def parse_naver_listings(text: str, complex_name: str | None = None) -> list[dict]:
    """read-chrome-tab.sh 의 네이버 단지 innerText → 매물 dict 리스트 (P0-1b, best-effort).
    추출: dong_ho, area_exclusive_m2, floor, facing, price_krw, agent_name, confirmed_date.
    단지 메타(세대수·용적률·재건축·전세)는 미추출 — 별도 enrich 필요. 불완전 블록은 버린다."""
    out: list[dict] = []
    cur: dict = {}
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        m_dong = re.search(r"(\d+동)", ln)
        if m_dong and ("집주인" in ln or (complex_name and complex_name in ln)):
            cur = {"dong_ho": m_dong.group(1)}
        if ln.startswith("매매"):
            p = parse_eok(ln[2:])
            if p:
                cur["price_krw"] = p
        m_area = re.search(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)m", ln)
        if m_area:
            cur["area_exclusive_m2"] = float(m_area.group(2))
            m_floor = re.search(r"([고중저]|\d+)/(\d+)층", ln)
            if m_floor:
                cur["floor"] = m_floor.group(0)
            m_face = re.search(_FACE, ln)
            if m_face:
                cur["facing"] = m_face.group(1)
        if "중개사" in ln and cur.get("price_krw") and "agent_name" not in cur:
            cur["agent_name"] = ln
        m_date = re.search(r"확인매물\s*(\d{2})\.(\d{2})\.(\d{2})", ln)
        if m_date and cur.get("price_krw"):
            cur["confirmed_date"] = f"20{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"
            if all(k in cur for k in ("dong_ho", "price_krw", "area_exclusive_m2", "floor", "facing", "agent_name")):
                out.append(cur)
            cur = {}
    return out


def collapse_brokers(listings: list[dict]) -> list[dict]:
    """동일 매물(동·면적·층·향·호가)이 여러 중개사에 노출되면 1건으로 합치고 broker_count 계산 (R3)."""
    groups: dict = {}
    for l in listings:
        key = (l["dong_ho"], l["area_exclusive_m2"], l["price_krw"], l.get("floor"), l.get("facing"))
        if key not in groups:
            g = dict(l); g["_agents"] = {l["agent_name"]}; groups[key] = g
        else:
            groups[key]["_agents"].add(l["agent_name"])
    out = []
    for g in groups.values():
        g["broker_count"] = len(g.pop("_agents"))
        out.append(g)
    return out


def build_candidates_from_text(text: str, enrich_by_complex: dict | None,
                               complex_name: str) -> list[dict]:
    """R2: 네이버 innerText → candidate dict 리스트. 4요소·호가·broker_count 는 파서가,
    단지 메타(세대수·용적률·재건축·전세·입지·구)는 enrich_by_complex[단지명] 에서 병합."""
    listings = collapse_brokers(parse_naver_listings(text, complex_name))
    enr = (enrich_by_complex or {}).get(complex_name, {})
    return [{**l, "complex_name": complex_name, **enr} for l in listings]


def _candidate_from_dict(d: dict) -> Candidate:
    listing = Listing(
        complex_name=d["complex_name"],
        dong_ho=d["dong_ho"],
        area_exclusive_m2=float(d["area_exclusive_m2"]),
        floor=d["floor"],
        facing=d["facing"],
        price_krw=int(d["price_krw"]),
        price_kind=PriceKind.ASKING_LIVE,
        agent_name=d["agent_name"],
        confirmed_date=date.fromisoformat(d["confirmed_date"]),
        source=DataSource.NAVER_LIVE_CHROME,   # G1: 라이브만 ASKING_LIVE 승격
    )
    return Candidate(
        listing=listing,
        units=int(d.get("units", 0)),
        built_year=int(d.get("built_year", 0)),
        far_pct=float(d.get("far_pct", 0)),
        land_share_pyeong=float(d.get("land_share_pyeong", 0)),
        land_share_is_estimate=bool(d.get("land_share_is_estimate", True)),
        redev_stage=RedevStage[d.get("redev_stage", "NONE")],
        jeonse_krw=(int(d["jeonse_krw"]) if d.get("jeonse_krw") else None),
        transit=d.get("transit", ""),
        district=d.get("district", ""),
        broker_count=int(d.get("broker_count", 1)),
        regulated=bool(d.get("regulated", True)),
        cbd_km=(float(d["cbd_km"]) if d.get("cbd_km") is not None else None),
        cbd_name=d.get("cbd_name", ""),
        hakgun_score=(float(d["hakgun_score"]) if d.get("hakgun_score") is not None else None),
        slope_pct=(float(d["slope_pct"]) if d.get("slope_pct") is not None else None),
        academy_exam=(int(d["academy_exam"]) if d.get("academy_exam") is not None else None),
        review_score=(float(d["review_score"]) if d.get("review_score") is not None else None),
        saenghwalgwon=d.get("saenghwalgwon", ""),
        bcr_pct=(float(d["bcr_pct"]) if d.get("bcr_pct") is not None else None),
        infra=(d.get("infra") if isinstance(d.get("infra"), dict) else None),
        trade_annual=(float(d["trade_annual"]) if d.get("trade_annual") is not None else None),
        gu_jeonse_ratio=(float(d["gu_jeonse_ratio"]) if d.get("gu_jeonse_ratio") is not None else None),
        gu_cagr=(float(d["gu_cagr"]) if d.get("gu_cagr") is not None else None),
        tukmokgo_pct=(float(d["tukmokgo_pct"]) if d.get("tukmokgo_pct") is not None else None),
        school_achievement=(float(d["school_achievement"]) if d.get("school_achievement") is not None else None),
        gu_ipsi_academy=(int(d["gu_ipsi_academy"]) if d.get("gu_ipsi_academy") is not None else None),
        dev_catalyst=d.get("dev_catalyst"),
        redev_infeasible=bool(d.get("redev_infeasible", False)),
        toher_zone=bool(d.get("toher_zone", False)),
        heating=d.get("heating"),
        corridor_type=d.get("corridor_type"),
        parking_per_unit=(float(d["parking_per_unit"]) if d.get("parking_per_unit") is not None else None),
        builder=d.get("builder"),
        nearest_elem_school=d.get("nearest_elem_school"),
        gongsi_man=(int(d["gongsi_man"]) if d.get("gongsi_man") is not None else None),
        maint_fee_won=(int(d["maint_fee_won"]) if d.get("maint_fee_won") is not None else None),
    )


def load_candidates(input_path: str) -> list[Candidate]:
    """주입 JSON(list[dict]) → 검증된 Candidate 리스트. 잘못된 매물은 예외로 즉시 드러남."""
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return [_candidate_from_dict(d) for d in data]
