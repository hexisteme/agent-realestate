"""첫 실제 결산 원고 검토: prepare는 승인 아님. Codex는 사람의 명시적 승인 없이 approve 금지."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile

FIRST_REVIEW_DATES = ("2026-09-13", "2026-09-27")


def describe_review(path: str, data: dict) -> dict:
    """퍼블리셔가 실제 파싱한 제목·본문·태그와 원고 파일을 함께 결박."""
    payload = {"date": Path(path).name[:10], "kind": "periodic",
               **{key: data[key] for key in ("title", "body", "tags")},
               "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return {**payload, "digest": hashlib.sha256(encoded).hexdigest()}


def review_path(outroot: str, date: str, suffix: str) -> Path:
    return Path(outroot) / "report/blog/reviews" / f"{date}-periodic-{suffix}"


def write_review(path: Path, text: str) -> None:
    """같은 디렉터리에 기록한 뒤 원자적으로 교체. 중간 승인 파일은 읽히지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".review-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def needs_review(outroot: str, path: str, data: dict, kind: str) -> bool:
    if kind != "periodic" or Path(path).name[:10] not in FIRST_REVIEW_DATES:
        return False
    current = describe_review(path, data)
    try:
        receipt = json.loads(review_path(outroot, current["date"], "approval.json").read_text())
        return not (isinstance(receipt, dict) and receipt.get("status") == "APPROVED"
                    and all(receipt.get(k) == current[k] for k in ("date", "kind", "digest"))
                    and receipt.get("reviewer", "").strip() and receipt.get("note", "").strip())
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("prepare", "approve"))
    ap.add_argument("--date", required=True, choices=FIRST_REVIEW_DATES)
    ap.add_argument("--outroot", default=".")
    ap.add_argument("--digest", help="사람이 검토한 prepare 결과의 digest")
    ap.add_argument("--reviewer", help="명시적으로 승인한 사람")
    ap.add_argument("--note", help="사람이 실제 승인한 판단. Codex 임의 작성 금지")
    a = ap.parse_args(argv)
    from blog.tistory_publish_pw import _parse_helper, _resolve_draft
    path = _resolve_draft(a.outroot, a.date, "periodic")
    if not path:
        ap.error("해당 날짜의 실제 원고가 없습니다")
    current = describe_review(path, _parse_helper(path))
    if not current["title"] or not current["body"]:
        ap.error("제목 또는 본문이 비어 있습니다")
    pending = review_path(a.outroot, a.date, "review.json")
    if a.action == "prepare":
        write_review(pending, json.dumps({"status": "AWAIT_REVIEW", **current}, ensure_ascii=False, indent=2))
        preview = review_path(a.outroot, a.date, "review.html")
        write_review(preview, '<!doctype html><meta charset="utf-8"><h1>검토 대기 · 승인 아님</h1>'
                     f'<h2>{html.escape(current["title"])}</h2><p>{html.escape(current["tags"])}</p>'
                     f'<p>digest: {current["digest"]}</p>{current["body"]}')
        print(f"AWAIT_REVIEW {preview.resolve()} digest={current['digest']}")
        return 3
    # 이 호출은 명시적인 사람 승인 후에만. 로컬 파일은 사람 신원을 암호학적으로 증명하지 않는다.
    try:
        prepared = json.loads(pending.read_text())
    except (OSError, ValueError):
        ap.error("prepare 원고를 먼저 검토해야 합니다")
    if not (a.digest == current["digest"] == prepared.get("digest")
            and a.reviewer and a.reviewer.strip() and a.note and a.note.strip()):
        ap.error("검토한 digest와 현재 원고가 일치하고 사람 승인 기록이 있어야 합니다")
    receipt = {k: current[k] for k in ("date", "kind", "digest")}
    receipt.update(status="APPROVED", reviewer=a.reviewer.strip(), note=a.note.strip(),
                   approved_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    target = review_path(a.outroot, a.date, "approval.json")
    write_review(target, json.dumps(receipt, ensure_ascii=False, indent=2))
    print(f"APPROVED {target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
