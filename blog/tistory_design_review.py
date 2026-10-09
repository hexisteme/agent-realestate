"""티스토리 발행 디자인 리뷰 게이트 — design-director 산출물(`design_review.json`) 판독·등급화.

계약 정본: `~/.agents/skills/design-director/references/contract.md` §2(Finding 필드)·§6(게이트용
산출, schema `design-review/v1`). 이 모듈은 리뷰를 읽고 등급만 낸다 — 리뷰를 만들거나 고치지 않고,
발행·마커·알림은 퍼블리셔(`tistory_publish_pw`)가 이 등급으로 결정한다.

리뷰 위치: 원고 옆 `<원고 경로에서 .html 을 뺀 것>.design_review.json`(미디어 사이드카
`.media.json` 과 같은 규약). 퍼블리셔 `--design-review <path>` 가 이 기본 위치를 덮어쓴다.
해시 기준: 발행할 원고 HTML 파일 바이트의 sha256.

등급 규칙(계약 §6 "게이트 해석은 소비자가 정한다" 의 agent_realestate 해석):
- 파일 없음 → NOT_RUN. 기존 발행 그대로(사용자 결정 — 부재는 비차단), 로그 한 줄만.
- 읽기·JSON 실패, schema ≠ design-review/v1, domain ≠ blog, 어휘 밖 값, findings 가 목록 아님,
  asset_sha256 이 있는데 현재 원고 sha256 과 다름(오래된 리뷰) → AWAIT_HUMAN.
- open P1 defect: check=rule → BLOCK, check=advisory → AWAIT_HUMAN.
  waiver_reason 이 비어 있는 waived 는 open 으로 센다(계약 §2: waived 는 사유 필수).
- 그 밖 → PASS + 개수 요약 한 줄.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Literal

DESIGN_REVIEW_SCHEMA = "design-review/v1"
DESIGN_REVIEW_DOMAIN = "blog"
DESIGN_REVIEW_SUFFIX = ".design_review.json"

_VOCABULARY = (
    ("priority", ("P1", "P2", "P3")),
    ("kind", ("defect", "suggestion")),
    ("check", ("rule", "advisory")),
    ("status", ("open", "fixed", "waived")),
)


class DesignReviewMalformed(ValueError):
    """리뷰 파일이 design-review/v1 형식을 만족하지 못함 — 깨진 증거는 통과로 취급하지 않는다."""


@dataclass(frozen=True)
class DesignReviewGrade:
    """게이트 등급. `detail` 은 로그·알림에 그대로 쓰는 한 줄 사유."""

    result: Literal["NOT_RUN", "PASS", "BLOCK", "AWAIT_HUMAN"]
    detail: str


def resolve_design_review_path(draft_path, explicit=None) -> Path:
    """읽을 리뷰 경로. 명시 경로가 있으면 그것, 없으면 원고 옆 기본 위치."""
    if explicit:
        return Path(explicit)
    draft = Path(draft_path)
    stem = draft.name[:-len(".html")] if draft.name.endswith(".html") else draft.name
    return draft.with_name(stem + DESIGN_REVIEW_SUFFIX)


def compute_draft_sha256(draft_path) -> str:
    """발행할 원고 HTML 파일 바이트의 sha256 — 리뷰 결속(asset_sha256) 대조 기준."""
    return hashlib.sha256(Path(draft_path).read_bytes()).hexdigest()


def _clip(value, limit: int = 60) -> str:
    """파일에서 온 값을 로그·알림 한 줄에 안전하게 싣는다(개행 제거·길이 제한)."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _label_finding(finding: dict, index: int) -> str:
    finding_id = finding.get("id")
    if isinstance(finding_id, str) and finding_id.strip():
        return _clip(finding_id, 40)
    return f"findings[{index}]"


def load_design_review(raw: bytes) -> dict:
    """바이트를 design-review/v1 로 읽는다. 게이트가 읽는 필드가 어긋나면 DesignReviewMalformed.

    domain·asset_sha256 대조는 형식이 아니라 이 원고와의 결속 문제라 grade_design_review 가 한다."""
    try:
        review = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DesignReviewMalformed("UTF-8 로 읽히지 않음") from exc
    except json.JSONDecodeError as exc:
        raise DesignReviewMalformed(f"JSON 파싱 실패(line {exc.lineno} col {exc.colno})") from exc
    if not isinstance(review, dict):
        raise DesignReviewMalformed("최상위가 객체가 아님")
    if review.get("schema") != DESIGN_REVIEW_SCHEMA:
        raise DesignReviewMalformed(f"schema {_clip(repr(review.get('schema')))} ≠ {DESIGN_REVIEW_SCHEMA!r}")
    asset_sha256 = review.get("asset_sha256")
    if asset_sha256 is not None and not isinstance(asset_sha256, str):
        raise DesignReviewMalformed("asset_sha256 이 문자열/null 이 아님")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise DesignReviewMalformed("findings 가 목록이 아님(없으면 [] 가 계약)")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise DesignReviewMalformed(f"findings[{index}] 가 객체가 아님")
        for field, allowed in _VOCABULARY:
            if finding.get(field) not in allowed:
                raise DesignReviewMalformed(
                    f"{_label_finding(finding, index)}.{field}={_clip(repr(finding.get(field)), 30)} "
                    f"허용값 {'/'.join(allowed)} 밖")
    return review


def _effective_status(finding: dict) -> str:
    """사유 없는 waived 는 open — 리뷰어가 스스로 결함을 지우는 길을 막는다."""
    reason = finding.get("waiver_reason")
    if finding["status"] == "waived" and not (isinstance(reason, str) and reason.strip()):
        return "open"
    return finding["status"]


def summarize_design_findings(findings: list) -> str:
    """findings 개수 요약 한 줄."""
    statuses = [_effective_status(finding) for finding in findings]
    open_by_priority = {
        priority: sum(1 for finding, status in zip(findings, statuses)
                      if status == "open" and finding["priority"] == priority)
        for priority in ("P1", "P2", "P3")
    }
    unexplained = sum(1 for finding, status in zip(findings, statuses)
                      if finding["status"] == "waived" and status == "open")
    summary = (
        f"findings {len(findings)}건 — open {statuses.count('open')}"
        f"(P1 {open_by_priority['P1']}·P2 {open_by_priority['P2']}·P3 {open_by_priority['P3']})"
        f" · fixed {statuses.count('fixed')} · waived {statuses.count('waived')}"
        f" · defect {sum(1 for finding in findings if finding['kind'] == 'defect')}"
        f" · suggestion {sum(1 for finding in findings if finding['kind'] == 'suggestion')}"
    )
    if unexplained:
        summary += f" · 사유 없는 waived {unexplained}건은 open 으로 셈"
    return summary


def grade_design_review(review_path, *, draft_path) -> DesignReviewGrade:
    """리뷰 파일 하나를 등급화한다. 아무것도 쓰지 않는다. 원고는 리뷰가 있을 때만 읽는다."""
    path = Path(review_path)
    if not path.exists():
        return DesignReviewGrade("NOT_RUN", f"design review 미실행 — {path} 없음(비차단)")
    try:
        review = load_design_review(path.read_bytes())
    except OSError as exc:
        return DesignReviewGrade("AWAIT_HUMAN", f"design review 읽기 실패({type(exc).__name__}) — {path}")
    except DesignReviewMalformed as exc:
        return DesignReviewGrade("AWAIT_HUMAN", f"깨진 design review: {exc} — {path}")

    if review.get("domain") != DESIGN_REVIEW_DOMAIN:
        return DesignReviewGrade(
            "AWAIT_HUMAN",
            f"design review domain {_clip(repr(review.get('domain')), 30)} ≠ {DESIGN_REVIEW_DOMAIN!r} — {path}")
    asset_sha256 = review.get("asset_sha256")
    if asset_sha256 is not None:
        try:
            current = compute_draft_sha256(draft_path)
        except OSError as exc:
            return DesignReviewGrade("AWAIT_HUMAN", f"원고 sha256 계산 실패({type(exc).__name__}) — {path}")
        if asset_sha256.strip().lower() != current:
            return DesignReviewGrade(
                "AWAIT_HUMAN",
                f"오래된 design review — asset_sha256 {_clip(asset_sha256, 12)} ≠ 현재 원고 {current[:12]}…, "
                f"현재 원고로 다시 리뷰 필요 — {path}")

    findings = review["findings"]
    summary = summarize_design_findings(findings)
    open_p1_defects = [
        (index, finding) for index, finding in enumerate(findings)
        if _effective_status(finding) == "open" and finding["priority"] == "P1" and finding["kind"] == "defect"
    ]
    rule_ids = [_label_finding(finding, index) for index, finding in open_p1_defects if finding["check"] == "rule"]
    advisory_ids = [_label_finding(finding, index) for index, finding in open_p1_defects
                    if finding["check"] == "advisory"]
    advisory_text = f"open P1 defect(check=advisory) {len(advisory_ids)}건 사람 판단 필요: {', '.join(advisory_ids)}"
    if rule_ids:
        detail = f"open P1 defect(check=rule) {len(rule_ids)}건: {', '.join(rule_ids)}"
        if advisory_ids:
            detail += f"; {advisory_text}"
        return DesignReviewGrade("BLOCK", f"{detail} — {summary} — {path}")
    if advisory_ids:
        return DesignReviewGrade("AWAIT_HUMAN", f"{advisory_text} — {summary} — {path}")
    return DesignReviewGrade("PASS", f"design review 통과 — {summary} — {path}")
