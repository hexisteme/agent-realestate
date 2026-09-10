"""티스토리 공개 목록에서 발행 흔적만 판독. 부재와 확인 불가를 구분한다."""
from __future__ import annotations

import datetime
import html
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit


def _normalize_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", html.unescape(value)).split())


def _publication_date(title: str) -> datetime.date | None:
    matches = re.findall(r"(?<![0-9])([0-9]{4}-[0-9]{2}-[0-9]{2})(?![0-9])", title)
    if not matches:
        return None
    try:
        # 원고 제목의 첫 ISO 날짜는 발행 기준일, 뒤의 날짜는 비교 기간일 수 있다.
        return datetime.date.fromisoformat(matches[0])
    except ValueError:
        return None


def _public_post_url(href: str, blog_home: str) -> str | None:
    """같은 origin의 공개 글 번호 경로만. 쿼리·fragment·사용자정보는 거부."""
    if not isinstance(href, str) or not href or re.search(r"[\s\\]", href):
        return None
    try:
        home = urlsplit(blog_home)
        resolved = urlsplit(urljoin(blog_home.rstrip("/") + "/", href))
        if home.scheme not in ("http", "https") or not home.hostname:
            return None
        if home.username is not None or home.password is not None or home.query or home.fragment:
            return None
        if resolved.username is not None or resolved.password is not None or resolved.query or resolved.fragment:
            return None
        def origin(parts):
            return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        if origin(home) != origin(resolved) or not re.fullmatch(r"/[1-9][0-9]*", resolved.path):
            return None
        return urlunsplit((home.scheme, home.netloc, resolved.path, "", ""))
    except (ValueError, TypeError):
        return None


class _PublicationListing(HTMLParser):
    def __init__(self, blog_home: str):
        super().__init__(convert_charrefs=True)
        self.blog_home = blog_home
        self.entries: list[tuple[str, str]] = []
        self.article: list[tuple[str, str]] | None = None
        self.anchor: tuple[str, str] | None = None
        self.heading: list[str] | None = None
        self.heading_tag: str | None = None
        self.has_archives_heading = False
        self.ignored: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template", "noscript"):
            self.ignored.append(tag)
            return
        if self.ignored:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.heading, self.heading_tag = [], tag
        if tag == "article":
            self.article, self.anchor = [], None
        elif tag == "a" and self.article is not None:
            self.anchor = None
            attributes = dict(attrs)
            if len(attributes) != len(attrs):
                return
            if "link-article" not in (attributes.get("class") or "").split():
                return
            title = attributes.get("data-tiara-name")
            url = _public_post_url(attributes.get("href"), self.blog_home)
            if title and url:
                plink = attributes.get("data-tiara-plink")
                if plink is not None and _public_post_url(plink, self.blog_home) != url:
                    return
                self.anchor = _normalize_title(title), url

    def handle_data(self, data):
        if not self.ignored and self.heading is not None:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == self.heading_tag:
            heading = _normalize_title("".join(self.heading))
            if re.fullmatch(r"전체 글(?:\s+[0-9,]+)?", heading):
                self.has_archives_heading = True
            self.heading, self.heading_tag = None, None
        if tag == "a":
            if self.article is not None and self.anchor is not None:
                self.article.append(self.anchor)
            self.anchor = None
        elif tag == "article":
            if self.article is not None:
                self.entries.extend(self.article)
            self.article, self.anchor = None, None


def parse_publication_listing(raw: str, title: str, blog_home: str) -> tuple[bool | None, str]:
    """True=정확한 공개 글 링크, False=정상 목록 범위 내 MISS, None=확인 불가.

    False는 공개 첫 목록에서 찾지 못했다는 뜻이며 이전 발행 시도의 실패 증거가 아니다.
    """
    if not all(isinstance(value, str) and value.strip() for value in (raw, title, blog_home)):
        return None, ""
    parser = _PublicationListing(blog_home)
    try:
        parser.feed(raw)
        parser.close()
        normalized = _normalize_title(title)
        hits = {url for entry_title, url in parser.entries if entry_title == normalized}
        if len(hits) == 1:
            return True, hits.pop()
        if hits:  # 같은 제목의 여러 글이면 어느 글이 이번 발행인지 확정하지 않는다.
            return None, ""
        # 앞의 정상 항목만 완성되고 대상 글이 포함된 후미가 잘렸을 수 있다.
        if (parser.article is not None or parser.anchor is not None
                or parser.heading is not None or parser.ignored):
            return None, ""
        target_date = _publication_date(normalized)
        today = datetime.date.today()
        dated_posts = {url: day for entry_title, url in parser.entries
                       if (day := _publication_date(entry_title)) is not None and day <= today}
        if (parser.has_archives_heading and len(dated_posts) >= 2 and target_date is not None
                and min(dated_posts.values()) <= target_date <= today):
            return False, ""
    except (ValueError, TypeError, RecursionError):
        pass
    return None, ""
