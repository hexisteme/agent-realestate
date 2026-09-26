"""Reconcile archived anchors against current pages, without changing old facts.

Interpret owned HTML with scripting enabled, as in the site browser: noscript
contents are text, and plaintext consumes everything through EOF. This is not a
JavaScript-disabled DOM audit; inactive markup must not create links or IDs.
"""
from __future__ import annotations

import html
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


_ATTRIBUTE = re.compile(r'''\s*([^\s/>=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')
_TEXT_ELEMENTS = ("script", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript")


class _ArchiveAnchors(HTMLParser):
    CDATA_CONTENT_ELEMENTS = _TEXT_ELEMENTS

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.template_depth = 0
        self.plaintext = False
        self.line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if self.plaintext:
            return
        if tag == "plaintext":
            self.plaintext = True
            return
        if tag == "template":
            self.template_depth += 1
        if self.template_depth or tag != "a":
            return
        hrefs = [value for name, value in attrs if name == "href"]
        if not hrefs or hrefs[0] is None:
            return
        raw = self.get_starttag_text()
        line, column = self.getpos()
        offset = self.line_starts[line - 1] + column
        if len(hrefs) != 1:
            self.anchors.append((hrefs[0], None, "AMBIGUOUS_HREF"))
            return
        position = re.match(r"<a\b", raw, re.I).end()
        while match := _ATTRIBUTE.match(raw, position):
            position = match.end()
            if match[1].lower() != "href":
                continue
            group = next((i for i in (2, 3, 4) if match[i] is not None), None)
            if group is not None and html.unescape(match[group]) == hrefs[0]:
                self.anchors.append((hrefs[0], (offset + match.start(group), offset + match.end(group), group != 4), None))
                return
            break
        self.anchors.append((hrefs[0], None, "UNVERIFIED_ATTRIBUTE_SPAN"))

    def handle_endtag(self, tag):
        if self.plaintext:
            return
        if tag == "template" and self.template_depth:
            self.template_depth -= 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)


class _ElementIds(HTMLParser):
    CDATA_CONTENT_ELEMENTS = _TEXT_ELEMENTS

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.template_depth = 0
        self.plaintext = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if self.plaintext:
            return
        if not self.template_depth:
            self.ids.update(value for name, value in attrs if name == "id" and value is not None)
        if tag == "plaintext":
            self.plaintext = True
            return
        if tag == "template":
            self.template_depth += 1

    def handle_endtag(self, tag):
        if self.plaintext:
            return
        if tag == "template" and self.template_depth:
            self.template_depth -= 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)


def _local_archive_target(root: Path, source: Path, href: str, base):
    parts = urlsplit(href)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.netloc and (parts.netloc.lower() != base.netloc.lower() or parts.scheme and parts.scheme != base.scheme):
        return None
    path = unquote(parts.path, errors="strict")
    if parts.netloc or path.startswith("/"):
        prefix = unquote(base.path, errors="strict").rstrip("/")
        if prefix and not path.startswith(prefix + "/"):
            return None
        target = root / path[len(prefix):].lstrip("/")
    else:
        target = source.parent / path if path else source
    target = target.resolve()
    if not target.is_relative_to(root):
        raise ValueError("LOCAL_PATH_OUTSIDE_SITE")
    return parts, target, unquote(parts.fragment, errors="strict")


def _archive_href(parts, original: str, path: str, fragment: str = "") -> str:
    result = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    # Preserve the query verbatim, including a deliberately empty '?' delimiter.
    if "?" in original.partition("#")[0]:
        result += "?" + parts.query
    if fragment:
        result += "#" + quote(fragment, safe="")
    return result


def repair_archived_navigation(site_dir: str | Path, base_url: str) -> dict:
    """Repair only real posts/daily anchors; return changes and unresolved links.

    This receipt is not a whole-site link verdict. Missing destinations outside
    the two repair policies remain untouched and explicitly unresolved. No gu
    IDs, complex owners, facts, page gates, or frozen cohort payloads are created.
    Call after current gu/complex generation and before D0 cohort restoration.
    """
    root = Path(site_dir).resolve()
    base = urlsplit(base_url)
    if base.scheme not in ("http", "https") or not base.netloc:
        raise ValueError("ARCHIVE_BASE_URL_INVALID")
    changes, unresolved, pending = [], [], []
    ids_by_file = {}

    def ids(path):
        if path not in ids_by_file:
            ids_by_file[path] = _ElementIds(path.read_bytes().decode("utf-8")).ids
        return ids_by_file[path]

    for directory in (root / "posts", root / "daily"):
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            unresolved.append({"source": directory.name, "code": "ARCHIVE_DIRECTORY_OUTSIDE_SCOPE"})
            continue
        if not directory.is_dir():
            continue
        for source in sorted(directory.rglob("*.html")):
            if source.is_symlink() or not source.resolve().is_relative_to(directory):
                unresolved.append({"source": source.relative_to(root).as_posix(), "code": "ARCHIVE_FILE_OUTSIDE_SCOPE"})
                continue
            original = source.read_bytes().decode("utf-8")
            replacements = []
            for href, span, span_error in _ArchiveAnchors(original).anchors:
                evidence = {"source": source.relative_to(root).as_posix(), "href": href}
                try:
                    local = _local_archive_target(root, source, href, base)
                except (ValueError, UnicodeError):
                    unresolved.append({**evidence, "code": "INVALID_OR_UNCONFINED_LOCAL_URL"})
                    continue
                if local is None:
                    continue
                if span_error:
                    unresolved.append({**evidence, "code": span_error})
                    continue
                parts, target, fragment = local
                new_href = None
                if target.parent == root / "complex" and target.suffix == ".html" and not target.is_file():
                    gu, separator, name = target.stem.partition("-")
                    gu_page = (root / "gu" / f"{gu}.html").resolve()
                    if not separator or not name or not gu_page.is_relative_to(root / "gu") or not gu_page.is_file():
                        unresolved.append({**evidence, "code": "MISSING_CURRENT_GU"})
                        continue
                    if parts.netloc or parts.path.startswith("/"):
                        path = base.path.rstrip("/") + "/gu/" + quote(gu, safe="") + ".html"
                    else:
                        path = posixpath.relpath("gu/" + quote(gu, safe="") + ".html", source.parent.relative_to(root).as_posix())
                    new_href = _archive_href(parts, href, path, name if name in ids(gu_page) else "")
                elif target.parent == root / "gu" and target.is_file() and parts.fragment:
                    if fragment not in ids(target):
                        new_href = _archive_href(parts, href, parts.path)
                elif target.suffix == ".html":
                    if not target.is_file():
                        unresolved.append({**evidence, "code": "MISSING_FILE_OUTSIDE_REPAIR_POLICY"})
                    elif parts.fragment and fragment not in ids(target):
                        unresolved.append({**evidence, "code": "MISSING_FRAGMENT_OUTSIDE_REPAIR_POLICY"})
                if new_href is not None and new_href != href:
                    start, end, quoted = span
                    escaped = html.escape(new_href, quote=True)
                    replacements.append((start, end, escaped if quoted else f'"{escaped}"'))
                    changes.append({**evidence, "new_href": new_href})
            if replacements:
                updated = original
                for start, end, replacement in sorted(replacements, reverse=True):
                    updated = updated[:start] + replacement + updated[end:]
                pending.append((source, updated.encode("utf-8")))
    for source, payload in pending:
        source.write_bytes(payload)
    return {"schema": "ArchiveNavigation/v1", "scripting_policy": "enabled",
            "changed_files": len(pending), "changed_links": len(changes),
            "changes": changes, "unresolved_count": len(unresolved), "unresolved": unresolved}
