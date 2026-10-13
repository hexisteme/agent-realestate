"""시장흐름대시보드(MarketDashboard) — 일간 글 첫 화면의 공통 차트 모델과 결정론 PNG(2026-10-10).

축·증감·문구를 이 모듈 한 곳에서 계산하고, 사이트와 티스토리는 같은 PNG 바이트를 쓴다.
티스토리는 붙여넣기에서 alt·title·svg 를 지우므로 값 표(dashboard_table_rows)와 읽는법(reading_notes)을
이미지 밖 텍스트로 반드시 함께 싣는다. 증감은 정확한 달력월끼리만 계산하고 빈 달을 건너뛰어 비교하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import io
import math
from pathlib import Path
import re

from agent_realestate.collectors.macro import ECOS_EXPECTED_UNITS
from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok

DASHBOARD_MONTHS = 24
DASHBOARD_WIDTH = 800  # 티스토리 모바일 본문 폭 310px 에서 가장 작은 글자가 10px 이상 되게(1080 이면 8px)
_SCALE = 2
_FRESH_DAYS = 100
_INDEX_UNIT = re.compile(r"(\d{4})\.(\d{2})=100")

_INK = (15, 23, 42)
_MUTED = (100, 116, 139)
_GRID = (226, 232, 240)
_SALE = (15, 118, 110)
_JEONSE = (180, 83, 9)
_RATE = (29, 78, 216)
_BASE = (100, 116, 139)
_BAR = (203, 213, 225)

_SOURCE_LINES = ("출처 한국부동산원(가격지수·R-ONE 거래량) · 한국은행 ECOS(금리)", "정리 서울 아파트 실거래 데이터랩")


@dataclass(frozen=True)
class SeriesPanel:
    """월별 관측 패널 — points 는 ('YYYY-MM', 값) 오름차순, 증감은 정확한 전월·전년동월만."""

    code: str
    unit: str
    points: tuple[tuple[str, float], ...]
    mom: float | None
    yoy: float | None

    @property
    def latest(self) -> tuple[str, float]:
        return self.points[-1]


@dataclass(frozen=True)
class MarketDashboard:
    """시장흐름대시보드 — 매매·전세 가격지수, 매매거래 동(호)수, 주담대 신규취급 금리 + 기준금리."""

    asof: str
    today: str
    sale: SeriesPanel | None
    jeonse: SeriesPanel | None
    trades: SeriesPanel | None
    mortgage: SeriesPanel | None
    base_rate: tuple[tuple[str, float], ...]
    base_label: str | None

    @property
    def spread(self) -> float | None:
        """주담대 최신월 금리 − 같은 달 말 기준금리(%p)."""
        if not self.mortgage or not self.base_rate:
            return None
        month, value = self.mortgage.latest
        base = dict(self.base_rate).get(month)
        return None if base is None else round(value - base, 2)


@dataclass(frozen=True)
class DashboardImage:
    path: str
    name: str
    sha256: str
    width: int
    height: int


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _month_shift(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + m - 1 + delta
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def _monthly(item, *, unit_ok, integer=False, asof, today, months):
    """검증된 월 관측만 남긴다 — 단위·주기·미래·신선도·중복충돌·정수(호) 검사."""
    if not isinstance(item, dict) or item.get("freq") != "monthly" or not unit_ok(item.get("unit")):
        return None
    latest = _day(item.get("date"))
    if not latest or latest > asof or not 0 <= (today - latest).days <= _FRESH_DAYS:
        return None
    seen: dict[str, float] = {}
    conflicts: set[str] = set()
    for row in item.get("series") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        day, value = _day(row[0]), row[1]
        if not day or day > latest or not _finite(value) or (integer and value != int(value)):
            continue
        key = row[0][:7]
        if key in conflicts:
            continue
        if key in seen and seen[key] != value:
            conflicts.add(key)
            del seen[key]
        else:
            seen[key] = float(value)
    if latest.isoformat()[:7] not in seen:
        return None
    first = _month_shift(latest.isoformat()[:7], -(months - 1))
    points = tuple(sorted((k, v) for k, v in seen.items() if k >= first))
    return points if len(points) >= 2 else None


def _panel(code, unit, points, *, ratio):
    if not points:
        return None
    lookup = dict(points)
    month, value = points[-1]

    def delta(other):
        base = lookup.get(_month_shift(month, other))
        if base is None:
            return None
        if ratio:
            return round((value / base - 1) * 100, 1) if base else None
        return round(value - base, 2)

    return SeriesPanel(code, unit, points, delta(-1), delta(-12))


def _base_rate_steps(item, months: tuple[str, ...]) -> tuple[tuple[str, float], ...]:
    """기준금리 변경 이벤트 → 각 달 말일에 유효한 값."""
    if not isinstance(item, dict) or item.get("unit") != "%" or not months:
        return ()
    events = sorted((r[0], float(r[1])) for r in item.get("series") or []
                    if isinstance(r, (list, tuple)) and len(r) == 2 and _day(r[0]) and _finite(r[1]))
    out = []
    for month in months:
        end = _month_shift(month, 1) + "-01"
        in_force = [v for d, v in events if d < end]
        if in_force:
            out.append((month, in_force[-1]))
    return tuple(out)


def build_market_dashboard(snapshot: dict | None, today: str, months: int = DASHBOARD_MONTHS) -> MarketDashboard | None:
    """거시 스냅샷(visual_snapshot) → 대시보드 모델. 그릴 패널이 하나도 없으면 None."""
    current = _day(today)
    if not isinstance(snapshot, dict) or not current:
        return None
    asof = _day(snapshot.get("asof"))
    raw = snapshot.get("indicators")
    if not asof or asof > current or (current - asof).days > 7 or not isinstance(raw, dict):
        return None

    base_month = {}

    def index_unit(unit):
        """수집기가 ECOS 응답에서 검증한 기준시점 단위만 — 기준 개편 시 수집기와 함께 바뀐다."""
        match = _INDEX_UNIT.fullmatch(unit) if isinstance(unit, str) else None
        if not match or unit != ECOS_EXPECTED_UNITS["seoul_apt_sale"]:
            return False
        if date(int(match[1]), int(match[2]), 1) > asof:
            return False
        base_month[unit] = f"{match[1]}년 {int(match[2])}월"
        return True

    common = {"asof": asof, "today": current, "months": months}
    sale = _monthly(raw.get("seoul_apt_sale"), unit_ok=index_unit, **common)
    jeonse = _monthly(raw.get("seoul_apt_jeonse"), unit_ok=index_unit, **common)
    trades = _monthly(raw.get("seoul_apt_sales"), unit_ok=lambda u: u == "호", integer=True, **common)
    rate = _monthly(raw.get("kr_mortgage_new"), unit_ok=lambda u: u == "%", **common)
    panels = {
        "sale": _panel("seoul_apt_sale", raw.get("seoul_apt_sale", {}).get("unit"), sale, ratio=True),
        "jeonse": _panel("seoul_apt_jeonse", raw.get("seoul_apt_jeonse", {}).get("unit"), jeonse, ratio=True),
        "trades": _panel("seoul_apt_sales", "호", trades, ratio=True),
        "mortgage": _panel("kr_mortgage_new", "%", rate, ratio=False),
    }
    if not any(panels.values()):
        return None
    index_panel = panels["sale"] or panels["jeonse"]
    base_label = base_month.get(index_panel.unit) if index_panel else None
    steps = _base_rate_steps(raw.get("bok_base"), tuple(m for m, _ in rate) if rate else ())
    return MarketDashboard(asof.isoformat(), current.isoformat(), base_rate=steps, base_label=base_label, **panels)


# ── 텍스트(이미지 밖 필수) ─────────────────────────────────────────────────────

def _signed(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    digits = 2 if unit == "%p" else 1
    return f"{'+' if value > 0 else '−' if value < 0 else '±'}{abs(value):.{digits}f}{unit}"


def _month_label(month: str) -> str:
    return f"{int(month[5:7])}월"


def format_value(panel: SeriesPanel, value: float) -> str:
    if panel.unit == "호":
        return f"{value:,.0f}호"
    if panel.unit == "%":
        return f"{value:.2f}%"
    return f"{value:.1f}"


def dashboard_table_rows(d: MarketDashboard | None, n: int = 6) -> list[tuple[str, str, str, str, str]]:
    """값 표(이미지 대체 텍스트) — 최근 n개 달력월 × (매매지수, 전세지수, 거래 호수, 주담대 %). 빈 칸은 —."""
    if d is None:
        return []
    panels = (d.sale, d.jeonse, d.trades, d.mortgage)
    last = max(p.latest[0] for p in panels if p)
    rows = []
    for k in range(n - 1, -1, -1):
        month = _month_shift(last, -k)
        cells = []
        for panel in panels:
            value = dict(panel.points).get(month) if panel else None
            if value is None:
                cells.append("—")
            elif panel.unit == "호":
                cells.append(f"{value:,.0f}")
            elif panel.unit == "%":
                cells.append(f"{value:.2f}")
            else:
                cells.append(f"{value:.1f}")
        rows.append((month, *cells))
    return rows


def reading_notes(d: MarketDashboard | None) -> list[tuple[str, str, str]]:
    """읽는법 — (지표, 의미 1문장, 산식·기준 1문장). 출처 원문(한국부동산원 통계설명·R-ONE 유의사항·한국은행 통계정보) 사실만."""
    if d is None:
        return []
    notes = []
    index = d.sale or d.jeonse
    if index and d.base_label:
        level = index.latest[1] - 100
        notes.append((
            "매매·전세 가격지수",
            f"{d.base_label}=100인 상대 지수입니다. {index.latest[1]:.1f}이면 기준월보다 {abs(level):.1f}% "
            f"{'높은' if level >= 0 else '낮은'} 수준입니다.",
            "한국부동산원 조사 표본 아파트의 가격 변화를 제본스(기하평균) 방식으로 이은 값이며, 개별 단지 가격이 아닙니다.",
        ))
    if d.trades:
        notes.append((
            "아파트 매매거래량",
            "서울 아파트 매매 신고 건수(호 단위) 합계입니다. 매물 수와 다릅니다.",
            "한국부동산원 R-ONE, 신고일 기준 집계. 법정 신고기한이 계약 후 30일이라 계약 시점과 어긋나며, 기한을 넘긴 신고·정정도 있습니다.",
        ))
    if d.mortgage:
        notes.append((
            "주택담보대출 금리",
            "예금은행 신규취급 주택담보대출 금리의 평균입니다. 개인 적용금리와 다릅니다.",
            "한국은행: 그달 새로 취급한 대출 금리를 취급금액으로 가중평균. 점선은 한국은행 기준금리입니다.",
        ))
    for note in notes:
        assert_wording_ok(" ".join(note), "market_charts:reading_notes")
    return notes


def _trend_windows(asof: str) -> tuple[str, str]:
    """3·9개월 비교의 실제 계약월 창(당월 제외, build_explorer._month_windows 와 같은 정의) — '(2026-07~09)' 꼴."""
    from blog.build_explorer import _month_windows
    try:
        windows = _month_windows(asof)
    except (TypeError, ValueError):
        return "", ""

    def fmt(months):
        a, b = min(months), max(months)
        end = b[4:] if a[:4] == b[:4] else f"{b[:4]}-{b[4:]}"
        return f"({a[:4]}-{a[4:]}~{end})"
    return fmt(windows[0]), fmt(windows[1])


def observation_summary_lines(counts: dict, d: MarketDashboard | None, *, asof: str) -> list[str]:
    """관측요약 두 줄 — ① 오늘 실거래 관측 ② 월간 공식 통계. 슬롯만 채우는 고정 템플릿, 평가·전망·권유 없음."""
    lines = []
    if all(isinstance(counts.get(k), int) for k in ("n_total", "n_judged", "up", "down")):
        # 표본기준추세수(GatedTrendCount) — 같은 글의 제목·사실 리드 S1 과 같은 규칙·같은 숫자.
        # 외부 리뷰(2026-10-13) C3·C4·C5·C10: 관측 대비 비율·최근 3개월 5건+ 보조 집계·깔때기·구성 효과를 붙인다.
        n_total, n_judged = counts["n_total"], counts["n_judged"]
        share = f"(관측의 {n_judged / n_total * 100:.1f}%)" if n_total else ""
        recent, prior = _trend_windows(asof)
        line = (f"{asof} 관측 {n_total:,}단지 중 3·9개월 비교가 가능한 {n_judged:,}단지{share}에서 "
                f"최근 3개월{recent} 실거래 중위가 직전 9개월{prior}보다 높은 곳 {counts['up']:,} · 낮은 곳 {counts['down']:,}"
                + (f" · 보합 {flat:,}." if (flat := n_judged - counts["up"] - counts["down"]) else "."))
        if counts.get("n_robust"):
            line += (f" 최근 3개월 거래 {counts['robust_min_recent']}건 이상인 {counts['n_robust']:,}단지만 보면 "
                     f"높은 곳 {counts['up_robust']:,} · 낮은 곳 {counts['down_robust']:,}"
                     + (f" · 보합 {rflat:,}." if (rflat := counts["n_robust"] - counts["up_robust"] - counts["down_robust"]) else "."))
        lines.append(line)
        f = counts.get("funnel")
        # C3(R2 Codex): 단계 숫자와 함께 단계별 제외 사유·수(미산출은 '거래 없음'이 아니라 중위를 못 낸 것).
        scope = (f"집계 범위: 관측 {f['n_total']:,} → 12개월 가격 {f['n_priced']:,}(가격 미산출 {f['n_no_price']:,}) → "
                 f"아파트·전용 40㎡+·매매 10건+ {f['n_gated']:,}(비아파트 {f['excl_non_apt']:,} · 40㎡ 미만 {f['excl_small']:,} · "
                 f"매매 10건 미만 {f['excl_thin']:,}) → 3·9개월 각 2건+ {f['n_judged']:,}(한 기간이라도 2건 미만 {f['n_unjudged']:,}). ") if f else ""
        lines.append(scope + "중위 변화에는 두 기간에 거래된 동·층·면적 구성 차이도 섞여 같은 집의 가격 변화와 다릅니다.")
    parts = []
    if d and d.sale:
        month, value = d.sale.latest
        parts.append(f"{_month_label(month)} 매매가격지수 {value:.1f}(전월 대비 {_signed(d.sale.mom, '%')})")
    if d and d.trades:
        month, value = d.trades.latest
        parts.append(f"{_month_label(month)} 매매 신고 {value:,.0f}호(전월 대비 {_signed(d.trades.mom, '%')})")
    if d and d.mortgage:
        month, value = d.mortgage.latest
        parts.append(f"{_month_label(month)} 주담대 신규취급 금리 {value:.2f}%")
    if parts:
        lines.append("공식 통계: " + " · ".join(parts) + ".")
    if lines and not lines[0].startswith(asof):
        lines[0] = f"{asof} 관측 · " + lines[0]
    for line in lines:
        assert_lead_wording_ok(line, "market_charts:observation_summary")
    return lines


def compose_observation_summary(counts: dict, d: MarketDashboard | None, *, asof: str) -> str:
    """관측요약 한 문단(observation_summary_lines 를 잇는다)."""
    return " ".join(observation_summary_lines(counts, d, asof=asof))


# ── 결정론 PNG ─────────────────────────────────────────────────────────────────

_GLYPH_FALLBACK = str.maketrans({"\u2212": "\u2013"})  # 산돌고딕Neo 에 수학 마이너스 글리프가 없다


class _Canvas:
    def __init__(self, width, height, font_path):
        from blog.thumbnail_card import _pillow
        Image, ImageDraw, self._ImageFont = _pillow()
        self._Image = Image
        self.image = Image.new("RGB", (width * _SCALE, height * _SCALE), (255, 255, 255))
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = Path(font_path)
        self._fonts = {}

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self._fonts:
            index = 6 if bold and self.font_path.name == "AppleSDGothicNeo.ttc" else 0
            self._fonts[key] = self._ImageFont.truetype(str(self.font_path), size=size * _SCALE, index=index)
        return self._fonts[key]

    def text(self, xy, value, size, fill=_INK, bold=False, anchor="la"):
        value = value.translate(_GLYPH_FALLBACK)
        self.draw.text((xy[0] * _SCALE, xy[1] * _SCALE), value, font=self.font(size, bold), fill=fill, anchor=anchor)

    def width_of(self, value, size, bold=False):
        return self.draw.textlength(value.translate(_GLYPH_FALLBACK), font=self.font(size, bold)) / _SCALE

    def line(self, points, fill, width, dash=None):
        pts = [(x * _SCALE, y * _SCALE) for x, y in points]
        if not dash:
            self.draw.line(pts, fill=fill, width=width * _SCALE, joint="curve")
            return
        on, off = dash[0] * _SCALE, dash[1] * _SCALE
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            pos = 0.0
            while pos < length:
                end = min(pos + on, length)
                a, b = pos / length, end / length
                self.draw.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=fill, width=width * _SCALE)
                pos += on + off

    def rect(self, box, fill):
        self.draw.rectangle([v * _SCALE for v in box], fill=fill)

    def dot(self, xy, r, fill):
        x, y = xy[0] * _SCALE, xy[1] * _SCALE
        self.draw.ellipse([x - r * _SCALE, y - r * _SCALE, x + r * _SCALE, y + r * _SCALE], fill=fill)

    def png(self):
        out = self.image.resize((self.image.width // _SCALE, self.image.height // _SCALE), self._Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


_PAD = 48
_GUTTER = 84  # 왼쪽 눈금 글자 자리 — 선이 눈금 글자와 겹치지 않게 플롯 밖에 둔다
_PANEL_H = 440
_HEADER_H = 160
_FOOTER_H = 100


def _short_month(month: str) -> str:
    return f"{month[2:4]}.{month[5:7]}"


def _nice_ticks(lo: float, hi: float, target: int = 4) -> list[float]:
    """lo~hi 를 덮는 둥근 눈금(간격 = {1, 2, 2.5, 5}×10^k, 3~6개). 값이 모두 같으면 위아래로 넓혀 축을 만든다."""
    if hi - lo < 1e-9:
        pad = max(abs(lo) * 0.02, 0.5)
        lo, hi = lo - pad, hi + pad
    span = hi - lo
    exp = math.floor(math.log10(span / (target - 1)))
    for mantissa in (1, 2, 2.5, 5, 10):
        step = mantissa * 10 ** exp
        first = math.floor(lo / step + 1e-9) * step
        last = math.ceil(hi / step - 1e-9) * step
        if round((last - first) / step) + 1 <= 6:
            break
    ticks = [round(first + i * step, 10) for i in range(round((last - first) / step) + 1)]
    while len(ticks) < 3:
        ticks = [round(ticks[0] - step, 10), *ticks, round(ticks[-1] + step, 10)][:3 if len(ticks) == 1 else 4]
    return ticks


def _tick_decimals(ticks, minimum=0):
    step = ticks[1] - ticks[0]
    return max(minimum, next(d for d in range(4) if abs(round(step, d) - step) < 1e-9))


def _month_gap(a: str, b: str) -> int:
    return abs((int(a[:4]) - int(b[:4])) * 12 + int(a[5:7]) - int(b[5:7]))


def _x_labels(months, min_gap: int = 3) -> list[str]:
    """가로축 글자: 처음·끝 달과, 양 끝에서 min_gap 개월 이상 떨어진 1월(연도 경계)."""
    if not months:
        return []
    first, last = months[0], months[-1]
    marks = [m for m in months[1:-1]
             if m.endswith("-01") and _month_gap(m, first) >= min_gap and _month_gap(m, last) >= min_gap]
    return [first, *marks] + ([last] if last != first else [])


def _x_positions(months, left, right):
    if len(months) == 1:
        return {months[0]: (left + right) / 2}
    first = months[0]
    total = (int(months[-1][:4]) - int(first[:4])) * 12 + int(months[-1][5:7]) - int(first[5:7])
    return {m: left + (right - left) * ((int(m[:4]) - int(first[:4])) * 12 + int(m[5:7]) - int(first[5:7])) / total
            for m in months}


def _segments(points, xpos, ymap):
    """연속 달력월끼리만 선으로 잇는다(빈 달을 건너 연결하지 않음)."""
    runs, run = [], []
    for month, value in points:
        if run and _month_shift(run[-1][0], 1) != month:
            runs.append(run)
            run = []
        run.append((month, value))
    if run:
        runs.append(run)
    return [[(xpos[m], ymap(v)) for m, v in r] for r in runs]


def _axis(c, top, bottom, left, right, ticks, fmt, months, xpos):
    """둥근 눈금 격자 + 왼쪽 여백 눈금 글자 + 가로축 달 글자. xpos: 달 → (왼끝, 가운데, 오른끝) x."""
    lo, hi = ticks[0], ticks[-1]
    for value in ticks:
        y = bottom - (bottom - top) * (value - lo) / (hi - lo)
        c.line([(left, y), (right, y)], _GRID, 1)
        c.text((left - 10, y), fmt(value), 28, _MUTED, anchor="rm")
    # 끝 글자(왼·오른쪽 정렬)와 가운데 정렬 1월 글자가 겹치지 않는 최소 달 간격 — 플롯 폭에서 계산한다.
    label_width = c.width_of("00.00", 28, False)
    span = max(1, _month_gap(months[-1], months[0])) if months else 1
    min_gap = max(3, math.ceil((1.5 * label_width + 8) / ((right - left) / span)))
    labels = _x_labels(months, min_gap)
    for i, month in enumerate(labels):
        x0, mid, x1 = xpos(month)
        if i == 0:
            c.text((x0, bottom + 12), _short_month(month), 28, _MUTED, anchor="la")
        elif i == len(labels) - 1:
            c.text((x1, bottom + 12), _short_month(month), 28, _MUTED, anchor="ra")
        else:
            c.text((mid, bottom + 12), _short_month(month), 28, _MUTED, anchor="ma")


def _panel_header(c, y, title, lines) -> int:
    """패널 제목과 머리줄 값. 폭을 넘는 값은 다음 줄로 넘기고, 그 아래 플롯 윗변 y 를 돌려준다."""
    c.text((_PAD, y), title, 40, _INK, bold=True)
    x, row = _PAD, y + 60
    for text, color, bold in lines:
        width = c.width_of(text, 34, bold)
        if x > _PAD and x + width > DASHBOARD_WIDTH - _PAD:
            x, row = _PAD, row + 44
        c.text((x, row), text, 34, color, bold=bold)
        x += width + 20
    return row + 80


def _missing(c, y, title):
    c.text((_PAD, y), title, 40, _INK, bold=True)
    c.text((_PAD, y + 64), "자료 미수집 — 출처 확인 중", 34, _MUTED)


def _draw_prices(c, y, d):
    title = "매매·전세 가격지수" + (f" ({d.base_label}=100)" if d.base_label else "")
    panels = [p for p in (d.sale, d.jeonse) if p]
    if not panels:
        return _missing(c, y, title)
    lines = []
    for label, p, color in (("매매", d.sale, _SALE), ("전세", d.jeonse, _JEONSE)):
        if p:
            lines.append((f"{label} {p.latest[1]:.1f}", color, True))
            lines.append((f"전월 {_signed(p.mom, '%')}", _MUTED, False))
    top = _panel_header(c, y, title, lines)
    bottom, left, right = y + _PANEL_H - 66, _PAD + _GUTTER, DASHBOARD_WIDTH - _PAD - 92
    months = sorted({m for p in panels for m, _ in p.points})
    values = [v for p in panels for _, v in p.points] + [100.0]
    ticks = _nice_ticks(min(values), max(values))
    lo, hi = ticks[0], ticks[-1]
    ymap = lambda v: bottom - (bottom - top) * (v - lo) / (hi - lo)
    xpos = _x_positions(months, left, right)
    digits = _tick_decimals(ticks)
    _axis(c, top, bottom, left, right, ticks, lambda v: f"{v:.{digits}f}", months, lambda m: (xpos[m],) * 3)
    c.line([(left, ymap(100.0)), (right, ymap(100.0))], _MUTED, 1, dash=(4, 6))
    for p, color, dash in ((d.sale, _SALE, None), (d.jeonse, _JEONSE, (14, 8))):
        if not p:
            continue
        for seg in _segments(p.points, xpos, ymap):
            c.line(seg, color, 4, dash=dash)
        end = (xpos[p.latest[0]], ymap(p.latest[1]))
        c.dot(end, 6, color)
    if d.sale and d.jeonse:
        ys = sorted([(ymap(d.sale.latest[1]), "매매", _SALE), (ymap(d.jeonse.latest[1]), "전세", _JEONSE)])
        if ys[1][0] - ys[0][0] < 36:
            mid = (ys[0][0] + ys[1][0]) / 2
            ys = [(mid - 19, *ys[0][1:]), (mid + 19, *ys[1][1:])]
        for yy, label, color in ys:
            c.text((right + 12, yy), label, 30, color, bold=True, anchor="lm")
    else:
        p = panels[0]
        c.text((right + 12, ymap(p.latest[1])), "매매" if p is d.sale else "전세", 30,
               _SALE if p is d.sale else _JEONSE, bold=True, anchor="lm")


def _draw_trades(c, y, d):
    title = "아파트 매매 거래량 (월별 신고, 호)"
    p = d.trades
    if not p:
        return _missing(c, y, title)
    month, value = p.latest
    change = f"전월 {_signed(p.mom, '%')}" + (f" · 1년 전보다 {_signed(p.yoy, '%')}" if p.yoy is not None else "")
    top = _panel_header(c, y, title, [(f"{_month_label(month)} {value:,.0f}호", _SALE, True), (change, _MUTED, False)])
    bottom, left, right = y + _PANEL_H - 66, _PAD + _GUTTER, DASHBOARD_WIDTH - _PAD - 92
    first = p.points[0][0]
    months = [first]
    while months[-1] < month:
        months.append(_month_shift(months[-1], 1))
    peak = max(v for _, v in p.points)
    ticks = _nice_ticks(0.0, peak * 1.08)
    lo, hi = ticks[0], ticks[-1]
    ymap = lambda v: bottom - (bottom - top) * (v - lo) / (hi - lo)
    step = (right - left) / len(months)
    index = {m: i for i, m in enumerate(months)}
    bar = lambda m: (left + step * index[m] + step * 0.15, left + step * (index[m] + 0.5),
                     left + step * (index[m] + 1) - step * 0.15)
    _axis(c, top, bottom, left, right, ticks, lambda v: f"{v:,.0f}", months, bar)
    lookup = dict(p.points)
    peak_month = max(p.points, key=lambda r: r[1])[0]
    for i, m in enumerate(months):
        if m not in lookup:
            continue
        x0 = left + step * i + step * 0.15
        x1 = left + step * (i + 1) - step * 0.15
        c.rect((x0, ymap(lookup[m]), x1, bottom), _SALE if m == month else _BAR)
        if m == peak_month and m != month:
            c.text(((x0 + x1) / 2, ymap(lookup[m]) - 6), f"{lookup[m]:,.0f}", 28, _MUTED, anchor="md")
    c.text((min(left + step * (len(months) - 0.5), DASHBOARD_WIDTH - _PAD - 50), ymap(value) - 6),
           f"{value:,.0f}", 30, _SALE, bold=True, anchor="md")


def _draw_rate(c, y, d):
    title = "주택담보대출 금리 (예금은행 신규취급)"
    p = d.mortgage
    if not p:
        return _missing(c, y, title)
    month, value = p.latest
    lines = [(f"{_month_label(month)} {value:.2f}%", _RATE, True), (f"전월 {_signed(p.mom, '%p')}", _MUTED, False)]
    if d.spread is not None:
        lines.append((f"기준금리와 차이 {d.spread:.2f}%p", _MUTED, False))
    top = _panel_header(c, y, title, lines)
    bottom, left, right = y + _PANEL_H - 66, _PAD + _GUTTER, DASHBOARD_WIDTH - _PAD - 120
    months = [m for m, _ in p.points]
    values = [v for _, v in p.points] + [v for _, v in d.base_rate]
    ticks = _nice_ticks(min(values), max(values))
    lo, hi = ticks[0], ticks[-1]
    ymap = lambda v: bottom - (bottom - top) * (v - lo) / (hi - lo)
    xpos = _x_positions(months, left, right)
    digits = _tick_decimals(ticks, minimum=1)
    _axis(c, top, bottom, left, right, ticks, lambda v: f"{v:.{digits}f}%", months, lambda m: (xpos[m],) * 3)
    if d.base_rate:
        pts = []
        for m, v in d.base_rate:
            if pts and pts[-1][1] != ymap(v):
                pts.append((xpos[m], pts[-1][1]))
            pts.append((xpos[m], ymap(v)))
        c.line(pts, _BASE, 3, dash=(10, 8))
        c.text((right + 12, pts[-1][1]), "기준금리", 28, _BASE, anchor="lm")
    for seg in _segments(p.points, xpos, ymap):
        c.line(seg, _RATE, 4)
    c.dot((xpos[month], ymap(value)), 6, _RATE)
    c.text((right + 12, ymap(value)), "주담대", 28, _RATE, bold=True, anchor="lm")


def dashboard_height() -> int:
    return _HEADER_H + 3 * _PANEL_H + _FOOTER_H


def render_dashboard_png(d: MarketDashboard, font_path: str | Path | None = None) -> bytes:
    """대시보드 → PNG 바이트(2배 그린 뒤 축소, 메타데이터 없음 — 같은 환경·입력이면 같은 바이트)."""
    from blog.thumbnail_card import _resolve_font
    c = _Canvas(DASHBOARD_WIDTH, dashboard_height(), _resolve_font(font_path))
    c.text((_PAD, 36), "서울 아파트 시장 흐름", 54, _INK, bold=True)
    c.text((_PAD, 106), f"최근 {DASHBOARD_MONTHS}개월 월별 공식 통계 · 확인일 {d.today}", 30, _MUTED)
    y = _HEADER_H
    for draw in (_draw_prices, _draw_trades, _draw_rate):
        c.line([(_PAD, y - 14), (DASHBOARD_WIDTH - _PAD, y - 14)], _GRID, 2)
        draw(c, y, d)
        y += _PANEL_H
    for i, line in enumerate(_SOURCE_LINES):
        c.text((_PAD, y + 8 + i * 38), line, 26, _MUTED)
    return c.png()


def write_dashboard_png(d: MarketDashboard, output_dir: str | Path,
                        font_path: str | Path | None = None) -> DashboardImage:
    """원자적 쓰기 — 이름에 내용 해시를 넣어 같은 날 재생성과 캐시를 구분한다."""
    from blog.thumbnail_card import _write_card_bytes
    payload = render_dashboard_png(d, font_path)
    digest = hashlib.sha256(payload).hexdigest()
    name = f"dashboard-{d.today}-{digest[:16]}.png"
    destination = _write_card_bytes(Path(output_dir).resolve(), name, payload)
    return DashboardImage(str(destination), name, digest, DASHBOARD_WIDTH, dashboard_height())
