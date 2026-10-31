"""SH/LH·서울시 청년안심주택 공식 공고와 주거지원 안내. 공고 상태와 실제 신청 기간을 구분한다."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

VERIFIED_DATE = "2026-10-03"
MAX_SNAPSHOT_AGE_DAYS = 3
MAX_NOTICE_AGE_DAYS = 90
MAX_REQUESTS = 5  # 목록 3회(SH·LH·청년안심주택) + SH 상세 최대 2회; 기존 fetch_text의 timeout=15초.
_INPUT_ERROR = "주거지원 공고 입력을 확인할 수 없음"
_PARSE_ERROR = "주거지원 공식 공고 표를 해석할 수 없음"
_FETCH_ERROR = "공식 공고를 자동 확인하지 못함"
_SH_BASE = "https://www.i-sh.co.kr/app/lay2/program/S48T1581C563/www/brd/m_247/"
_LH_BASE = "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/"
_SOCO_BASE = "https://soco.seoul.go.kr/youth/"
_SOCO_LIST_JSON = _SOCO_BASE + "pgm/home/yohome/bbsListJson.json?bbsId=BMSR00015&pageIndex=1"
OFFICIAL_SOURCES = {
    "sh": {"label": "SH", "list_url": _SH_BASE + "list.do?multi_itm_seq=2", "host": "www.i-sh.co.kr", "verified_date": VERIFIED_DATE},
    "lh": {"label": "LH", "list_url": _LH_BASE + "selectWrtancList.do?mi=1026", "host": "apply.lh.or.kr", "verified_date": VERIFIED_DATE},
    "soco": {"label": "청년안심주택", "list_url": _SOCO_BASE + "bbs/BMSR00015/list.do?menuNo=400008", "host": "soco.seoul.go.kr", "verified_date": "2026-10-31"},
    "applyhome": {"label": "청약홈", "list_url": "https://www.applyhome.co.kr/", "host": "www.applyhome.co.kr", "verified_date": VERIFIED_DATE},
}
GUIDANCE = (
    {"id": "youth", "label": "청년", "brief": "LH 청년매입: 미혼·무주택, 대학생·취준생 또는 19~39세",
     "detail": "LH 청년매입임대는 미혼 무주택자 중 대학생·취업준비생·19~39세 경로를 구분합니다. 순위별 본인·부모 소득과 자산 기준은 해당 공고로 확인합니다. 서울시 청년안심주택(공공지원민간임대)은 모집공고일 기준 19~39세 무주택 청년·신혼부부가 대상이며, 특별공급 소득·자산과 자동차가액 기준은 공고별로 확인합니다.",
     "url": "https://apply.lh.or.kr/lhapply/cm/cntnts/cntntsView.do?cntntsId=201251&mi=1150"},
    {"id": "spouses", "label": "신혼", "brief": "LH 신혼매입Ⅰ: 무주택세대, 혼인 7년 이내·예비신혼 등",
     "detail": "LH 신혼매입임대Ⅰ는 무주택세대구성원 요건을 확인하며, 혼인 7년 이내·예비신혼·6세 이하 자녀 한부모·유자녀 혼인가구 등 경로를 구분합니다. 소득·자산·순위는 해당 공고가 기준입니다.",
     "url": "https://apply.lh.or.kr/lhapply/cm/cntnts/cntntsView.do?cntntsId=201254&mi=1150"},
    {"id": "first", "label": "생애최초", "brief": "공공·민영 구분 후 주택소유 이력·통장·소득 확인",
     "detail": "생애최초 특별공급은 공공분양과 민영주택을 구분한 뒤 세대의 주택소유 이력, 청약통장, 소득·자산 등 해당 유형의 공고 요건을 확인합니다.", "url": "https://www.applyhome.co.kr/"},
    {"id": "unranked", "label": "무순위", "brief": "거주지·무주택·재당첨 제한은 해당 유형·공고별 확인",
     "detail": "무순위·임의공급·계약취소주택을 같은 조건으로 보지 않습니다. 거주지역, 주택소유, 재당첨 제한 등은 해당 유형과 공고 원문에서 확인합니다.", "url": "https://www.applyhome.co.kr/"},
    {"id": "rental", "label": "공공임대", "brief": "임대 유형·무주택·소득/자산·우선공급 확인",
     "detail": "공공임대는 영구·국민·행복·매입·전세임대 등 유형을 먼저 확인합니다. 무주택 판단 범위, 소득·자산, 우선공급, 임대조건은 공고별로 다릅니다.",
     "url": _LH_BASE + "selectWrtancList.do?mi=1026"},
)

_PROVINCE = r"경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|충청[남북]?|전북|전남|전라[남북]?|경북|경남|경상[남북]?|제주"
_NON_SEOUL = re.compile(r"\[(?!서울)[가-힣]{2,4}지역본부\]|(?<![가-힣])(?:" + _PROVINCE + r")(?:광역시|특별시|특별자치도|특별자치시|도|시|(?![가-힣]))")


def is_seoul_relevant(title: str) -> bool:
    """서울 글에 실을 공고인가 — '전국' 공고 중 제목이 서울 밖 지역본부·시도('경남 의령군' 같은 약칭 포함)를 명시한 것은 뺀다."""
    return not _NON_SEOUL.search(title or "")


def _iso(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(_INPUT_ERROR)
    return date.fromisoformat(value)


def _official_url(issuer, value):
    parsed = urlsplit(value)
    if (issuer not in OFFICIAL_SOURCES or parsed.scheme != "https" or parsed.hostname != OFFICIAL_SOURCES[issuer]["host"]
            or parsed.username or parsed.password or parsed.port or parsed.fragment
            or any(re.search(r"token|secret|password|auth|session|api.?key", key, re.I) for key in parse_qs(parsed.query))):
        raise ValueError(_INPUT_ERROR)


def _instant(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", value):
        raise ValueError(_INPUT_ERROR)
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(hours=9): raise ValueError(_INPUT_ERROR)
    return result


@dataclass(frozen=True)
class HousingNotice:
    issuer: str
    title: str
    url: str
    published_on: str
    scope: str
    apply_start: str | None = None
    apply_end: str | None = None
    announcement_end: str | None = None
    declared_state: str = ""
    apply_start_at: str | None = None
    apply_end_at: str | None = None
    eligibility_brief: str = ""

    def __post_init__(self):
        try:
            _official_url(self.issuer, self.url)
            _iso(self.published_on)
            for value in (self.apply_start, self.apply_end, self.announcement_end):
                if value is not None:
                    _iso(value)
            if self.apply_start and self.apply_end and self.apply_end < self.apply_start:
                raise ValueError(_INPUT_ERROR)
            for instant, day in ((self.apply_start_at, self.apply_start), (self.apply_end_at, self.apply_end)):
                if instant is not None and _instant(instant).date().isoformat() != day: raise ValueError(_INPUT_ERROR)
            if self.apply_start_at and self.apply_end_at and self.apply_end_at <= self.apply_start_at: raise ValueError(_INPUT_ERROR)
            if self.scope not in ("서울", "전국") or not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 300:
                raise ValueError(_INPUT_ERROR)
            if not isinstance(self.declared_state, str) or len(self.declared_state) > 30:
                raise ValueError(_INPUT_ERROR)
            if not isinstance(self.eligibility_brief, str) or len(self.eligibility_brief) > 500: raise ValueError(_INPUT_ERROR)
        except Exception:
            raise ValueError(_INPUT_ERROR) from None

    def as_dict(self, today, *, asof_datetime=None):
        return {**asdict(self), "status": notice_status(self, today, asof_datetime=asof_datetime),
                "status_basis": "확인 시각 기준" if asof_datetime and self.apply_start_at and self.apply_end_at else "일자 기준; 시작·마감일 접수시각 확인"}


def notice_status(notice: HousingNotice, today: str, *, asof_datetime=None) -> str:
    _iso(today)
    if not notice.apply_start or not notice.apply_end:
        return "미확인"
    if asof_datetime and notice.apply_start_at and notice.apply_end_at:
        asof = _instant(asof_datetime)
        if asof.date().isoformat() != today: raise ValueError(_INPUT_ERROR)
        return "예정" if asof < _instant(notice.apply_start_at) else "마감" if asof >= _instant(notice.apply_end_at) else "접수중"
    if today in (notice.apply_start, notice.apply_end): return "미확인"
    return "예정" if today < notice.apply_start else "마감" if today > notice.apply_end else "접수중"


class _NoticeTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.rows, self.row, self.cell = [], None, None, None
        self.skip = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table": self.rows = []
        elif tag == "tr" and self.rows is not None: self.row = []
        elif tag in ("td", "th") and self.row is not None: self.cell = {"parts": [], "anchors": []}
        elif tag == "a" and self.cell is not None: self.cell["anchors"].append(attrs)
        elif tag == "em" and "day" in attrs.get("class", "").split(): self.skip = True

    def handle_data(self, value):
        if self.cell is not None and not self.skip: self.cell["parts"].append(value)

    def handle_endtag(self, tag):
        if tag == "em": self.skip = False
        elif tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = " ".join("".join(self.cell.pop("parts")).split())
            self.row.append(self.cell); self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append(self.rows); self.rows = None


def _notice_rows(text, required):
    parser = _NoticeTables(); parser.feed(text); parser.close()
    matches = [rows for rows in parser.tables if rows and set(required) <= {cell["text"] for cell in rows[0]}]
    if len(matches) != 1:
        raise ValueError(_PARSE_ERROR)
    rows = matches[0]
    header = [cell["text"] for cell in rows[0]]
    return [{key: row[header.index(key)] for key in required} for row in rows[1:] if len(row) == len(header)]


def _calendar_text(text):
    return _iso(text.replace(".", "-")).isoformat()


def parse_lh_notice_list(text) -> list[HousingNotice]:
    try:
        notices = []
        for row in _notice_rows(text, ("공고명", "지역", "게시일", "마감일", "상태")):
            region = row["지역"]["text"]
            if region not in ("서울", "서울특별시", "전국"): continue
            anchor = next(a for a in row["공고명"]["anchors"] if "wrtancInfoBtn" in a.get("class", "").split())
            values = [anchor.get(f"data-id{i}", "") for i in range(1, 5)]
            if not all(re.fullmatch(r"\d{1,20}", value) for value in values): raise ValueError(_PARSE_ERROR)
            query = urlencode(dict(zip(("panId", "ccrCnntSysDsCd", "uppAisTpCd", "aisTpCd"), values)))
            notices.append(HousingNotice("lh", row["공고명"]["text"], _LH_BASE + "selectWrtancInfo.do?" + query + "&mi=1026",
                _calendar_text(row["게시일"]["text"]), "전국" if region == "전국" else "서울",
                announcement_end=_calendar_text(row["마감일"]["text"]) if row["마감일"]["text"] else None,
                declared_state=row["상태"]["text"]))
        return notices
    except Exception:
        raise ValueError(_PARSE_ERROR) from None


def parse_sh_notice_list(text) -> list[HousingNotice]:
    try:
        notices = []
        for row in _notice_rows(text, ("제목", "등록일")):
            title = row["제목"]["text"]
            if "모집" not in title or not re.search(r"청년|신혼|신생아|임대|생애최초|무순위", title): continue
            seq = next(re.search(r"getDetailView\('([0-9]+)'\)", a.get("onclick", "")) for a in row["제목"]["anchors"])
            if not seq: raise ValueError(_PARSE_ERROR)
            notices.append(HousingNotice("sh", title, _SH_BASE + "view.do?seq=" + seq[1] + "&multi_itm_seq=2", _calendar_text(row["등록일"]["text"]), "서울"))
        return notices
    except Exception:
        raise ValueError(_PARSE_ERROR) from None


class _VisibleText(HTMLParser):
    def __init__(self): super().__init__(); self.parts, self.skip = [], None
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.skip = tag
    def handle_endtag(self, tag):
        if tag == self.skip: self.skip = None
    def handle_data(self, text):
        if not self.skip: self.parts.append(text)


def _plain_text(markup):
    parser = _VisibleText(); parser.feed(markup or ""); parser.close()
    return " ".join(" ".join(parser.parts).split())


_SOCO_DAY = r"(?:[‘’'`]\s*)?(?:(\d{4}|\d{2})\.\s*)?(\d{1,2})\.\s*(\d{1,2})\.?\s*(?:\([^)]{1,3}\)\s*)?"
_SOCO_PERIOD = re.compile(r"청약\s*신청\s*[:：]\s*" + _SOCO_DAY + r"(\d{1,2}):(\d{2})\s*[~～∼]\s*(?:" + _SOCO_DAY + r")?(\d{1,2}):(\d{2})")


def _soco_year(value, fallback):
    return fallback if not value else int(value) + (2000 if len(value) == 2 else 0)


def _soco_schedule(plain):
    """청년안심주택 본문의 '청약신청 : '26. 11. 02. (월) 09:00 ~ 11. 04. (수) 23:00' → 한국시간 ISO 시작·끝. 못 읽으면 (None, None)."""
    found = _SOCO_PERIOD.findall(plain)
    if len(found) != 1: return None, None
    y, m, d, h, mi, y2, m2, d2, h2, mi2 = found[0]
    try:
        if not y: return None, None
        seoul = ZoneInfo("Asia/Seoul")
        start = datetime(_soco_year(y, 0), int(m), int(d), int(h), int(mi), tzinfo=seoul)
        if m2:
            end = datetime(_soco_year(y2, start.year + (int(m2) < start.month)), int(m2), int(d2), int(h2), int(mi2), tzinfo=seoul)
        else:
            end = start.replace(hour=int(h2), minute=int(mi2))
    except ValueError:
        return None, None
    return (start.isoformat(), end.isoformat()) if end > start else (None, None)


def parse_soco_notice_list(text) -> list[HousingNotice]:
    """서울시 청년안심주택 모집공고 JSON 목록. 본문에서 접수 시각을 못 읽으면 목록의 청약신청 시작일만 남긴다."""
    try:
        notices = []
        for row in json.loads(text)["resultList"]:
            board = str(row["boardId"])
            if not re.fullmatch(r"\d{1,12}", board): raise ValueError(_PARSE_ERROR)
            plain = _plain_text(row.get("content"))
            start_at, end_at = _soco_schedule(plain)
            supply = re.search(r"공급\s*호수\s*[:：]\s*(.+?)(?=\s*■|\s*\[|$)", plain)
            notices.append(HousingNotice("soco", " ".join(str(row["nttSj"]).split()),
                _SOCO_BASE + "bbs/BMSR00015/view.do?" + urlencode({"boardId": board, "menuNo": "400008"}), row["optn1"], "서울",
                apply_start=start_at[:10] if start_at else (row.get("optn4") or None), apply_end=end_at[:10] if end_at else None,
                apply_start_at=start_at, apply_end_at=end_at,
                eligibility_brief=("공급호수: " + supply[1])[:200] if supply else ""))
        return notices
    except Exception:
        raise ValueError(_PARSE_ERROR) from None


def parse_sh_application_period(text):
    start, end = parse_sh_application_schedule(text)
    return (start[:10], end[:10]) if start and end else (None, None)


def parse_sh_application_schedule(text):
    try:
        parser = _VisibleText(); parser.feed(text)
        plain = " ".join(" ".join(parser.parts).split())
        ranges = re.findall(r"인터넷접수\s*[:：]\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.(?:\([^)]{1,3}\))?\s*(\d{2}):(\d{2})\s*~\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.(?:\([^)]{1,3}\))?\s*(\d{2}):(\d{2})", plain)
        if not ranges: return None, None
        if len(ranges) != 1: raise ValueError(_PARSE_ERROR)
        y, m, d, h, minute, y2, m2, d2, h2, minute2 = map(int, ranges[0])
        start = datetime(y, m, d, h, minute, tzinfo=ZoneInfo("Asia/Seoul")).isoformat()
        end = datetime(y2, m2, d2, h2, minute2, tzinfo=ZoneInfo("Asia/Seoul")).isoformat()
        if end < start: raise ValueError(_PARSE_ERROR)
        return start, end
    except Exception:
        raise ValueError(_PARSE_ERROR) from None


def empty_housing_support(target_date):
    _iso(target_date)
    return {"schema_version": 1, "target_date": target_date, "collected_at": None,
            "sources": {code: {**source, "state": "미확인", "fetched_on": None} for code, source in OFFICIAL_SOURCES.items()},
            "notices": [], "errors": {}}


def _previous_schedule(item, previous, target_date):
    if not isinstance(previous, dict): return item
    try:
        age = (_iso(target_date) - _iso(previous.get("target_date"))).days
        if previous.get("schema_version") != 1 or not 0 <= age <= MAX_SNAPSHOT_AGE_DAYS: return item
        names = {field.name for field in fields(HousingNotice)}
        for raw in previous.get("notices", []):
            prior = HousingNotice(**{key: value for key, value in raw.items() if key in names})
            identity = ("issuer", "url", "title", "published_on", "scope", "announcement_end")
            if all(getattr(prior, key) == getattr(item, key) for key in identity) and prior.apply_start and prior.apply_end:
                changes = {}
                if not item.eligibility_brief and prior.eligibility_brief:
                    changes["eligibility_brief"] = prior.eligibility_brief
                schedule = ("apply_start", "apply_end", "apply_start_at", "apply_end_at")
                if not any(getattr(item, key) for key in schedule):
                    changes.update({key: getattr(prior, key) for key in schedule})
                return replace(item, **changes) if changes else item
    except (ValueError, TypeError, AttributeError):
        pass
    return item


def collect_housing_support(target_date, *, fetch=None, previous=None):
    """최대 4요청, 기존 text transport timeout=15초. 실패한 이전 공고는 현재 접수 상태로 재사용하지 않는다."""
    if fetch is None:
        from agent_realestate.collectors.macro import fetch_text
        fetch = fetch_text
    today = _iso(target_date)
    result = empty_housing_support(target_date)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    result["collected_at"] = now.isoformat(timespec="seconds") if now.date() == today else None
    lists = (("sh", OFFICIAL_SOURCES["sh"]["list_url"], parse_sh_notice_list),
             ("lh", OFFICIAL_SOURCES["lh"]["list_url"], parse_lh_notice_list),
             ("soco", _SOCO_LIST_JSON, parse_soco_notice_list))
    for code, url, parse in lists:
        try:
            items = parse(fetch(url, encoding="utf-8"))
            items = [item for item in items
                     if 0 <= (today - _iso(item.published_on)).days <= MAX_NOTICE_AGE_DAYS and is_seoul_relevant(item.title)]
            items.sort(key=lambda item: item.published_on, reverse=True)
            if code == "sh":
                for i, item in enumerate(items[:2]):
                    try:
                        start, end = parse_sh_application_schedule(fetch(item.url, encoding="utf-8"))
                        items[i] = replace(item, apply_start=start[:10] if start else None, apply_end=end[:10] if end else None,
                                           apply_start_at=start, apply_end_at=end)
                    except Exception:
                        result["errors"]["sh_detail"] = _FETCH_ERROR
            items = [_previous_schedule(item, previous, target_date) for item in items]
            result["notices"].extend(item.as_dict(target_date, asof_datetime=result["collected_at"]) for item in items[:8])
            result["sources"][code].update(state="확인", fetched_on=target_date)
        except Exception:
            result["errors"][code] = _FETCH_ERROR
    result["errors"]["applyhome"] = "공고 자동 확인 미지원; 공식 공고에서 확인"
    result["notices"].sort(key=lambda item: (item["published_on"], item["issuer"]), reverse=True)
    return result


def save_housing_support(snapshot, directory):
    day = _iso(snapshot.get("target_date")).isoformat()
    if snapshot.get("schema_version") != 1: raise ValueError(_INPUT_ERROR)
    folder = Path(directory); folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"support-{day}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path.resolve())


def load_housing_support_latest(directory, on_or_before):
    today = _iso(on_or_before)
    paths = []
    for path in Path(directory).glob("support-*.json"):
        match = re.fullmatch(r"support-(\d{4}-\d{2}-\d{2})\.json", path.name)
        if not match: continue
        try:
            day = _iso(match[1])
            if day <= today: paths.append((day, path))
        except ValueError: continue
    for day, path in sorted(paths, reverse=True):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if item.get("schema_version") == 1 and item.get("target_date") == day.isoformat(): return item
        except (ValueError, OSError, AttributeError): continue
    return None
