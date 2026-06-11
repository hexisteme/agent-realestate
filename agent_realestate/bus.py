"""Orchestra 버스 워커 계약 — ../.orchestra/PROTOCOL.md 구현.

  register()      : registry/agent_realestate.json upsert (capabilities 등록)
  run_task(path)  : tasks/<id>.json(Task v1) 소비 → produce_report → results/<id>.json(Result v1)

원자적 write = 임시파일 → os.replace. 한 파일은 한 주체만 write.
시크릿 비노출(G4): SMTP/세션 자격을 봉투·로그에 담지 않는다.
계산은 결정론(G3): LTV/DSR·세금·전세수익률·5축 점수는 설치형 CLI 가 산출 — LLM 재계산 금지.

이 에이전트는 *독립 실행 워커* 다 (사용자 결정 2026-05-29). 라이브 호가(네이버부동산)·정책은
허브 세션이 수집·구조화해 context.injected{profile, candidates} 로 넣어준다 — 추정 금지(RDU-061),
없으면 needs 역신호로 정직 실패. 이메일 발송(side-effect)은 버스 경로에서 강제 OFF.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

AGENT_NAME = "agent_realestate"
EXT_ROOT = Path("/Volumes/EXT_SSD/bot/agent_realestate")
ORCHESTRA_ROOT = Path("/Volumes/EXT_SSD/bot/.orchestra")
REGISTRY_DIR = ORCHESTRA_ROOT / "registry"
RESULTS_DIR = ORCHESTRA_ROOT / "results"
LOG_DIR = ORCHESTRA_ROOT / "log"

CAPABILITIES = [
    "realestate_decision", "ltv_dsr_compute", "tax_compute",
    "jeonse_yield", "five_axis_scoring",
]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _log_append(event: str, task_id: str, **kw) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = {"ts": _now_iso(), "event": event, "task_id": task_id,
            "from": kw.pop("from_", AGENT_NAME), "to": kw.pop("to", "agent_council")}
    line.update(kw)
    p = LOG_DIR / f"{date.today().isoformat()}.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _resolve_cli() -> list[str]:
    console = EXT_ROOT / ".venv" / "bin" / "agent-realestate"
    if console.exists():
        return [str(console), "run", "--task-file"]
    return [sys.executable, "-m", "agent_realestate.cli", "run", "--task-file"]


def register() -> Path:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "agent": AGENT_NAME,
        "cwd": str(EXT_ROOT),
        "cli": _resolve_cli(),
        "headless_claude": "claude -p {prompt} --output-format json --permission-mode acceptEdits",
        "capabilities": CAPABILITIES,
        "input_contract": "tasks/<task_id>.json (Task v1)",
        "output_contract": "results/<task_id>.json (Result v1)",
        "max_parallel": 1,
        "health": "up",
        "registered_at": _now_iso(),
    }
    path = REGISTRY_DIR / f"{AGENT_NAME}.json"
    _atomic_write_json(path, entry)
    return path


def _build_claims(res: dict) -> list[dict]:
    """produce_report 결과 → Result/v1 claims. 재무수치는 결정론 계산(FACT), 5축 순위는 평가(INFERENCE)."""
    ranking = res.get("ranking", [])
    claims: list[dict] = [{
        "text": (f"결정론 계산(LTV/DSR·취득세·전세수익률·5축) — 후보 {res['evaluated_count']}개 평가, "
                 f"전략 {res['strategy']}. 모든 수치는 주입된 라이브 호가+정책 출처 기반(RDU-061)."),
        "provenance": "FACT",
        "evidence_ids": [f"report:{Path(res['out']).name}"],
    }]
    if ranking:
        top, top_score, top_flag = ranking[0]
        claims.append({
            "text": f"최우선 후보: {top} (조정점수 {top_score:.3f}){' ⚠️플래그 있음' if top_flag else ''}",
            "provenance": "INFERENCE",
            "evidence_ids": ["five_axis_scoring"],
        })
    for name, score, flagged in ranking:
        claims.append({
            "text": f"{name}: 조정점수 {score:.3f}{' ⚠️' if flagged else ''}",
            "provenance": "INFERENCE",
            "evidence_ids": ["five_axis_scoring"],
        })
    return claims


def run_task(task_path: str | Path) -> Path:
    """Task v1 소비 → produce_report → Result v1. 실패도 정직 보고(status:failed + needs)."""
    from agent_realestate.cli import produce_report

    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    task_id = task.get("task_id", f"unknown-{int(time.time()*1000)}")
    ctx = task.get("context", {}) or {}
    injected = ctx.get("injected") or {}
    reply_to = task.get("reply_to") or str(RESULTS_DIR / f"{task_id}.json")
    started = time.time()

    _log_append("task_issued", task_id, from_=task.get("from", "agent_council"), to=AGENT_NAME)

    def _fail(reason: str, needs: list | None = None) -> Path:
        result = {
            "task_id": task_id, "schema": "Result/v1", "agent": AGENT_NAME,
            "completed_at": _now_iso(), "status": "failed", "output_ref": None,
            "summary": f"리포트 실패: {reason}", "claims": [], "self_confidence": 0.0,
            "cost": {"tokens": 0, "wall_s": round(time.time() - started, 2), "free_ai_calls": 0},
            "utility_hooks": {"verifiable_ratio": 0.0, "candidate_count": 0},
        }
        if needs:
            result["needs"] = needs
        _atomic_write_json(Path(reply_to), result)
        _log_append("task_failed", task_id, to=AGENT_NAME, reason=reason)
        return Path(reply_to)

    profile = injected.get("profile")
    candidates = injected.get("candidates")
    if not profile or not candidates:
        missing = [k for k in ("profile", "candidates") if not injected.get(k)]
        return _fail(
            f"injected.{'/'.join(missing)} 누락 — 라이브 호가+프로필 주입 필요 (추정 금지)",
            needs=[{"capability": "live_listings+profile", "from": "caller",
                    "why": "네이버부동산 라이브 호가(candidates[])와 매수 프로필(profile, exit_strategy 포함)을 "
                           "context.injected 로 주입해야 결정론 계산 가능"}],
        )

    # candidates(list[dict]) → 임시파일 (load_candidates 는 path 를 받음).
    fd, tmp_input = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False)

    try:
        res = produce_report(
            profile=profile, input_path=tmp_input,
            insight=injected.get("insight") or ctx.get("insight"),
            council_session=injected.get("council_session"),
            council_models=injected.get("council_models"),
            no_email=True,  # side-effect 안전: 버스 경로에선 이메일 발송 금지
        )
        claims = _build_claims(res)
        fact_n = sum(1 for c in claims if c["provenance"] == "FACT")
        verifiable_ratio = round(fact_n / len(claims), 4) if claims else 0.0
        result = {
            "task_id": task_id, "schema": "Result/v1", "agent": AGENT_NAME,
            "completed_at": _now_iso(), "status": "done", "output_ref": res["out"],
            "summary": (f"부동산 매수 의사결정 리포트 — 후보 {res['evaluated_count']}개, 전략 "
                        f"{res['strategy']}, 최우선 {res['base_top']}. 결정론 계산(추정 0)."),
            "claims": claims,
            "self_confidence": verifiable_ratio,
            "cost": {"tokens": 0, "wall_s": round(time.time() - started, 2), "free_ai_calls": 0},
            "utility_hooks": {"verifiable_ratio": verifiable_ratio,
                              "candidate_count": res["evaluated_count"]},
        }
        _atomic_write_json(Path(reply_to), result)
        _log_append("task_done", task_id, to=AGENT_NAME, status="done",
                    verifiable_ratio=verifiable_ratio, outcome_score=None)
        return Path(reply_to)
    except (Exception, SystemExit) as e:  # Loud failure (SystemExit: _strategy G2 게이트 포함)
        return _fail(f"{type(e).__name__}: {e}")
    finally:
        if os.path.exists(tmp_input):
            os.unlink(tmp_input)
