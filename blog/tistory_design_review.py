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
- 그 뒤 판정은 design-director 정본 검사기(`VALIDATOR_PATH`, validate_review.judge)가 정한다 — 소비자마다
  의미를 따로 구현해 생긴 드리프트를 없애려고 2026-10-21 통합했다. BLOCK → BLOCK, HUMAN → AWAIT_HUMAN:
  open P1 defect(rule) → BLOCK, open P1 defect(advisory)·결속 없는 리뷰(asset_sha256 null·키 누락)·계약 위반
  → AWAIT_HUMAN. 사유 없는 waive·에이전트가 승인자인 waive·기준선과 대조되지 않는 waiver_ref 는 open 으로 센다.
  waiver_ref 기준선은 그 경로(절대 경로) 또는 리뷰 옆·`BASELINE_DIRS` 아래 같은 파일명에서 찾는다.
- 검사기를 불러오지 못하면 AWAIT_HUMAN — 깨진 판정 도구를 통과로 취급하지 않는다.
- 그 밖 → PASS + 개수 요약 한 줄(부분 리뷰면 그 사실도).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Literal

DESIGN_REVIEW_SCHEMA = "design-review/v1"
DESIGN_REVIEW_DOMAIN = "blog"
DESIGN_REVIEW_SUFFIX = ".design_review.json"
VALIDATOR_PATH = Path(os.environ.get("DESIGN_REVIEW_VALIDATOR")
                      or Path.home() / ".agents/skills/design-director/scripts/validate_review.py")
# 발행 뒤 사람이 본 리뷰(사람 승인 waiver 기준선)가 모이는 곳 — 계약 §6.
BASELINE_DIRS = (Path("/Volumes/EXT_SSD/bot/report/design-review"),)
_VALIDATOR_IMPORT_NAME = "design_director_validate_review"

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


def load_validator():
    """design-director 정본 검사기(validate_review.py)를 불러온다. 판정 의미는 그 파일 하나에만 둔다."""
    validator = sys.modules.get(_VALIDATOR_IMPORT_NAME)
    if validator is None or getattr(validator, "__file__", None) != str(VALIDATOR_PATH):
        spec = importlib.util.spec_from_file_location(_VALIDATOR_IMPORT_NAME, VALIDATOR_PATH)
        if spec is None or spec.loader is None or not Path(VALIDATOR_PATH).is_file():
            raise ImportError(f"검사기 파일 없음: {VALIDATOR_PATH}")
        validator = importlib.util.module_from_spec(spec)
        sys.modules[_VALIDATOR_IMPORT_NAME] = validator  # dataclass 가 모듈을 sys.modules 에서 찾는다.
        try:
            spec.loader.exec_module(validator)
        except Exception:
            sys.modules.pop(_VALIDATOR_IMPORT_NAME, None)
            raise
    if not callable(getattr(validator, "judge", None)):
        raise ImportError(f"검사기에 judge 없음: {VALIDATOR_PATH}")
    return validator


def summarize_design_findings(findings: list, open_flags: list | None = None) -> str:
    """findings 개수 요약 한 줄. `open_flags` 는 검사기 judge 의 계약상 open 판정(없으면 적힌 status 그대로)."""
    flags = open_flags if open_flags is not None else [finding["status"] == "open" for finding in findings]
    statuses = ["open" if is_open else finding["status"] for finding, is_open in zip(findings, flags)]
    open_by_priority = {
        priority: sum(1 for finding, status in zip(findings, statuses)
                      if status == "open" and finding["priority"] == priority)
        for priority in ("P1", "P2", "P3")
    }
    reopened = sum(1 for finding, status in zip(findings, statuses)
                   if finding["status"] == "waived" and status == "open")
    summary = (
        f"findings {len(findings)}건 — open {statuses.count('open')}"
        f"(P1 {open_by_priority['P1']}·P2 {open_by_priority['P2']}·P3 {open_by_priority['P3']})"
        f" · fixed {statuses.count('fixed')} · waived {statuses.count('waived')}"
        f" · defect {sum(1 for finding in findings if finding['kind'] == 'defect')}"
        f" · suggestion {sum(1 for finding in findings if finding['kind'] == 'suggestion')}"
    )
    if reopened:
        summary += f" · 계약상 무효인 waived {reopened}건은 open 으로 셈"
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
    current = None
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

    try:
        judgement = load_validator().judge(review, domain=DESIGN_REVIEW_DOMAIN, asset_sha256=current,
                                           review_path=path, baseline_dirs=BASELINE_DIRS)
    except Exception as exc:  # noqa: BLE001 — 판정 도구가 죽으면 통과로 두지 않는다.
        return DesignReviewGrade(
            "AWAIT_HUMAN", f"design review 정본 검사기 실패({type(exc).__name__}: {_clip(exc)}) — {path}")
    findings = review["findings"]
    summary = summarize_design_findings(findings, judgement["open"])
    open_p1_defects = [
        (index, finding) for index, (finding, is_open) in enumerate(zip(findings, judgement["open"]))
        if is_open and finding["priority"] == "P1" and finding["kind"] == "defect"
    ]
    rule_ids = [_label_finding(finding, index) for index, finding in open_p1_defects if finding["check"] == "rule"]
    advisory_ids = [_label_finding(finding, index) for index, finding in open_p1_defects
                    if finding["check"] == "advisory"]
    parts = [f"open P1 defect(check=rule) {len(rule_ids)}건: {', '.join(rule_ids)}"] if rule_ids else []
    if advisory_ids:
        parts.append(f"open P1 defect(check=advisory) {len(advisory_ids)}건 사람 판단 필요: {', '.join(advisory_ids)}")
    parts += [_clip(reason, 160) for reason in judgement["context"]]
    if judgement["verdict"] == "BLOCK":
        return DesignReviewGrade("BLOCK", f"{'; '.join(parts)} — {summary} — {path}")
    if judgement["verdict"] != "PASS":
        return DesignReviewGrade("AWAIT_HUMAN", f"{'; '.join(parts) or '사람 판단 필요'} — {summary} — {path}")
    context = "".join(f" — {part}" for part in parts)
    return DesignReviewGrade("PASS", f"design review 통과 — {summary}{context} — {path}")
