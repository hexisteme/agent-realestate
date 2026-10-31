"""주간 청년안심주택 모집공고 정리 — 토요일에 다음 주(월~일) 접수 공고를 모아 티스토리 원고를 만든다.

사용자 결정(2026-10-31): 매주 토요일 다음 주 접수 정보를 /180 형태(대표 카드·요약 카드·단지별 정리·출처)로 올리고,
제목·본문은 검색(SEO)과 생성형 검색 인용(GEO)에 걸리게 날짜·지역·역·공급유형을 절대 표기로 담는다.

사실의 출처는 둘뿐이다.
- 서울시 청년안심주택 모집공고 게시판(soco) 목록 JSON 의 본문 요약: 단지명·주택위치·공급호수·사업주체·청약신청 일정·신청 페이지.
- 공고 PDF 텍스트층의 공급현황 표: 보증금·월 임대료 범위와 전용면적. 표를 결정론으로 못 읽으면 금액을 쓰지 않고
  "공고문 공급현황 표에서 확인"으로 둔다(추정 금지). LLM 은 이 경로에 없다.
개인 자격 판정·당첨 가능성은 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_realestate.collectors.housing_support import _plain_text, _soco_schedule  # noqa: E402
from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
SOCO_LIST = "https://soco.seoul.go.kr/youth/pgm/home/yohome/bbsListJson.json?bbsId=BMSR00015&pageIndex={}"
SOCO_VIEW = "https://soco.seoul.go.kr/youth/bbs/BMSR00015/view.do?boardId={}&menuNo=400008"
SOCO_BOARD = "https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do?menuNo=400008"
SOCO_FILE = "https://soco.seoul.go.kr/coHouse/cmmn/file/fileDown.do?atchFileId={}&fileSn={}"
OUTROOT = ROOT / "report" / "blog" / "youth-weekly"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
WEEKDAY = "월화수목금토일"

# /180(2026-10-31)이 '공식 공고 전'으로 적은 용산 두 곳. 공고가 뜨면 알리고 /180 을 갱신한다(사용자 승인 2026-10-31).
# 브랜드만으로는 안 된다 — 해링턴플레이스는 노원·송파에도 있다(10-31 오탐 6694·6679). 지명이나 '용산'과 함께 볼 때만.
WATCH_RULES = (("효창",), ("남영",), ("갈월",), ("해링턴", "용산"), ("롯데캐슬", "용산"))
WATCH_BASELINE = frozenset({6213})
# 첫 주간 글은 2026-11-07(토). 11/2~11/8 주는 /180(10-31 단발 글)이 이미 다뤘다 — 같은 내용을 두 번 올리지 않는다.
SERIES_START = dt.date(2026, 11, 7)   # 롯데캐슬 헤리티지 2025-04-09 최초공고 — 이미 /180 에 반영

FIXED_TAGS = ["청년안심주택", "청년안심주택모집공고", "서울청년주택", "주택청약", "서울임대주택"]
NO_RENT = "금액은 공고문 공급현황 표에서 확인해 주세요(표를 자동으로 읽지 못함)."


@dataclasses.dataclass(frozen=True)
class RentRange:
    """공고 PDF 공급현황 표에서 읽은 보증금·월 임대료 범위(원)와 전용면적 표기."""
    deposit_min: int
    deposit_max: int
    rent_min: int
    rent_max: int
    n_options: int
    areas: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class WeeklyNotice:
    """soco 게시판 공고 1건 — 본문 요약에서 읽은 사실만 담는다."""
    board_id: int
    title: str
    url: str
    kind: str
    is_additional: bool
    station: str | None
    complex_name: str
    address: str | None
    exit_note: str | None
    gu: str | None
    dong: str | None
    supply_text: str | None
    special_units: int | None
    general_units: int | None
    reserve: bool
    operator: str | None
    notice_date: str | None
    apply_start: str | None
    apply_start_at: str | None
    apply_end_at: str | None
    apply_end: str | None
    apply_url: str | None
    phone: str | None
    atch_file_id: str | None
    rent: RentRange | None = None

    @property
    def name(self) -> str:
        """표시 이름 — 단지명 앞의 역 이름과 끝의 '청년안심주택'을 뗀다(역은 따로 적는다)."""
        nm = self.complex_name
        if self.station and nm.startswith(self.station + " "):
            nm = nm[len(self.station) + 1:]
        nm = re.sub(r"\s*청년\s*안심\s*주택$", "", nm).strip()
        return nm or self.complex_name

    def with_rent(self, rent: RentRange | None) -> "WeeklyNotice":
        return dataclasses.replace(self, rent=rent)

    @property
    def start_date(self) -> dt.date | None:
        s = (self.apply_start_at or self.apply_start or "")[:10]
        return dt.date.fromisoformat(s) if s else None

    @property
    def end_date(self) -> dt.date | None:
        e = (self.apply_end_at or self.apply_end or "")[:10]
        return dt.date.fromisoformat(e) if e else self.start_date


def target_week(run_day: dt.date) -> tuple[dt.date, dt.date]:
    """실행일 다음에 오는 월요일~일요일."""
    mon = run_day + dt.timedelta(days=(7 - run_day.weekday()) or 7)
    return mon, mon + dt.timedelta(days=6)


def _field(plain: str, label: str) -> str | None:
    m = re.search(r"■\s*" + label + r"\s*[:：]\s*(.+?)\s*(?=■|\[|$)", plain)
    return " ".join(m[1].split()) if m else None


_DAY = re.compile(r"(?:(\d{4}|\d{2})\s*\.\s*)?(\d{1,2})\s*\.\s*(\d{1,2})(?![\d:])")


def _date_span(plain: str, year_hint: int) -> tuple[str | None, str | None]:
    """'청약신청' 구간들의 날짜만 읽어 (첫날, 끝날). 시각을 못 읽는 형식(날짜만·여러 구간·24:00)용 — Codex R1 #3."""
    days = []
    # '청약신청(일반공급) :' 같은 꼬리표도 구간으로 본다. '청약신청 페이지' 는 주소라 뺀다(Codex R5).
    for seg in re.findall(r"청약\s*신청(?!\s*페이지)[^:：■]{0,20}[:：]\s*(.+?)(?=■|$)", plain):
        year = None
        for y, mo, d in _DAY.findall(re.sub(r"[‘’'`]", "", seg)):
            if y:
                year = int(y) + (2000 if len(y) == 2 else 0)
            yy = year or year_hint
            try:
                day = dt.date(yy, int(mo), int(d))
            except ValueError:
                continue
            if days and day < days[-1] and not y:
                day = day.replace(year=yy + 1)
            if abs(day.year - year_hint) > 1:   # '01.05. 24:00' 같은 시각·번호를 날짜로 읽은 값(Codex R2)
                continue
            days.append(day)
            year = day.year
    if not days:
        return None, None
    return min(days).isoformat(), max(days).isoformat()


def _units(text: str | None, label: str) -> int | None:
    m = re.search(label + r"\s*(\d+)\s*세대", text or "")
    return int(m[1]) if m else None


def parse_board_row(row: dict) -> WeeklyNotice:
    """soco 목록 JSON 한 행 → WeeklyNotice. 본문에 없는 항목은 None 으로 둔다."""
    board = int(str(row["boardId"]))
    title = " ".join(str(row["nttSj"]).split())
    plain = _plain_text(row.get("content"))
    start_at, end_at = _soco_schedule(plain)
    year_hint = int((row.get("optn1") or row.get("optn4") or "2026")[:4])
    span_start, span_end = _date_span(plain, year_hint)
    if start_at and end_at and span_start and (span_start, span_end) != (start_at[:10], end_at[:10]):
        # 시각 판독은 첫 구간만 읽는다 — 청약신청 글 어디든 다른 날짜가 있으면 날짜 구간 전체를 쓴다(Codex R3·R4)
        start_at = end_at = None
    if start_at and end_at:
        span_start, span_end = start_at[:10], end_at[:10]
    m = re.match(r"\[(민간임대|공공임대)\]\s*", title)
    rest = title[m.end():] if m else title
    st = re.match(r"(\S+?역(?:\(예정\))?)\s", rest)
    station = st[1] if st else None
    loc = _field(plain, r"주택\s*위치")
    address = exit_note = None
    if loc:
        pm = re.match(r"(.+?)\s*\(([^()]*)\)\s*$", loc)
        address, exit_note = (pm[1], pm[2].strip()) if pm else (loc, None)
    gm = re.search(r"서울(?:특별)?시\s+(\S+구)(?:\s+(\S+동)(?=\s|$))?", address or "")
    supply = _field(plain, r"공급\s*호수")
    name_from_title = re.sub(r"\s*(?:입주자\s*)?(?:추가\s*)?모집\s*공고.*$", "", rest[st.end():] if st else rest).strip()
    url_m = re.search(r"청약\s*신청\s*페이지\s*[:：]\s*(https?://[^\s■]+)", plain)
    apply_url = url_m[1] if url_m else None
    if apply_url and re.fullmatch(r"https?://[^/]+", apply_url):
        apply_url += "/"   # 티스토리 편집기가 경로 없는 주소에 '/'를 붙인다 — 대조가 어긋나지 않게 미리 맞춘다(10-31 dry)
    phone_m = re.search(r"문의\s*전화\s*[:：]\s*([0-9][0-9\-~, ]*[0-9])", plain)
    return WeeklyNotice(
        board_id=board, title=title, url=SOCO_VIEW.format(board), kind=m[1] if m else "",
        is_additional="추가모집" in title.replace(" ", ""), station=station,
        complex_name=_field(plain, r"단지\s*명") or name_from_title or title,
        address=address, exit_note=exit_note, gu=gm[1] if gm else None, dong=gm[2] if gm else None,
        supply_text=supply, special_units=_units(supply, r"특별\s*공급"), general_units=_units(supply, r"일반\s*공급"),
        reserve="예비" in (supply or "") or "예비" in title, operator=_field(plain, r"사업\s*주체"),
        notice_date=row.get("optn1") or None, apply_start=span_start or (row.get("optn4") or None),
        apply_start_at=start_at, apply_end_at=end_at, apply_end=span_end, apply_url=apply_url,
        phone=phone_m[1].strip() if phone_m else None, atch_file_id=row.get("atchFileId") or None)


def select_for_week(notices: list[WeeklyNotice], mon: dt.date, sun: dt.date) -> list[WeeklyNotice]:
    """접수 기간이 그 주(월~일)와 겹치는 공고 — 접수 시작 순."""
    seen, picked = set(), []
    for n in notices:
        s, e = n.start_date, n.end_date
        if s and e and s <= sun and e >= mon and n.board_id not in seen:
            seen.add(n.board_id)
            picked.append(n)
    return sorted(picked, key=lambda n: (n.apply_start_at or n.apply_start or "", n.apply_end_at or "", n.board_id))


_UNIT = re.compile(r"\(\s*단위\s*[:：]\s*(만원|천원|원)\s*\)")
_TOKEN = re.compile(r"(?<!\S)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?!\S)")
_AREA = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:㎡|m2|m²)(?![0-9])")
_STOP = ("※", "■", "*", "[")
_HEAD_WORD = re.compile(r"(?<!\S)(보증금|임대료)(?!\S)")


def _header_extent(line: str) -> int | None:
    """표 머리줄이면 표 오른쪽 끝(글자 위치), 아니면 None.

    '보증금'·'임대료'가 따로 선 낱말이어야 하고(문장 '임대보증금 및 임대료는' 제외), 끝은 '보증금' 바로 뒤에 온
    마지막 '임대료'다 — 오른쪽 단의 '임대료 안내' 같은 글자로 범위가 늘지 않게(Codex R1 2026-10-31)."""
    prev, end = None, None
    for m in _HEAD_WORD.finditer(line):
        # '보증금' 바로 다음 낱말이 '임대료'일 때만 한 열 쌍 — '보증금 및 임대료 안내' 같은 문장은 짝이 아니다.
        if m[1] == "임대료" and prev is not None and prev[1] == "보증금" and not line[prev.end():m.start()].strip():
            end = m.end()
        prev = m
    return end


def _left(line: str, extent: int) -> str:
    """표 열 범위까지 자른다. 경계에 걸친 숫자는 끝까지 포함한다(반쪽 숫자 방지)."""
    while 0 < extent < len(line) and not line[extent - 1].isspace() and not line[extent].isspace():
        extent += 1
    return line[:extent]


def _supply_header(lines: list[str], i: int) -> int | None:
    """공급현황 표 머리줄이면 표 오른쪽 끝. 머리줄 위아래에 '공급'과 '전용·타입·호수' 머리말이 있어야 한다(소득·자산표 제외)."""
    end = _header_extent(lines[i])
    if end is None:
        return None
    near = "\n".join(x[:end + 6] for x in lines[max(0, i - 6):i + 4])
    return end if "공급" in near and re.search(r"전용|타입|호수", near) else None


def _money_start(header: str) -> int:
    """머리줄에서 금액 열이 시작하는 글자 위치 — 짝을 이룬 첫 '보증금' 낱말의 시작."""
    words = list(_HEAD_WORD.finditer(header))
    return min((a.start() for a, b in zip(words, words[1:]) if a[1] == "보증금" and b[1] == "임대료"), default=0)


def _pair_tokens(left: str) -> tuple[list[tuple[float, float]], bool, list[tuple[int, int]]]:
    """한 줄에서 (보증금, 임대료) 짝. 짝으로 못 묶인 큰 숫자(쉼표 없는 보증금 등)가 있으면 미완 표시.

    반환: (짝, 미완, 짝을 이룬 숫자들의 글자 범위)."""
    toks = list(_TOKEN.finditer(left))
    pairs, odd, j, loose, spans = [], False, 0, 0, []
    while j < len(toks):
        a, frac = toks[j][1], toks[j][2]
        if "," in a and not frac and j + 1 < len(toks):
            b = toks[j + 1][1] + (toks[j + 1][2] or "")
            pairs.append((float(a.replace(",", "")), float(b.replace(",", ""))))
            spans += [toks[j].span(), toks[j + 1].span()]
            j += 2
            loose = 0
            continue
        # 짝 안 된 숫자가 둘 이어지면 쉼표 없는 금액 행(예: '900  5')일 수 있다 — 범위를 좁히느니 판독 포기(Codex R2).
        loose += 1
        odd = odd or loose >= 2
        j += 1
    return pairs, odd, spans


_FIGURE = re.compile(r"\S*\d\S*")


def _stray_figures(left: str, spans: list[tuple[int, int]]) -> list[int]:
    """짝에 들지 못한 숫자 낱말(각주 '900*'·괄호 '(900)'·'900원' 포함)의 시작 위치."""
    return [m.start() for m in _FIGURE.finditer(left) if not any(a <= m.start() < b for a, b in spans)]


def _mostly_figures(left: str) -> bool:
    """낱말 절반 이상에 숫자가 든 줄 — 표 행 조각. 주석 문장('제 19조', '20%, 25%')은 글자 낱말이 더 많다(실측 6709·6715)."""
    words = left.split()
    return bool(words) and 2 * sum(1 for w in words if re.search(r"\d", w)) >= len(words)


def _block_pairs(lines: list[str], head: int) -> tuple[list[tuple[float, float]], list[str], int, bool]:
    """표 머리(보증금·임대료가 함께 있는 줄) 아래에서 (보증금, 임대료) 짝과 전용면적을 읽는다.

    2단 편집 PDF 는 오른쪽 단 글자가 같은 줄에 섞이므로 머리줄의 마지막 '임대료' 위치까지만 본다.
    금액 열(짝 보증금의 가장 왼쪽 시작 − 1 칸부터) 안에 짝 못 된 숫자가 있으면 줄이 나뉜 금액 행일 수 있어
    미완으로 본다 — 실측 공고 8건은 짝 보증금이 머리말보다 최대 2칸 왼쪽, 면적·호수는 5칸 이상 왼쪽(Codex R3).
    숫자는 깨끗한 토큰만이 아니라 숫자가 든 모든 낱말(각주·괄호·단위 붙음)을 본다(Codex R4)."""
    extent = _header_extent(lines[head]) + 6
    pairs, areas, end, complete, limit = [], [], head, True, min(len(lines), head + 45)
    stray, pair_at = [], [_money_start(lines[head])]
    for i in range(head + 1, limit):
        left = _left(lines[i], extent)
        s = left.strip()
        # 왼쪽 단(표) 안에서 시작한 줄만 표 끝 신호로 본다 — 오른쪽 단의 ■·※ 는 다른 글이다(6716).
        inside = (len(left) - len(left.lstrip())) < extent // 2
        if pairs and inside and (s.startswith(_STOP) or re.search(r"합\s*계", s)):
            # 끝 신호 뒤 12줄에 또 금액 행이 있으면 표 중간의 주석이었다 — 일부만 읽은 범위는 쓰지 않는다(Codex R1).
            for later in lines[i + 1:i + 13]:
                lp = _left(later, extent)
                if _header_extent(lp) or re.search(r"합\s*계", lp):
                    break
                more, odd, sp = _pair_tokens(lp)
                if more or odd:
                    complete = False
                    break
                if _mostly_figures(lp):
                    stray += _stray_figures(lp, sp)
            break
        if _header_extent(left):
            break
        found, odd, sp = _pair_tokens(left)
        pairs += found
        complete = complete and not odd
        stray += _stray_figures(left, sp)
        pair_at += [a for a, _ in sp[::2]]
        areas += _AREA.findall(left)
        end = i
    else:
        if pairs and any(_pair_tokens(_left(lines[k], extent))[0] for k in range(max(head + 1, limit - 5), limit)):
            complete = False
    money = min(pair_at) - 1
    if any(x >= money for x in stray):
        complete = False
    return pairs, areas, end, complete


def parse_rent_table(text: str) -> RentRange | None:
    """공고 PDF(pdftotext -layout) 공급현황 표 → 보증금·월 임대료 범위. 하나라도 말이 안 되는 값이 섞이면 None."""
    lines = text.splitlines()
    won_pairs, areas, i = [], [], 0
    while i < len(lines):
        ln = lines[i]
        if _supply_header(lines, i):
            pairs, found_areas, end, complete = _block_pairs(lines, i)
            if not complete:
                return None
            if pairs:
                window = "\n".join(lines[max(0, i - 14):i + 1])
                marks = _UNIT.findall(window)
                if marks:
                    mult = {"원": 1, "천원": 1_000, "만원": 10_000}[marks[-1]]
                else:
                    mult = 1 if min(d for d, _ in pairs) >= 1_000_000 else 10_000
                won_pairs += [(round(d * mult), round(r * mult)) for d, r in pairs]
                areas += found_areas
            i = max(end, i) + 1
            continue
        i += 1
    if not won_pairs:
        return None
    for d, r in won_pairs:
        # 보증금/월세 배수 30~1500 — 실측 10-29 공고 58~957. 소득 기준표 숫자(배수 14 안팎)가 섞이면 통째로 버린다.
        if not (5_000_000 <= d <= 1_500_000_000 and 50_000 <= r <= 3_000_000 and 30 <= d / r <= 1500):
            return None
    uniq = tuple(sorted(set(areas), key=float))
    return RentRange(min(d for d, _ in won_pairs), max(d for d, _ in won_pairs),
                     min(r for _, r in won_pairs), max(r for _, r in won_pairs), len(won_pairs), uniq)


def format_won(won: int) -> str:
    """원 → '1억2,825만' · '7,300만' · '33만7,600'. 반올림하지 않는다(범위가 좁아지지 않게)."""
    eok, rest = divmod(int(won), 100_000_000)
    man, won_rest = divmod(rest, 10_000)
    out = (f"{eok}억" if eok else "") + (f"{man:,}만" if man else "") + (f"{won_rest:,}" if won_rest else "")
    return out or "0"


def _abs_day(d: dt.date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일({WEEKDAY[d.weekday()]})"


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def _md_w(d: dt.date) -> str:
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def _period(n: WeeklyNotice, absolute: bool = False) -> str:
    """접수 기간 문구. 시각을 못 읽은 공고는 시작일만 쓴다."""
    s = n.start_date
    if not s:
        return "공고문 확인"
    fmt = _abs_day if absolute else _md_w
    if not n.apply_start_at:
        if n.apply_end and n.end_date != s:
            return f"{fmt(s)} ~ {fmt(n.end_date)}(시각은 공고문)"
        return f"{fmt(s)}(시각은 공고문)" if n.apply_end else f"{fmt(s)} 시작(시각은 공고문)"
    st, en = n.apply_start_at[11:16], (n.apply_end_at or "")[11:16]
    e = n.end_date
    if e == s:
        return f"{fmt(s)} {st} ~ {en}"
    return f"{fmt(s)} {st} ~ {fmt(e)} {en}"


def _recruit(n: WeeklyNotice) -> str:
    parts = []
    if n.general_units:
        parts.append(f"일반 {n.general_units}")
    if n.special_units:
        parts.append(f"특별 {n.special_units}")
    if parts:
        return " · ".join(parts) + "세대"
    return "예비자 모집" if n.reserve else "공고문 확인"


def _ordered(values: list[str]) -> list[str]:
    """많이 나온 순, 같으면 먼저 나온 순."""
    cnt, first = Counter(values), {}
    for i, v in enumerate(values):
        first.setdefault(v, i)
    return sorted(cnt, key=lambda v: (-cnt[v], first[v]))


def _rent_line(r: RentRange) -> str:
    return (f"보증금 {format_won(r.deposit_min)}~{format_won(r.deposit_max)} 원 · "
            f"월 임대료 {format_won(r.rent_min)}~{format_won(r.rent_max)} 원")


def _short_rent(r: RentRange | None) -> str:
    if not r:
        return "공고문 표 참조"
    dep = format_won(r.deposit_min) if r.deposit_min == r.deposit_max else f"{format_won(r.deposit_min)}~{format_won(r.deposit_max)}"
    rent = format_won(r.rent_min) if r.rent_min == r.rent_max else f"{format_won(r.rent_min)}~{format_won(r.rent_max)}"
    return f"{dep} / {rent}"


def _host(url: str | None) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m[1] if m else ""


def _table(head: list[str], rows: list[tuple]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def render_post(mon: dt.date, notices: list[WeeklyNotice], checked_at: dt.datetime) -> dict:
    """제목·태그·리드·본문(HTML)·카드 HTML. 같은 입력이면 같은 출력."""
    esc = html.escape
    sun = mon + dt.timedelta(days=6)
    n = len(notices)
    week_no = (mon.day - 1) // 7 + 1
    all_add = all(x.is_additional for x in notices)
    label = "추가모집" if all_add else "모집공고"
    gus = _ordered([x.gu for x in notices if x.gu])
    stations = _ordered([x.station for x in notices if x.station])
    kinds = _ordered([("공공지원민간임대" if x.kind == "민간임대" else x.kind) for x in notices if x.kind])
    span = f"{_md(mon)}~{_md(sun)}"
    base = f"{mon.year}년 {mon.month}월 {week_no}주차 청년안심주택 {label} {span} 접수 {n}곳 정리"
    title = base
    if stations:
        tail = f" ({'·'.join(stations[:2])}{' 등' if len(stations) > 2 else ''})"
        if len(base + tail) <= 60:
            title = base + tail
    tags = list(FIXED_TAGS)
    if any(x.is_additional for x in notices):
        tags.append("청년안심주택추가모집")
    for t in gus[:2] + stations[:2]:
        if len(tags) < 10 and t not in tags:
            tags.append(t)

    checked = f"{_abs_day(checked_at.date())} {checked_at:%H:%M}"
    first = notices[0]
    first_at = f"{_abs_day(first.start_date)}" + (f" {first.apply_start_at[11:16]}" if first.apply_start_at else "")
    gu_txt = "·".join(gus) if gus else "서울"
    kind_txt = "·".join(kinds) if kinds else "청년안심주택"
    lead = (f"{_abs_day(mon)}부터 {_abs_day(sun)}까지 서울에서 접수하는 청년안심주택 {label}는 "
            f"{len(gus)}개 구 {n}곳입니다. {gu_txt}에서 {kind_txt} {label}가 있고, 첫 접수는 {first_at}에 시작합니다. "
            f"각 단지의 공고 원문 링크를 함께 붙였고, 이 글은 서울시 청년안심주택 모집공고 게시판을 {checked}에 확인한 기준입니다.")

    b = [f"<p>{esc(lead)}</p>",
         f'<p><img src="summary.png" alt="{esc(f"{span} 접수 청년안심주택 {label} {n}곳 요약 카드")}"></p>']
    b.append(f"<h2>{span} 접수 한눈에 보기</h2>")
    b.append("<p>" + esc(f"{n}곳 모두 아래 표에 접수 시작 순으로 적었습니다. 금액은 공고문 공급현황 표의 보증금 / 월 임대료 범위이고, "
                         "보증금 비율이나 호실에 따라 달라집니다.") + "</p>")
    b.append(_table(["단지 · 역", "구", "모집", "접수", "보증금 / 월 임대료"],
                    [(f"{esc(x.name)}<br>{esc(x.station or '')}", esc(x.gu or "-"), esc(_recruit(x)),
                      esc(_period(x)).replace(" ~ ", " ~<br>"), esc(_short_rent(x.rent))) for x in notices]))

    b.append("<h2>접수 일정 (날짜순)</h2><ul>")
    for x in notices:
        how = f" · 신청 페이지 {_host(x.apply_url)}" if x.apply_url else " · 신청 방법은 공고문"
        b.append(f"<li>{esc(_period(x, absolute=True))} — {esc(x.name)}"
                 f"{esc(' (' + x.station + ')' if x.station else '')}{esc(how)}</li>")
    b.append("</ul>")

    b.append(f"<h2>{esc(gu_txt)} 단지별 공고 요약</h2>")
    for x in notices:
        sup = "예비자 모집" if (x.reserve and not (x.general_units or x.special_units)) else (
            f"{label} " + _recruit(x))
        h3 = " · ".join(p for p in [" ".join(p for p in [x.gu, x.dong] if p), x.station, x.name, sup] if p)
        b.append(f"<h3>{esc(h3)}</h3>")
        lines = [f"접수: {_period(x, absolute=True)}"]
        if x.address:
            lines.append("위치: " + x.address.replace("서울특별시 ", "") + (f" · {x.exit_note}" if x.exit_note else ""))
        if x.supply_text:
            lines.append(f"모집: {x.supply_text}")
        if x.phone:
            lines.append(f"문의: {x.phone}")
        body = "<br>".join(esc(t) for t in lines)
        if x.apply_url:
            body += f'<br>신청: <a href="{esc(x.apply_url)}">청약신청 페이지({esc(_host(x.apply_url))})</a>'
        b.append(f"<p>{body}</p>")
        if x.rent:
            area = (" · 전용 " + "·".join(f"{a}㎡" for a in x.rent.areas)) if x.rent.areas else ""
            b.append("<p>" + esc(f"임대 조건(공고문 공급현황 표{area}): {_rent_line(x.rent)}. "
                                 "보증금 비율·타입·호실별 금액은 공고문 표에 있습니다.") + "</p>")
        else:
            b.append(f"<p>{esc(NO_RENT)}</p>")
        b.append(f'<p><a href="{esc(x.url)}">{esc(x.name)} 모집공고 원문(서울시 청년안심주택)</a></p>')

    n_priv = sum(1 for x in notices if x.kind == "민간임대")
    n_pub = sum(1 for x in notices if x.kind == "공공임대")
    n_add = sum(1 for x in notices if x.is_additional)
    reserve = [x.name for x in notices if x.reserve]
    special = [x.name for x in notices if x.special_units]
    general = [x.name for x in notices if x.general_units]
    b.append(f"<h2>공급유형별 정리 ({esc(kind_txt)})</h2><ul>")
    for li in [f"공공지원민간임대 {n_priv}곳 · 공공임대 {n_pub}곳 · 이 중 추가모집 {n_add}곳",
               f"일반공급 세대가 있는 곳 {len(general)}곳: " + (", ".join(general) or "없음"),
               f"특별공급 세대가 있는 곳 {len(special)}곳: " + (", ".join(special) or "없음"),
               f"예비자 모집 {len(reserve)}곳: " + (", ".join(reserve) or "없음")]:
        b.append(f"<li>{esc(li)}</li>")
    b.append("</ul>")

    b.append("<h2>신청 전에 공고문에서 확인할 항목</h2>")
    b.append("<p>아래는 공고문마다 따로 정하는 항목입니다. 같은 청년안심주택이라도 단지·공급유형마다 기준이 다르니 "
             "본인이 신청할 공고의 원문으로 확인하세요.</p><ul>")
    for li in ["나이·무주택 요건과 그 판단 기준일(예비자 모집은 기준일을 따로 적은 공고가 있습니다)",
               "특별공급의 소득·자산 기준(일반공급과 다릅니다)",
               "자동차 소유 기준(차량가액 상한)",
               "중복 신청 제한의 범위(한 단지 안 여러 타입, 여러 단지)",
               "보증금 비율 선택 가능 여부와 비율별 금액",
               "보증금 대출 가능 여부(주택 근저당 등으로 제한될 수 있다는 안내가 있는지)",
               "기존 계약자의 신청 가능 여부와 입주 가능일"]:
        b.append(f"<li>{esc(li)}</li>")
    b.append("</ul>")

    faq = [
        (f"{mon.year}년 {mon.month}월 {week_no}주차({span}) 서울 청년안심주택 접수는 언제인가요?",
         " / ".join(f"{x.name}: {_period(x)}" for x in notices) + "."),
        ("이번 주 접수 단지는 몇 곳이고 어느 지역인가요?",
         f"{n}곳입니다. 지역은 {gu_txt}입니다" + (f"(역: {', '.join(stations)})." if stations else ".")),
        ("신청은 어디서 하나요?",
         "단지마다 다릅니다. 각 공고 본문에 적힌 청약신청 페이지에서 받으며, 위 단지별 요약에 링크를 붙였습니다."),
        ("공급유형은 어떤 것이 있나요?",
         f"공공지원민간임대 {n_priv}곳, 공공임대 {n_pub}곳이고, 예비자 모집은 {len(reserve)}곳입니다."),
        ("공고 원문은 어디서 보나요?",
         "서울시 청년안심주택 모집공고 게시판(soco.seoul.go.kr)에 있고, 단지별 요약 끝에 원문 링크를 붙였습니다."),
        ("이 글은 언제 기준인가요?",
         f"{checked} 게시판 확인 기준입니다. 공고가 바뀌거나 추가되면 원문이 우선입니다."),
    ]
    b.append("<h2>자주 묻는 질문</h2>")
    for q, a in faq:
        b.append(f"<h3>Q. {esc(q)}</h3><p>{esc(a)}</p>")

    b.append("<h2>출처와 확인 일자</h2>")
    b.append("<p>" + esc(f"일정·위치·모집 세대는 서울시 청년안심주택 모집공고 게시판의 공고 본문에서, 금액과 전용면적은 각 공고문(PDF) "
                         f"공급현황 표에서 옮겼습니다. 확인 시각: {checked}. 공고문에는 보증금·임대료가 타입별 기준 금액이며 호실에 따라 "
                         "다를 수 있다는 안내가 있습니다. 신청 자격·금액·접수 시각은 단지별 공식 모집공고문으로 직접 확인해 주세요. "
                         "이 글은 공고 내용을 정리한 정보이며 투자·법률 자문이 아닙니다.") + "</p>")
    b.append(f'<p><a href="{SOCO_BOARD}">서울시 청년안심주택 모집공고 게시판</a></p>')
    body_html = "".join(b)

    cover_alt = f"{span} 접수 청년안심주택 {label} {n}곳 대표 이미지"
    return {"title": title, "tags": tags, "lead": lead, "body_html": body_html, "label": label, "span": span,
            "cover_alt": cover_alt, "cover_html": _cover_html(mon, notices, label, span, gus, stations, checked_at),
            "summary_html": _summary_html(notices, label, span, checked_at)}


CARD_CSS = """*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Apple SD Gothic Neo',-apple-system,sans-serif;-webkit-font-smoothing:antialiased}"""


def _cover_html(mon, notices, label, span, gus, stations, checked_at) -> str:
    esc = html.escape
    n = len(notices)
    badge2 = "추가모집" if label == "추가모집" else f"{n}곳 접수"
    st_line = " · ".join(stations[:6])
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CARD_CSS}
#c{{width:1200px;height:630px;position:relative;overflow:hidden;background:#f4f1e8}}
.band{{position:absolute;left:0;top:0;width:1200px;height:630px;
 background:radial-gradient(circle at 1050px 120px,#cfe6c4 0,#cfe6c4 170px,transparent 171px),
 radial-gradient(circle at 1120px 520px,#176556 0,#176556 120px,transparent 121px)}}
.kicker{{position:absolute;left:84px;top:78px;font-size:30px;font-weight:700;color:#176556;letter-spacing:-0.5px}}
h1{{position:absolute;left:80px;top:128px;font-size:96px;font-weight:800;color:#13332b;letter-spacing:-3px;line-height:1.08}}
.sub{{position:absolute;left:84px;top:358px;font-size:40px;font-weight:600;color:#2b4a40;letter-spacing:-1px}}
.badges{{position:absolute;left:84px;top:436px;display:flex;gap:16px}}
.b{{font-size:30px;font-weight:800;padding:12px 26px;border-radius:40px}}
.b1{{background:#13332b;color:#fff}} .b2{{background:#fff;color:#13332b;border:3px solid #13332b}}
.st{{position:absolute;left:84px;top:528px;font-size:22px;color:#4f6a60;letter-spacing:-0.3px}}
.src{{position:absolute;left:84px;top:572px;font-size:19px;color:#6d8178}}
</style></head><body><div id='c'><div class='band'></div>
<div class='kicker'>서울 청년안심주택 · 다음 주 접수 정리</div>
<h1>청년안심주택<br>{esc(span)}</h1>
<div class='sub'>{esc(f"{label} {n}곳 접수 일정·금액")}</div>
<div class='badges'><span class='b b1'>{esc(f"{len(gus)}개 구")}</span><span class='b b2'>{esc(badge2)}</span></div>
<div class='st'>{esc(st_line)}</div>
<div class='src'>{esc(f"출처: soco.seoul.go.kr 모집공고 ({checked_at:%Y.%m.%d} 확인) · 서울 아파트 실거래 데이터랩")}</div>
</div></body></html>"""


def _summary_html(notices, label, span, checked_at) -> str:
    esc = html.escape
    rows = []
    for x in notices:
        close = f"~{_md_w(x.end_date)} {(x.apply_end_at or '')[11:16]}".rstrip() if x.end_date else "공고문 확인"
        money = f" · <b>{esc(_short_rent(x.rent))}</b>" if x.rent else ""
        rows.append(f"""<div class='r'><div class='l1'><span class='n'>{esc(x.name)}</span>
<span class='s'>{esc(x.station or x.gu or '')}</span><span class='cl'>{esc(close)}</span></div>
<div class='l2'>{esc(x.gu or '-')} · {esc(_recruit(x))}{money}</div></div>""")
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CARD_CSS}
#c{{width:800px;background:#f4f1e8;padding:44px 40px 36px}}
.t{{font-size:40px;font-weight:800;color:#13332b;letter-spacing:-1.5px}}
.t2{{font-size:24px;color:#2b4a40;margin:10px 0 26px}}
.r{{background:#fff;border-radius:18px;padding:18px 22px;margin-bottom:12px;border:2px solid #dfe6d8}}
.l1{{display:flex;align-items:baseline;gap:12px}}
.n{{font-size:30px;font-weight:800;color:#13332b;letter-spacing:-1px}}
.s{{font-size:23px;color:#4f6a60}}
.cl{{margin-left:auto;font-size:23px;font-weight:700;color:#176556;white-space:nowrap}}
.l2{{font-size:23px;color:#2b4a40;margin-top:8px;letter-spacing:-0.5px}}
.l2 b{{color:#13332b}}
.f{{font-size:19px;color:#6d8178;margin-top:18px;line-height:1.5}}
</style></head><body><div id='c'>
<div class='t'>{esc(f"청년안심주택 {label} {len(notices)}곳")}</div>
<div class='t2'>{esc(f"{span} 접수 · 접수 시작 순 · 오른쪽은 마감 시각")}</div>
{''.join(rows)}
<div class='f'>금액: 공고문 공급현황 표의 보증금 / 월 임대료 범위(원)<br>
{esc(f"출처: 서울시 청년안심주택 모집공고 ({checked_at:%Y.%m.%d} 확인) · 서울 아파트 실거래 데이터랩")}</div>
</div></body></html>"""


def preview_html(title: str, body: str) -> str:
    return ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>body{{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;"
            "max-width:760px;margin:0 auto;padding:16px;color:#1b3831;line-height:1.85}"
            "table{width:100%;border-collapse:collapse;font-size:14px}th,td{border:1px solid #c9d3c2;padding:8px 6px;"
            "vertical-align:top}th{background:#edf1e7}img{max-width:100%}h2{color:#176556;margin-top:40px}</style></head>"
            f"<body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>")


def render_card(doc: str, path: Path, scale: float) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=True)
        try:
            page = br.new_page(device_scale_factor=scale, viewport={"width": 1300, "height": 800})
            page.set_content(doc, wait_until="load")
            page.locator("#c").screenshot(path=str(path))
        finally:
            br.close()


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": SOCO_BOARD})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_rows(since: dt.date, max_pages: int = 6) -> list[dict]:
    """공고일이 since 보다 오래된 행만 남을 때까지 목록을 넘긴다."""
    rows = []
    for page in range(1, max_pages + 1):
        part = json.loads(_get(SOCO_LIST.format(page)).decode("utf-8"))["resultList"]
        rows += part
        if not part or all((r.get("optn1") or "9999") < since.isoformat() for r in part):
            break
    return rows


def pdf_text_fetcher(out_dir: Path):
    """공고 첨부 중 첫 PDF 를 받아 pdftotext -layout 결과를 돌려주는 함수. 실패하면 None."""
    pdf_dir = out_dir / "pdf"

    def fetch(n: WeeklyNotice) -> str | None:
        if not n.atch_file_id or not re.fullmatch(r"[0-9a-f]{16,64}", n.atch_file_id):
            return None
        pdf_dir.mkdir(parents=True, exist_ok=True)
        for sn in (1, 2, 3):
            try:
                data = _get(SOCO_FILE.format(n.atch_file_id, sn), timeout=60)
            except Exception:
                continue
            if not data.startswith(b"%PDF"):
                continue
            path = pdf_dir / f"{n.board_id}-{sn}.pdf"
            path.write_bytes(data)
            try:
                return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                                      timeout=60, check=True).stdout
            except Exception:
                return None
        return None
    return fetch


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class _StateLock:
    """묶음 상태 파일 잠금 — 빌드의 확인→쓰기와 발행 시도 기록이 끼어들지 않게(Codex R2)."""
    def __init__(self, out_dir: Path):
        self.path = Path(out_dir) / ".state.lock"

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.f.close()


def read_state(out_dir: Path) -> dict:
    p = Path(out_dir) / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _update_state(out_dir: Path, **fields) -> dict:
    st = read_state(out_dir)
    st.update(fields, updated_at=dt.datetime.now(KST).isoformat(timespec="seconds"))
    _write_json(Path(out_dir) / "state.json", st)
    return st


def write_state(out_dir: Path, **fields) -> dict:
    with _StateLock(out_dir):
        return _update_state(out_dir, **fields)


def _write_unless_attempted(out_dir: Path, **fields) -> dict:
    """상태 잠금 안에서 다시 읽어 발행 시도·공개 기록이 있으면 그대로 둔다(조회 중 발행이 끝난 경우 — Codex R3)."""
    with _StateLock(out_dir):
        latest = read_state(out_dir)
        if latest.get("state") in ("PUBLISH_ATTEMPTED", "PUBLISHED"):
            return latest
        return _update_state(out_dir, **fields)


def build(run_day: dt.date, out_dir: Path, rows: list[dict] | None = None, fetch_pdf_text=None,
          render_cards: bool = True, now: dt.datetime | None = None) -> dict:
    """원고 묶음(preview.html·post.json·notices.json·cover.png·summary.png·state.json)을 쓴다.

    발행 시도 기록이 있으면 다시 만들지 않는다(같은 글을 두 번 올리지 않게)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prior = read_state(out_dir)
    if prior.get("state") in ("PUBLISH_ATTEMPTED", "PUBLISHED"):
        return prior
    mon, sun = target_week(run_day)
    now = now or dt.datetime.now(KST)
    if rows is None:
        rows = fetch_rows(mon - dt.timedelta(days=40))
    notices, bad = [], []
    for r in rows:
        try:
            notices.append(parse_board_row(r))
        except Exception as e:
            bad.append(f"{r.get('boardId')}:{type(e).__name__}")
    picked = select_for_week(notices, mon, sun)
    if not picked:
        return _write_unless_attempted(out_dir, state="SKIPPED_EMPTY", week=[mon.isoformat(), sun.isoformat()], n=0,
                           unparsed=bad, note=f"{_md(mon)}~{_md(sun)} 접수 예정 모집공고는 {now:%Y-%m-%d %H:%M} 기준 확인되지 않음")
    fetch_pdf_text = fetch_pdf_text or pdf_text_fetcher(out_dir)
    with_rent = []
    for x in picked:
        text = fetch_pdf_text(x)
        with_rent.append(x.with_rent(parse_rent_table(text) if text else None))
    lock = _StateLock(out_dir).__enter__()
    try:
        return _write_bundle(out_dir, mon, sun, with_rent, now, bad, render_cards)
    finally:
        lock.__exit__()


def _write_bundle(out_dir, mon, sun, with_rent, now, bad, render_cards) -> dict:
    """상태 잠금 안에서: 발행 시도가 이미 있으면 손대지 않고, 없으면 원고를 쓰고 BUILT."""
    latest = read_state(out_dir)
    if latest.get("state") in ("PUBLISH_ATTEMPTED", "PUBLISHED"):
        return latest
    post = render_post(mon, with_rent, checked_at=now)
    text = re.sub(r"<[^>]+>", " ", post["body_html"])
    assert_wording_ok(post["title"] + " " + text, "youth-weekly")
    assert_lead_wording_ok(post["lead"], "youth-weekly-lead")
    (out_dir / "preview.html").write_text(preview_html(post["title"], post["body_html"]), encoding="utf-8")
    _write_json(out_dir / "post.json", {"title": post["title"], "tags": post["tags"], "cover_alt": post["cover_alt"],
                                        "week": [mon.isoformat(), sun.isoformat()], "n": len(with_rent)})
    _write_json(out_dir / "notices.json", [dataclasses.asdict(x) for x in with_rent])
    if render_cards:
        render_card(post["cover_html"], out_dir / "cover.png", 1)
        render_card(post["summary_html"], out_dir / "summary.png", 2)
    return _update_state(out_dir, state="BUILT", week=[mon.isoformat(), sun.isoformat()], n=len(with_rent),
                       title=post["title"], rent_parsed=sum(1 for x in with_rent if x.rent), unparsed=bad)


def watch_hits(notices: list[WeeklyNotice], seen: set) -> list[WeeklyNotice]:
    """/180 이 '공식 공고 전'으로 적은 용산 두 곳의 새 공고."""
    def hay(n):
        return " ".join(x for x in (n.title, n.complex_name, n.address, n.gu) if x)
    return [n for n in notices if n.board_id not in seen and n.board_id not in WATCH_BASELINE
            and any(all(k in hay(n) for k in rule) for rule in WATCH_RULES)]


def notify(text: str) -> bool:
    """텔레그램 알림(실패는 무음)."""
    try:
        from blog import captcha_relay as cr
        cred = cr.read_credentials()
        if not cred:
            return False
        cr.send_text(cred[0], cred[1], text)
        return True
    except Exception:
        return False


def run_watch(rows: list[dict], outroot: Path = OUTROOT) -> list[int]:
    path = Path(outroot) / "watch-180.json"
    seen = set(json.loads(path.read_text())["seen"]) if path.exists() else set()
    notices = []
    for r in rows:
        try:
            notices.append(parse_board_row(r))
        except Exception:
            continue
    hits = watch_hits(notices, seen)
    for h in hits:
        notify(f"[청년안심주택] /180 갱신 대상 공고 게시: {h.title}\n{h.url}\n접수 {_period(h)} — 세션에서 '/180 갱신' 진행")
    if hits:
        Path(outroot).mkdir(parents=True, exist_ok=True)
        _write_json(path, {"seen": sorted(seen | {h.board_id for h in hits}),
                           "hits": [{"board_id": h.board_id, "title": h.title, "url": h.url,
                                     "found_at": dt.datetime.now(KST).isoformat(timespec="seconds")} for h in hits]})
    return [h.board_id for h in hits]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="주간 청년안심주택 모집공고 정리(토요일)")
    ap.add_argument("cmd", choices=["build", "watch", "run"])
    ap.add_argument("--day", default=None, help="실행일 YYYY-MM-DD(기본 오늘)")
    ap.add_argument("--publish", action="store_true", help="run: 원고가 준비되면 공개 발행 1회")
    ap.add_argument("--any-day", action="store_true", help="run: 토요일이 아니어도 주간 원고 단계 실행")
    a = ap.parse_args(argv)
    day = dt.date.fromisoformat(a.day) if a.day else dt.datetime.now(KST).date()
    out_dir = OUTROOT / day.isoformat()
    import fcntl
    OUTROOT.mkdir(parents=True, exist_ok=True)
    run_lock = open(OUTROOT / ".run.lock", "w")
    try:
        fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("SKIP: 다른 주간 정리 실행 중")
        return 0
    if a.cmd == "build":
        print(json.dumps(build(day, out_dir), ensure_ascii=False))
        return 0
    mon, _ = target_week(day)
    rows = fetch_rows(mon - dt.timedelta(days=40))
    hits = run_watch(rows)
    for back in range(0, 15):   # 캡차로 멈춘 묶음은 재클릭 없이 공개 확인만(오늘 것은 아래 발행 단계가 확인)
        prev = OUTROOT / (day - dt.timedelta(days=back)).isoformat()
        pst = read_state(prev)
        if back == 0 or pst.get("state") != "PUBLISH_ATTEMPTED":
            continue
        from blog import youth_rental_publish as yp
        out = yp.confirm(prev)
        print(prev.name, out)
        _report_outcome(prev, out)
    print(json.dumps({"watch_hits": hits}, ensure_ascii=False))
    if a.cmd == "watch" or (day.weekday() != 5 and not a.any_day):
        return 0
    if day < SERIES_START:
        print(f"SKIP: 주간 연재는 {SERIES_START} 부터")
        return 0
    st = build(day, out_dir, rows=rows)
    print(json.dumps({k: st.get(k) for k in ("state", "n", "title", "rent_parsed")}, ensure_ascii=False))
    if st.get("state") == "SKIPPED_EMPTY":
        if not st.get("notified"):
            notify(f"[청년안심주택 주간] {st.get('note')} — 발행 생략")
            write_state(out_dir, notified=True)
        return 0
    if not a.publish or st.get("state") == "PUBLISHED":
        return 0
    from blog import youth_rental_publish as yp
    out = yp.run_locked(out_dir, publish=True)
    print(out)
    _report_outcome(out_dir, out)
    return 0 if not out.startswith("ERR") else 1


def _report_outcome(out_dir: Path, out: str) -> None:
    """발행 결과 알림 — 완료(공개 대조 결과 포함)·실패·6시간 넘은 미확인을 한 번씩."""
    st = read_state(out_dir)
    if st.get("state") == "PUBLISHED" and not st.get("notified"):
        ok = st.get("public_check_ok")
        notify(f"[청년안심주택 주간] 발행 완료{'' if ok else ' — 공개 본문 대조 불일치, 확인 필요'}: "
               f"{st.get('title')}\n{st.get('published_url')}")
        write_state(out_dir, notified=True)
    elif out.startswith(("ERR", "REFUSE")):
        notify(f"[청년안심주택 주간] 발행 실패: {out[:300]}")
    elif st.get("state") == "PUBLISH_ATTEMPTED" and not st.get("stall_notified"):
        at = dt.datetime.fromisoformat(st.get("publish_attempt_at") or dt.datetime.now(KST).isoformat())
        if dt.datetime.now(KST) - at > dt.timedelta(hours=6):
            notify(f"[청년안심주택 주간] 발행 클릭 뒤 6시간째 공개 목록에 없음(재클릭 안 함) — 상주 크롬 탭·관리 화면 확인: "
                   f"{st.get('title')}")
            write_state(out_dir, stall_notified=True)


if __name__ == "__main__":
    sys.exit(main())
