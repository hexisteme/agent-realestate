"""정적 산출물의 검색·AI 추출 계약을 검사한다. 순위나 인용을 예측하지 않는다."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from blog.search_intent import SearchIntent, canonical_url, read_intent_registry, validate_registry


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class ParsedPage(HTMLParser):
    """Collect visible text and technical fields without executing page scripts."""

    def __init__(self, body: str):
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.h1s: list[str] = []
        self.descriptions: list[str] = []
        self.canonicals: list[str] = []
        self.robots: list[str] = []
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.schema_raw: list[str] = []
        self.visible: list[str] = []
        self.lang = ""
        self.landmarks: Counter = Counter()
        self._stack: list[tuple[str, bool]] = []
        self._capture: tuple[str, list[str]] | None = None
        self.feed(body)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        hidden = any(state[1] for state in self._stack) or tag in {"head", "script", "style", "template", "title"}
        hidden = hidden or "hidden" in values or values.get("aria-hidden") == "true"
        style = (values.get("style") or "").replace(" ", "").lower()
        hidden = hidden or "display:none" in style or "visibility:hidden" in style
        if tag not in {"meta", "link", "img", "br", "hr", "input", "source", "wbr"}:
            self._stack.append((tag, hidden))
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "html":
            self.lang = values.get("lang") or ""
        if tag in {"main", "article"}:
            self.landmarks[tag] += 1
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "meta":
            name = (values.get("name") or "").lower()
            if name == "description":
                self.descriptions.append(values.get("content") or "")
            if name in {"robots", "googlebot", "bingbot"}:
                self.robots.append(values.get("content") or "")
        if tag == "link" and "canonical" in (values.get("rel") or "").split():
            self.canonicals.append(values.get("href") or "")
        if (tag == "h1" or tag == "title" and any(state[0] == "head" for state in self._stack)
                or tag == "script" and values.get("type") == "application/ld+json"):
            self._capture = (tag, [])

    def handle_endtag(self, tag: str) -> None:
        if self._capture and self._capture[0] == tag:
            value = "".join(self._capture[1])
            destination = self.schema_raw if tag == "script" else self.h1s if tag == "h1" else self.titles
            destination.append(value if tag == "script" else _text(value))
            self._capture = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, value: str) -> None:
        if self._capture:
            self._capture[1].append(value)
        if not any(state[1] for state in self._stack):
            self.visible.append(value)

    @property
    def visible_text(self) -> str:
        return _text(" ".join(self.visible))


@dataclass(frozen=True)
class SeoFinding:
    intent_id: str
    canonical_path: str
    code: str
    detail: str
    action: str


def _local_path(root: Path, url: str, base_url: str) -> Path | None:
    parsed, base = urlsplit(url), urlsplit(base_url)
    if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
        return None
    prefix = unquote(base.path).rstrip("/")
    url_path = unquote(parsed.path)
    if url_path != prefix and not url_path.startswith(prefix + "/"):
        return None
    relative = url_path[len(prefix):].lstrip("/") or "index.html"
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("local link escapes site root")
    return candidate / "index.html" if candidate.is_dir() else candidate


def audit_site(site_dir: str | Path, intents: list[SearchIntent], base_url: str) -> dict:
    """Audit every registry owner and its internal links, returning actionable JSON."""
    validate_registry(intents)
    root = Path(site_dir).resolve()
    base = urlsplit(base_url)
    if base.scheme != "https" or not base.netloc or base.query or base.fragment:
        raise ValueError("base_url must be an absolute HTTPS public root")
    findings: list[SeoFinding] = []
    pages: dict[Path, ParsedPage] = {}
    checked_links = 0
    robots = RobotFileParser()
    robots_path = root / "robots.txt"
    if robots_path.is_file():
        robots.parse(robots_path.read_text(encoding="utf-8").splitlines())

    for intent in intents:
        def fail(code: str, detail: str, action: str) -> None:
            findings.append(SeoFinding(intent.intent_id, intent.canonical_path, code, detail, action))

        url = canonical_url(base_url, intent)
        path = _local_path(root, url, base_url)
        if path is None or not path.is_file():
            fail("MISSING_OWNER", "Registry canonical has no generated HTML.", "Regenerate owner or remove stale registry row.")
            continue
        if path not in pages:
            pages[path] = ParsedPage(path.read_text(encoding="utf-8"))
        page = pages[path]
        if page.canonicals != [url]:
            fail("CANONICAL_MISMATCH", "Expected one exact absolute registry canonical.", "Generate canonical from SearchIntent.")
        if page.titles != [intent.title]:
            fail("TITLE_MISMATCH", "Title differs from its registry owner.", "Use the registry title once in head.")
        if page.descriptions != [intent.description]:
            fail("DESCRIPTION_MISMATCH", "Description differs from its registry owner.", "Use the registry description once in head.")
        if len(page.h1s) != 1 or not page.h1s[0]:
            fail("H1_COUNT", f"Found {len(page.h1s)} H1 elements.", "Render one visible entity-specific H1.")
        if page.h1s and page.h1s[0] not in page.visible_text:
            fail("H1_HIDDEN", "H1 is not visible.", "Remove hiding from the canonical answer heading.")
        heading_entity = intent.entity_ids[-1] if intent.entity_type != "daily" else "서울"
        if page.h1s and heading_entity not in page.h1s[0]:
            fail("H1_ENTITY_MISMATCH", "Heading does not identify its canonical entity.", "Show the actual district/complex in the answer heading.")
        if page.landmarks["main"] != 1 or page.landmarks["article"] != 1:
            fail("LANDMARKS", "Expected one main and one article.", "Wrap the canonical answer in main/article landmarks.")
        if page.lang.lower() not in {"ko", "ko-kr"} or intent.locale != "ko-KR":
            fail("LOCALE_MISMATCH", "Page and query locale are not Korean.", "Use Korean visible content and html lang=ko.")
        if any(re.search(r"\b(noindex|none)\b", value, re.I) for value in page.robots):
            fail("NOINDEX", "Owner has an indexing exclusion.", "Remove exclusion only for evidence-gated owners.")
        if not robots_path.is_file():
            fail("ROBOTS_MISSING", "No robots.txt in the audited site root.", "Generate a public crawl policy.")
        else:
            for bot in ("Googlebot", "bingbot", "OAI-SearchBot", "PerplexityBot"):
                if not robots.can_fetch(bot, url):
                    fail("ROBOTS_BLOCK", f"robots.txt blocks {bot}.", "Review the crawler rule for this owner.")
        datasets = []
        for raw in page.schema_raw:
            try:
                value = json.loads(raw)
                nodes = value if isinstance(value, list) else value.get("@graph", [value])
                datasets.extend(node for node in nodes if isinstance(node, dict) and node.get("@type") == "Dataset")
                for node in nodes:
                    if not isinstance(node, dict) or node.get("@type") != "FAQPage":
                        continue
                    for question in node.get("mainEntity", []):
                        answer = question.get("acceptedAnswer", {})
                        texts = (question.get("name", ""), answer.get("text", ""))
                        if any(not text or ParsedPage(text).visible_text not in page.visible_text for text in texts):
                            fail("SCHEMA_FAQ_HIDDEN", "FAQ schema contains a question/answer absent from the page.", "Use only exact visible FAQ questions and answers.")
            except (ValueError, AttributeError):
                fail("SCHEMA_JSON", "JSON-LD is not parseable.", "Serialize schema through json.dumps.")
        if len(datasets) != 1:
            fail("DATASET_SCHEMA_COUNT", f"Found {len(datasets)} Dataset nodes.", "Render one Dataset backed by visible data.")
        else:
            dataset = datasets[0]
            creator = dataset.get("creator", {})
            creator_name = creator.get("name", "") if isinstance(creator, dict) else ""
            if not creator_name or creator_name not in page.visible_text:
                fail("SCHEMA_CREATOR_HIDDEN", "Dataset creator is missing from visible text.", "Show the actual personal-research publisher beside methodology.")
            if dataset.get("dateModified") != intent.observed_at or intent.observed_at not in page.visible_text:
                fail("SCHEMA_DATE_MISMATCH", "Schema update date and visible build date disagree.", "Render the exact dataset update date beside its source date.")
            for entity in intent.entity_ids if intent.entity_type != "daily" else ():
                if entity not in str(dataset.get("name", "")) or entity not in page.visible_text:
                    fail("SCHEMA_ENTITY_MISMATCH", "Structured and visible owner identities disagree.", "Derive Dataset name from the canonical owner.")
                    break
            if not dataset.get("description") or not dataset.get("name"):
                fail("SCHEMA_REQUIRED_TEXT", "Dataset name or description is missing.", "Describe only the data shown on the page.")
            claims = re.findall(r"(?<!\d)\d+(?:\.\d+)?(?!\d)", str(dataset.get("description", "")))
            if any(not re.search(r"(?<!\d)" + re.escape(number) + r"(?!\d)", page.visible_text) for number in claims):
                fail("SCHEMA_VALUE_HIDDEN", "Dataset description contains a number absent from visible data.", "Match structured numeric claims to the shown source-backed values.")
        seen_links = set()
        for href in page.links:
            target_url = urljoin(url, href)
            target = _local_path(root, target_url, base_url)
            if target is None or target_url in seen_links:
                continue
            seen_links.add(target_url)
            checked_links += 1
            if not target.is_file():
                fail("BROKEN_INTERNAL_LINK", target_url, "Link only to generated files or repair the target.")
                continue
            fragment = unquote(urlsplit(target_url).fragment)
            if fragment and target.suffix == ".html":
                if target not in pages:
                    pages[target] = ParsedPage(target.read_text(encoding="utf-8"))
                target_page = pages[target]
                if fragment not in target_page.ids:
                    fail("BROKEN_FRAGMENT", target_url, "Use an existing target id or remove the fragment.")
    counts = Counter(finding.code for finding in findings)
    return {
        "schema": "SeoGeoAudit/v1", "status": "PASS" if not findings else "FAIL",
        "registry_owners": len(intents), "checked_internal_links": checked_links,
        "finding_count": len(findings), "counts_by_code": dict(sorted(counts.items())),
        "findings": [asdict(finding) for finding in findings],
        "scope": "local_generated_html; no remote HTTP or search rank inference",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = audit_site(args.site, read_intent_registry(args.registry), args.base_url)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "findings"}, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
