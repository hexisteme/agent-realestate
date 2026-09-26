"""실거래 관측 데이터로 만드는 결정론적 1200×630 대표 카드.

원본 행에서 수치를 계산하고, 미확인 입력은 숫자를 대신해 명시한다.
동일 데이터와 같은 폰트/Pillow 버전에서는 PNG 바이트가 동일하다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import hashlib
import html
from io import BytesIO
import math
import os
from pathlib import Path
import statistics
import tempfile
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from blog.brand_identity import BRAND_NAME


CARD_WIDTH = 1200
CARD_HEIGHT = 630
CARD_MAX_BYTES = 500_000
CARD_BRAND = BRAND_NAME
CARD_SOURCE = "국토교통부 RTMS · rt.molit.go.kr"
CARD_NOTICE = "신고·정정 지연으로 수치가 바뀔 수 있습니다."
_FONT_CANDIDATES = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


@dataclass(frozen=True)
class DailyCardData:
    """대표 카드에 인쇄할 관측값과 계산 범위."""

    observed_date: str
    data_asof: str
    complex_count: int | None
    sample_count: int | None
    up_count: int | None
    down_count: int | None
    median_eok: float | None
    median_complex_count: int
    band_counts: tuple[int, int, int, int]
    issues: tuple[str, ...]

    @property
    def alt(self) -> str:
        """HTML 대체 텍스트에도 이미지와 같은 실제 수치를 사용한다."""
        count = "미확인" if self.complex_count is None else f"{self.complex_count:,}"
        sample = "미확인" if self.sample_count is None else f"{self.sample_count:,}"
        up = "미확인" if self.up_count is None else f"{self.up_count:,}"
        down = "미확인" if self.down_count is None else f"{self.down_count:,}"
        return (f"{CARD_BRAND}. 관측일 {self.observed_date}, 데이터 기준 {self.data_asof}. "
                "수집 대상은 아파트·다세대·연립 등 주거 유형입니다. "
                f"관측 {count}단지, 단지 대표면적 실거래 표본 {sample}건, "
                f"상승 {up}단지·하락 {down}단지. "
                f"출처 국토교통부 RTMS. {CARD_NOTICE}")


@dataclass(frozen=True)
class ThumbnailCard:
    """원자적 출력 이후 파일과 공개 메타데이터를 연동할 수 있는 영수증."""

    path: str
    sha256: str
    width: int
    height: int
    alt: str
    byte_count: int
    observed_date: str
    data_asof: str
    font_path: str


def _date_label(raw: Any) -> str:
    if not isinstance(raw, str):
        return "미확인"
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return "미확인"
    return value.isoformat()


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _count(raw: Any) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw if 0 <= raw and raw.bit_length() <= 1023 else None
    value = _number(raw)
    if value is None or value < 0 or not value.is_integer():
        return None
    return int(value)


def prepare_daily_card(ds: Mapping[str, Any], *, today: str | None = None,
                       asof: str | None = None) -> DailyCardData:
    """원본 데이터셋에서 관측 범위·표본 합계·기존 정책의 가격 집계를 계산한다."""
    from blog.build_explorer import passes_rank_gate

    observed = _date_label(today if today is not None else ds.get("generated"))
    data_asof = _date_label(asof if asof is not None else ds.get("data_asof"))
    raw_rows = ds.get("complexes")
    complete = (isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes))
                and all(isinstance(row, Mapping) for row in raw_rows))
    rows = list(raw_rows) if complete else []
    issues: list[str] = []
    if not complete:
        issues.append("complexes_unavailable")
    if observed == "미확인" or data_asof == "미확인":
        issues.append("date_unavailable")

    counts = [_count(row.get("molit_n")) for row in rows]
    sample_count = sum(counts) if complete and all(n is not None for n in counts) else None
    if sample_count is None:
        issues.append("sample_count_unavailable")
    trend_valid = complete and all(row.get("molit_trend_dir") in (None, "▲", "▼", "—")
                                   for row in rows)
    if not trend_valid:
        issues.append("trend_count_unavailable")

    medians: list[float] = []
    bands = [0, 0, 0, 0]
    for row, n in zip(rows, counts):
        area = _number(row.get("area_m2"))
        price = _number(row.get("molit_recent_eok"))
        if n is None or area is None or price is None or price <= 0:
            continue
        clean = {"product_type": row.get("product_type"), "area_m2": area, "molit_n": n}
        if not passes_rank_gate(clean):
            continue
        medians.append(price)
        idx = 0 if price < 10 else 1 if price < 15 else 2 if price < 20 else 3
        bands[idx] += 1

    return DailyCardData(
        observed_date=observed, data_asof=data_asof,
        complex_count=len(rows) if complete else None, sample_count=sample_count,
        up_count=sum(row.get("molit_trend_dir") == "▲" for row in rows) if trend_valid else None,
        down_count=sum(row.get("molit_trend_dir") == "▼" for row in rows) if trend_valid else None,
        median_eok=round(statistics.median(medians), 2) if medians else None,
        median_complex_count=len(medians), band_counts=tuple(bands), issues=tuple(issues),
    )


def _resolve_font(font_path: str | Path | None = None) -> Path:
    candidates = [Path(font_path)] if font_path is not None else [Path(p) for p in _FONT_CANDIDATES]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("한국어 카드 폰트가 없습니다. font_path에 한국어 TrueType/OpenType 폰트를 지정하세요.")


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("대표 카드 생성에는 Pillow가 필요합니다. pillow 패키지를 설치하세요.") from exc
    return Image, ImageDraw, ImageFont


class CardCanvas:
    """고정 캔버스 내 텍스트 박스를 측정하며 그리는 조판 도구."""

    def __init__(self, font_path: Path, background: str = "#071c2d"):
        Image, ImageDraw, self.ImageFont = _pillow()
        self.image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), background)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self._fonts: dict[tuple[int, bool], Any] = {}
        self.text_boxes: list[tuple[int, int, int, int]] = []

    def font(self, size: int, bold: bool = False):
        key = size, bold
        if key not in self._fonts:
            index = 6 if bold and self.font_path.name == "AppleSDGothicNeo.ttc" else 0
            self._fonts[key] = self.ImageFont.truetype(str(self.font_path), size=size, index=index)
        return self._fonts[key]

    def text(self, value: str, box: tuple[int, int, int, int], *, size: int = 28,
             fill: str = "#ffffff", bold: bool = False, min_size: int | None = None) -> None:
        """박스 밖으로 넘치지 않게 폰트를 줄인 뒤 끝 문자를 생략한다."""
        left, top, right, bottom = box
        if not (0 <= left < right <= CARD_WIDTH and 0 <= top < bottom <= CARD_HEIGHT):
            raise ValueError("대표 카드 텍스트 박스가 캔버스 경계를 벗어났습니다.")
        value = " ".join(str(value).split())
        smallest = min_size or min(18, size)
        chosen = self.font(size, bold)
        while size > smallest:
            chosen = self.font(size, bold)
            measured = self.draw.textbbox((0, 0), value, font=chosen)
            if measured[2] - measured[0] <= right - left and measured[3] - measured[1] <= bottom - top:
                break
            size -= 1
        chosen = self.font(size, bold)
        while value and self.draw.textlength(value, font=chosen) > right - left:
            value = value[:-2] + "…" if len(value) > 1 else ""
        measured = self.draw.textbbox((0, 0), value, font=chosen)
        height = measured[3] - measured[1]
        if height > bottom - top:
            raise ValueError("대표 카드 텍스트 높이가 지정 박스를 초과했습니다.")
        self.draw.text((left - measured[0], top - measured[1]), value, font=chosen, fill=fill)
        rendered = (left, top, left + measured[2] - measured[0], top + height)
        self.text_boxes.append(rendered)

    def png_bytes(self) -> bytes:
        output = BytesIO()
        self.image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if len(payload) > CARD_MAX_BYTES:
            raise ValueError(f"대표 카드 PNG {len(payload)}B가 {CARD_MAX_BYTES}B 예산을 초과했습니다.")
        return payload


def format_card_count(value: int | None, unit: str = "") -> str:
    return "미확인" if value is None else f"{value:,}{unit}"


def draw_daily_card(data: DailyCardData, font_path: Path) -> CardCanvas:
    """큰 숫자형 관측 범위 카드를 렌더한다."""
    card = CardCanvas(font_path)
    card.draw.rectangle((0, 0, 1200, 9), fill="#2dd4bf")
    card.text(CARD_BRAND, (56, 39, 1030, 78), size=28, bold=True)
    card.draw.line((56, 99, 1144, 99), fill="#294356", width=2)
    card.text("서울 주거", (56, 136, 680, 204), size=52, bold=True)
    card.text("실거래 관측", (56, 205, 680, 270), size=52, bold=True)
    card.text(format_card_count(data.complex_count), (56, 295, 694, 428),
              size=130, min_size=46, fill="#5eead4", bold=True)
    card.text("관측 단지 · 대표면적 표본", (61, 443, 680, 477), size=26, fill="#c7d6e2")
    card.text("아파트·다세대·연립 등 수집 대상", (61, 484, 680, 508), size=21, fill="#9ab2c3")

    card.draw.rounded_rectangle((747, 137, 1144, 481), radius=20, fill="#102c41")
    card.text("대표면적 실거래 표본", (777, 164, 1114, 197), size=24, fill="#c7d6e2")
    card.text(format_card_count(data.sample_count, "건"), (775, 214, 1114, 276), size=48, bold=True)
    card.draw.line((776, 296, 1114, 296), fill="#294b60", width=2)
    card.text("상승 · 하락 관측 단지", (777, 318, 1114, 349), size=24, fill="#c7d6e2")
    card.text(f"{format_card_count(data.up_count)} / {format_card_count(data.down_count)}",
              (777, 368, 1114, 423), size=43, bold=True)
    card.text("각 단지의 비교기간 거래 중위값 기준", (777, 443, 1114, 467), size=18, fill="#a6bfcd")

    card.draw.line((56, 511, 1144, 511), fill="#294356", width=2)
    card.text(f"관측일 {data.observed_date}  ·  데이터 기준 {data.data_asof}",
              (56, 530, 1144, 564), size=25, fill="#c7d6e2")
    card.text(CARD_SOURCE, (56, 581, 589, 608), size=21, fill="#9ab2c3")
    card.text("입력 일부 미확인 · 원문 확인 필요" if data.issues else CARD_NOTICE,
              (594, 581, 1144, 608), size=21, fill="#9ab2c3")
    return card


def _write_card_bytes(output_dir: Path, file_name: str, payload: bytes) -> Path:
    """완성 PNG만 노출하고, 실패하면 임시 파일을 남기지 않는다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / file_name
    if destination.is_file() and destination.read_bytes() == payload:
        return destination
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".daily-card-", suffix=".tmp", delete=False) as fp:
            temporary = fp.name
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def build_daily_card(ds: Mapping[str, Any], output_dir: str | Path, *,
                     today: str | None = None, asof: str | None = None,
                     font_path: str | Path | None = None) -> ThumbnailCard:
    """일간 데이터 → 폰트 검증 → 정확 조판 → 해시를 포함한 원자적 PNG 출력."""
    data = prepare_daily_card(ds, today=today, asof=asof)
    font = _resolve_font(font_path)
    payload = draw_daily_card(data, font).png_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    file_date = data.observed_date if data.observed_date != "미확인" else "undated"
    name = f"daily-{file_date}-{digest[:16]}.png"
    destination = _write_card_bytes(Path(output_dir).resolve(), name, payload)
    return ThumbnailCard(str(destination), digest, CARD_WIDTH, CARD_HEIGHT, data.alt,
                         len(payload), data.observed_date, data.data_asof, str(font))


def social_meta(base_url: str, relative_image: str, alt: str) -> str:
    """허용된 사이트 하위 PNG의 절대 HTTPS OG/Twitter 메타를 만든다."""
    parsed = urlsplit(base_url)
    relative = urlsplit(relative_image)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(char.isspace() for char in base_url)):
        raise ValueError("대표 카드 base_url은 사용자 정보가 없는 절대 HTTPS 사이트 URL이어야 합니다.")
    parts = relative.path.split("/")
    if (relative.scheme or relative.netloc or relative.query or relative.fragment
            or relative.path.startswith("/") or any(part in ("", ".", "..") for part in parts)
            or "%" in relative_image or not relative.path.endswith(".png")
            or not relative_image or any(char.isspace() or char == "\\" for char in relative_image)):
        raise ValueError("대표 카드 경로는 사이트 아래의 상대 PNG 파일이어야 합니다.")
    path = parsed.path.rstrip("/") + "/" + quote(relative.path, safe="/%-._~")
    image_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    escaped_url, escaped_alt = html.escape(image_url, quote=True), html.escape(alt, quote=True)
    return "\n".join((
        f'<meta property="og:image" content="{escaped_url}">',
        '<meta property="og:image:type" content="image/png">',
        f'<meta property="og:image:width" content="{CARD_WIDTH}">',
        f'<meta property="og:image:height" content="{CARD_HEIGHT}">',
        f'<meta property="og:image:alt" content="{escaped_alt}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:image" content="{escaped_url}">',
        f'<meta name="twitter:image:alt" content="{escaped_alt}">',
    ))
