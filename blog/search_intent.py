"""Search-intent ownership contract for public real-estate pages.

One query family has exactly one canonical owner.  The registry is generated
from the same dataset gates that generate district and complex pages; demand
never creates a page that the evidence gates would reject.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import html
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, unquote, urlsplit


_ENTITY_TYPES = {"daily", "district", "complex"}
_CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


@dataclass(frozen=True)
class SearchIntent:
    intent_id: str
    locale: str
    target_query: str
    query_family: str
    search_job: str
    entity_type: str
    entity_ids: tuple[str, ...]
    canonical_path: str
    answer_claim_ids: tuple[str, ...]
    observed_at: str
    campaign_id: str
    title: str
    description: str

    def __post_init__(self) -> None:
        text_fields = (
            self.intent_id, self.target_query, self.query_family, self.search_job,
            self.canonical_path, self.observed_at, self.title, self.description,
        )
        if any(not value.strip() or "\n" in value for value in text_fields):
            raise ValueError("search intent text fields must be non-empty single lines")
        if self.locale != "ko-KR":
            raise ValueError("only ko-KR intent ownership is active")
        if self.entity_type not in _ENTITY_TYPES:
            raise ValueError(f"unsupported entity type: {self.entity_type}")
        if not self.entity_ids or any(not value.strip() for value in self.entity_ids):
            raise ValueError("entity_ids must identify the canonical owner")
        if not self.canonical_path.startswith("/") or "?" in self.canonical_path or "#" in self.canonical_path:
            raise ValueError("canonical_path must be a query-free absolute path")
        decoded = unquote(self.canonical_path)
        if decoded.startswith("//") or any(part in {".", ".."} for part in decoded.split("/")) or "\\" in decoded:
            raise ValueError("canonical_path cannot escape its public site root")
        if date.fromisoformat(self.observed_at).isoformat() != self.observed_at:
            raise ValueError("observed_at must be YYYY-MM-DD")
        if not self.answer_claim_ids or len(set(self.answer_claim_ids)) != len(self.answer_claim_ids):
            raise ValueError("answer_claim_ids must be non-empty and unique")
        if not _CAMPAIGN_RE.fullmatch(self.campaign_id):
            raise ValueError("invalid campaign_id")

    def as_dict(self) -> dict:
        return asdict(self)


def daily_intent(today: str, title: str, description: str) -> SearchIntent:
    return SearchIntent(
        intent_id=f"daily:{today}", locale="ko-KR",
        target_query="서울 아파트 실거래가 오늘 변화",
        query_family="seoul-apartment-daily-change",
        search_job="오늘 공개된 서울 아파트 실거래 변화와 데이터 기준일을 확인한다.",
        entity_type="daily", entity_ids=(today,), canonical_path=f"/daily/{today}.html",
        answer_claim_ids=(f"daily:{today}:rtms-summary",), observed_at=today,
        campaign_id="realestate-daily", title=title, description=description,
    )


def district_intent(gu: str, count: int, today: str) -> SearchIntent:
    title = f"{gu} 아파트 공공 실거래 구허브 — {today}"
    description = f"{gu} 감시 단지 {count}개 국토부 공공 실거래 중위·분포·추세 한눈에. 자체 점수·순위 없음, 투자자문 아님."
    return SearchIntent(
        intent_id=f"district:{gu}", locale="ko-KR", target_query=f"{gu} 아파트 실거래",
        query_family=f"district:{gu}:apartment-transactions",
        search_job=f"{gu} 아파트 단지의 실거래 수준과 분포를 비교한다.",
        entity_type="district", entity_ids=(gu,), canonical_path=f"/gu/{quote(gu)}.html",
        answer_claim_ids=(f"district:{gu}:recent_transaction_median_eok",), observed_at=today,
        campaign_id="realestate-district", title=title, description=description,
    )


def complex_intent(gu: str, name: str, slug: str, area_m2: float | None, today: str) -> SearchIntent:
    area_txt = f"{area_m2:g}㎡" if area_m2 is not None else ""
    title = f"{name} 실거래 — {gu} · {today}"
    description = f"{name}({gu}) 전용{area_txt} 국토부 공공 실거래 12개월 중위·분포·추세·월별 중위. 자체 점수·순위 없음, 투자자문 아님."
    return SearchIntent(
        intent_id=f"complex:{slug}", locale="ko-KR", target_query=f"{gu} {name} 실거래",
        query_family=f"complex:{gu}:{name}:transactions",
        search_job=f"{name}의 동일평형 실거래 수준·분포·최근 변화를 확인한다.",
        entity_type="complex", entity_ids=(gu, name), canonical_path=f"/complex/{quote(slug)}.html",
        answer_claim_ids=(f"complex:{slug}:recent_transaction_median_eok",), observed_at=today,
        campaign_id="realestate-complex", title=title, description=description,
    )


def validate_registry(intents: list[SearchIntent]) -> None:
    for attribute in ("intent_id", "query_family", "canonical_path"):
        values = [getattr(intent, attribute) for intent in intents]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"duplicate {attribute}: {duplicates}")


def build_intent_registry(ds: dict, today: str, *, daily_title: str | None = None,
                          daily_description: str | None = None) -> list[SearchIntent]:
    """Generate owners only for surfaces the current evidence gates can publish."""
    from blog import complex_page as cp

    rows = ds.get("complexes") or []
    by_gu: dict[str, list[dict]] = {}
    for row in rows:
        by_gu.setdefault(row["gu"], []).append(row)

    intents: list[SearchIntent] = []
    if daily_title and daily_description:
        intents.append(daily_intent(today, daily_title, daily_description))
    intents.extend(district_intent(gu, len(by_gu[gu]), today) for gu in sorted(by_gu))
    for row in cp.select_page_complexes(ds):
        slug = cp.complex_slug(row["gu"], row["name"])
        intents.append(complex_intent(row["gu"], row["name"], slug, row.get("area_m2"), today))
    validate_registry(intents)
    return intents


def canonical_url(base_url: str, intent: SearchIntent) -> str:
    base = urlsplit(base_url)
    if base.scheme != "https" or not base.netloc or base.query or base.fragment or base.username or base.password:
        raise ValueError("canonical base_url must be an absolute HTTPS site root")
    return base_url.rstrip("/") + intent.canonical_path


def canonical_tag(base_url: str, intent: SearchIntent) -> str:
    return f'<link rel="canonical" href="{html.escape(canonical_url(base_url, intent), quote=True)}">'


def write_intent_registry(path: str | os.PathLike[str], intents: list[SearchIntent]) -> str:
    validate_registry(intents)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(intent.as_dict(), ensure_ascii=False, sort_keys=True) + "\n" for intent in intents)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return str(destination)


def read_intent_registry(path: str | os.PathLike[str]) -> list[SearchIntent]:
    intents = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        raw["entity_ids"] = tuple(raw["entity_ids"])
        raw["answer_claim_ids"] = tuple(raw["answer_claim_ids"])
        intents.append(SearchIntent(**raw))
    validate_registry(intents)
    return intents
