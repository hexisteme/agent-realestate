"""Tistory helper document contract.

The publication boundary is one HTML helper with exactly three textareas:
``t`` (title), ``g`` (tags), and ``b`` (body HTML).  Writers and both
publishers use this module so a raw-body file can never be mistaken for a
publishable draft.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile


class DraftContractError(ValueError):
    """Raised when a draft is not a complete, unambiguous helper document."""


@dataclass(frozen=True)
class DraftPayload:
    title: str
    tags: str
    body: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "tags": self.tags, "body": self.body}


_FIELD_NAMES = {"t": "title", "g": "tags", "b": "body"}


class _TextareaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, list[str]] = {}
        self.active: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "textarea":
            return
        field_id = dict(attrs).get("id")
        if field_id not in _FIELD_NAMES:
            self.active = None
            return
        if field_id in self.values:
            raise DraftContractError(f"duplicate textarea#{field_id}")
        self.values[field_id] = []
        self.active = field_id

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "textarea":
            self.active = None

    def handle_data(self, data: str) -> None:
        if self.active is not None:
            self.values[self.active].append(data)


def parse_helper_text(source: str) -> DraftPayload:
    parser = _TextareaParser()
    try:
        parser.feed(source)
        parser.close()
    except DraftContractError:
        raise
    except Exception as exc:  # HTML parser failures must fail closed at publication boundary.
        raise DraftContractError("invalid helper HTML") from exc

    missing = [field_id for field_id in _FIELD_NAMES if field_id not in parser.values]
    if missing:
        raise DraftContractError("missing helper fields: " + ",".join(missing))
    values = {field_id: "".join(parser.values[field_id]).strip() for field_id in _FIELD_NAMES}
    empty = [field_id for field_id, value in values.items() if not value]
    if empty:
        raise DraftContractError("empty helper fields: " + ",".join(empty))
    return DraftPayload(title=values["t"], tags=values["g"], body=values["b"])


def parse_helper(path: str | os.PathLike[str]) -> dict[str, str]:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DraftContractError("helper cannot be read") from exc
    return parse_helper_text(source).as_dict()


def render_helper(payload: DraftPayload, label_date: str) -> str:
    return f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>티스토리 발행 원고 {escape(label_date)}</title>
<style>body{{font:15px/1.6 -apple-system,Pretendard,sans-serif;max-width:900px;margin:0 auto;padding:24px}}
textarea{{width:100%;font:12px/1.5 ui-monospace,monospace;border:1px solid #ddd;border-radius:6px;padding:8px}}
button{{margin:4px 0 14px;padding:6px 14px;border:1px solid #0969da;background:#0969da;color:#fff;border-radius:6px;cursor:pointer}}
button.ok{{background:#1a7f37;border-color:#1a7f37}}
.box{{background:#f6f8fa;border:1px solid #ddd;border-radius:8px;padding:12px 16px;font-size:13px}}
details{{border:1px solid #ddd;border-radius:8px;padding:10px 16px;margin-top:14px}}</style></head><body>
<h1>티스토리 발행 원고 <small>{escape(label_date)}</small></h1>
<div class=box><b>등록 절차 (3복사 + 발행 1클릭)</b><ol style="margin:6px 0">
<li>티스토리 → 글쓰기 → 에디터 우상단 <b>기본모드 ▾ → HTML</b> 전환</li>
<li>아래 <b>본문 HTML 복사</b> → 에디터에 붙여넣기 (기본모드로 되돌리면 표 미리보기 확인 가능)</li>
<li><b>제목·태그 복사</b> → 각 입력란에 붙여넣기</li>
<li><b>발행</b>(공개) 클릭 — 끝</li></ol></div>
<h3>제목</h3><textarea id=t rows=1 readonly>{escape(payload.title)}</textarea>
<button onclick="cp('t',this)">제목 복사</button>
<h3>태그</h3><textarea id=g rows=1 readonly>{escape(payload.tags)}</textarea>
<button onclick="cp('g',this)">태그 복사</button>
<h3>본문 HTML</h3><textarea id=b rows=16 readonly>{escape(payload.body)}</textarea>
<button onclick="cp('b',this)">본문 HTML 복사</button>
<details><summary>본문 미리보기</summary>{payload.body}</details>
<script>function cp(id,btn){{navigator.clipboard.writeText(document.getElementById(id).value)
.then(()=>{{btn.textContent='복사됨 ✓';btn.className='ok';}});}}</script>
</body></html>"""


def write_helper(path: str | os.PathLike[str], payload: DraftPayload, label_date: str) -> str:
    """Atomically write a helper after an exact in-memory contract round trip."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = render_helper(payload, label_date)
    parsed = parse_helper_text(document)
    if parsed != payload:
        raise DraftContractError("helper round-trip mismatch")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise

    if parse_helper(destination) != payload.as_dict():
        raise DraftContractError("persisted helper round-trip mismatch")
    return str(destination)
