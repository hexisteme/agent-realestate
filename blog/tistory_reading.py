"""Add a compact, real section index to generated Tistory HTML.

The original HTML is patched in place, not serialized, so prices, images,
links and disclosures keep their original bytes. Call this before measuring
the publishing byte budget. The index is text only: the Tistory editor
rewrites in-page href="#..." to its own /manage/newpost/#... URL (live posts
/126~/133, checked 2026-10-20), so readers would land on the admin editor.
This module does not publish anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
from html.parser import HTMLParser
import re


READING_CSS = (
    '.re-toc,[data-reading-toc="1"]{margin:20px 0;padding:12px 16px;border:1px solid #cbd5e1;'
    'border-radius:8px;background:#f8fafc;color:#334155}'
    '.re-toc span,[data-reading-toc="1"] span{display:inline-block;padding:4px 2px;line-height:1.5;'
    'overflow-wrap:anywhere}'
)

_TOP_LABELS = ('오늘의 관측 요약', '최근 6개월 값', '읽는 법', '구별 가격·추세')
_MAX_ITEMS = 8
_NAV_LABELS = (
    ('구별 가격·추세', '구별 가격·추세'),
    ('서울 25개 구 요약', '구별 가격·추세'),
    ('가격대별 요약', '가격대별 요약'),
    ('서울 아파트 매수를 검토할 때', '매수 검토 관측'),
    ('청약·공공임대 안내', '청약·공공임대'),
    ('거시 지표', '금리·주택시장'),
    ('최근 6개월 값', '최근 6개월 값'),
    ('읽는 법', '지표 읽는 법'),
)
_TOP_PARAGRAPH = re.compile(
    r'<p><b>(' + '|'.join(re.escape(label) for label in _TOP_LABELS)
    + r')</b>(.*?)</p>', re.DOTALL,
)
_FRESHNESS_THEN_ANSWER = re.compile(
    r'(?P<fresh>\s*<p\b(?=[^>]*\bdata-daily-freshness\b)[^>]*>.*?</p>\s*)'
    r'(?P<answer><h3>오늘의 관측 요약</h3>\s*<p\b[^>]*>.*?</p>)', re.DOTALL,
)


@dataclass
class _Heading:
    start: int
    end: int = 0
    text: list[str] = field(default_factory=list)
    main_text: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        main = ' '.join(''.join(self.main_text).split())
        return main or ' '.join(''.join(self.text).split())


class _Sections(HTMLParser):
    """Collect offsets while leaving all non-heading markup untouched."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer('\n', source))
        self.headings: list[_Heading] = []
        self.current: _Heading | None = None
        self.span_depth = 0

    def absolute_offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == 'h3':
            self.current = _Heading(self.absolute_offset())
            self.span_depth = 0
        elif tag == 'span' and self.current:
            self.span_depth += 1
        elif tag == 'br' and self.current:
            self.handle_data(' ')

    def handle_endtag(self, tag):
        if tag == 'h3' and self.current:
            self.current.end = self.absolute_offset() + len('</h3>')
            self.headings.append(self.current)
            self.current = None
        elif tag == 'span' and self.current:
            self.span_depth = max(0, self.span_depth - 1)

    def handle_data(self, data):
        if self.current:
            self.current.text.append(data)
            if not self.span_depth:
                self.current.main_text.append(data)


def _promote_top_labels(source: str) -> str:
    def promote(match):
        label, note = match.groups()
        return f'<h3>{label}</h3>' + (f'<p>{note}</p>' if note.strip() else '')

    return _TOP_PARAGRAPH.sub(promote, source)


def _navigation_priority(heading: _Heading) -> int:
    return next((index for index, (prefix, _) in enumerate(_NAV_LABELS)
                 if heading.label.startswith(prefix)), len(_NAV_LABELS))


def _navigation_label(heading: _Heading) -> str:
    label = next((label for prefix, label in _NAV_LABELS if heading.label.startswith(prefix)), heading.label)
    return label if len(label) <= 28 else label[:27] + '…'


def enhance_tistory_reading(source: str, *, byte_budget: int | None = None) -> str:
    """Return an idempotent text-only quick index, with the observation answer first.

    Only the four exact labels emitted by visual_first are promoted. Existing
    h3/h4 levels, IDs and all section content remain intact. Duplicate labels
    are listed once. Optional navigation degrades before evidence: eight,
    four, two, then none.
    An already oversized original is left for the caller's existing compaction.
    """
    if 'data-reading-toc="1"' in source:
        return source
    original = source
    source = re.sub(r'<p\b[^>]*\bdata-outline\b[^>]*>.*?</p>', '', source, flags=re.DOTALL)
    source = _promote_top_labels(source)
    first = _FRESHNESS_THEN_ANSWER.match(source)
    if first:
        source = first['answer'] + first['fresh'] + source[first.end():]
    parser = _Sections(source)
    parser.feed(source)
    parser.close()
    headings = [heading for heading in parser.headings if heading.label]
    if len(headings) < 2:
        return source if byte_budget is None or len(source.encode('utf-8')) <= byte_budget else original

    # Visual-first starts with a factual observation paragraph. Keep its answer
    # above the index, then let readers jump to the next sections.
    summary = next((heading for heading in headings if heading.label == '오늘의 관측 요약'), None)
    insertion = headings[0].end
    if summary:
        paragraph = re.match(r'\s*<p\b[^>]*>.*?</p>', source[summary.end:], re.DOTALL)
        if paragraph:
            insertion = summary.end + paragraph.end()
    else:
        cards = list(re.finditer(r'<p\b[^>]*\bdata-reader-card\b[^>]*>.*?</p>', source, re.DOTALL))
        paragraph = re.match(r'\s*<p\b[^>]*>.*?</p>', source[insertion:], re.DOTALL)
        if cards:
            insertion = cards[-1].end()
        elif paragraph:
            insertion += paragraph.end()
    eligible = sorted((heading for heading in headings if heading.label != '오늘의 관측 요약'),
                      key=_navigation_priority)
    for limit in (_MAX_ITEMS, 4, 2):
        labels = dict.fromkeys(_navigation_label(heading)
                               for heading in sorted(eligible[:limit], key=lambda heading: heading.start))
        toc = ('<p class="re-toc" data-reading-toc="1"><b>이 글의 주요 항목</b><br>'
               + ' · '.join(f'<span>{html.escape(label)}</span>' for label in labels) + '</p>')
        rendered = source[:insertion] + toc + source[insertion:]
        if byte_budget is None or len(rendered.encode('utf-8')) <= byte_budget:
            return rendered
    return source if len(source.encode('utf-8')) <= byte_budget else original
