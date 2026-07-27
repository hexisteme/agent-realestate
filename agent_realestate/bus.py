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
import re
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


# 경로 탈출 방어 (2026-07-27, RDU-165 — agent_intel 참조 구현 이식): .orchestra 버스는 공유
# 파일시스템 — task_id/reply_to 는 비신뢰 입력으로 취급한다. task_id 는 식별자(경로 아님)이므로
# 안전 문자만 허용, reply_to 는 RESULTS_DIR 밖이면 거부.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_TASK_ID_MAX_LEN = 128  # 실측 라이브버스 최대 49자(평균 44) — 128은 여유상한, NAME_MAX(255) 이하 (C4)


def _safe_task_id(task_id: str) -> str:
    """task_id 를 경로로 쓰기 전 검증 — '/' '..' 등 경로 인젝션 차단 (영숫자._- 만 허용),
    길이 상한 초과는 거부 (C4). 거부 전 감사로그 선행 (C13)."""
    tid = str(task_id)
    if len(tid) > _TASK_ID_MAX_LEN:
        reason = f"task_id 길이 초과 — 최대 {_TASK_ID_MAX_LEN}자, 실제 {len(tid)}자"
        _log_append("task_id_rejected", tid[:300], to="agent_council", reason=reason)
        raise ValueError(reason)
    if not _SAFE_ID.fullmatch(tid):
        # fullmatch (2026-07-27, C2) — match() 는 "abc\n" 처럼 후행 개행 앞에서 $ 가 매칭되어 통과시켰다.
        reason = f"task_id 형식 위반 — 경로 인젝션 차단: {task_id!r} (영숫자._- 만 허용)"
        _log_append("task_id_rejected", tid[:300], to="agent_council", reason=reason)
        raise ValueError(reason)
    return tid


def _confine(candidate: str | Path, base: Path) -> Path:
    """candidate 가 base 하위로 resolve 되는지 검증 — arbitrary write 차단. 상대경로는 base 기준.
    candidate 가 base 자신과 같아도 거부 (C5) — 컨테인먼트만 보면 통과하지만 파일이 아닌
    디렉토리를 향한 쓰기라 os.replace 가 나중에 알아보기 어렵게 실패한다. 거부 전 감사로그
    선행 (C13)."""
    base_r = base.resolve()
    p = Path(candidate)
    p = (base_r / p).resolve() if not p.is_absolute() else p.resolve()
    if p == base_r or not p.is_relative_to(base_r):
        reason = f"경로가 허용 디렉토리 밖(또는 디렉토리 자체) — 차단: {candidate!r} ∉ {base_r}"
        _log_append("confine_rejected", str(candidate)[:300], to="agent_council",
                    reason=reason, base=str(base_r))
        raise ValueError(reason)
    return p


def _require_own_reply_to(raw_reply_to, task_id: str) -> str | None:
    """C3/F2 (2026-07-27 round 3, codex+agy 교차검증): reply_to 는 이 task 자신의 결과파일만
    가리켜야 한다 — 라이브 버스 실측(1070/1070 Task/v1 이 reply_to 를 '/{task_id}.json' 로
    끝맺음, 불일치 0건)으로 강제해도 깨지는 게 없다. basename 이 f'{task_id}.json' 과 다르면
    (다른 task 의 Result 를 덮어쓸 수 있음) *더 이상 조용히 기본값으로 대체하지 않는다* — 그
    조용한 대체가 바로 F1(envelope task_id 위조)을 형제 파일 덮어쓰기 공격으로 완성시키는
    경로였고, 허브가 이 reply_to 로 기다리면 loud failure 대신 Result blackhole 이 된다.
    거부 전 감사로그 선행 (C13), 이후 raise (loud, 더 이상 fallback 아님)."""
    if not raw_reply_to:
        return None
    expected = f"{task_id}.json"
    if Path(str(raw_reply_to)).name != expected:
        reason = f"reply_to basename != {expected!r} — 다른 task 파일 지정 거부 (C3/F2, 대체 없이 raise)"
        _log_append("reply_to_rejected", task_id, to="agent_council",
                    reply_to=str(raw_reply_to)[:300], reason=reason)
        raise ValueError(reason)
    return str(raw_reply_to)


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
    # 신뢰경계 검증을 try 밖에서 먼저 — 위조 task_id/reply_to 는 흡수하지 않고 거부(refuse-to-act, loud).
    # F1 (2026-07-27 round 3, codex+agy 교차검증): task_id 는 envelope 이 아니라 *task 파일명*
    # 에서 도출한다 — envelope 은 비신뢰 입력이라 자신을 다른 task_id 로 사칭해 형제 Result 를
    # 덮어쓸 수 있었다 (tasks/attacker.json 이 {"task_id":"victim"} 을 주장 → results/victim.json
    # 덮어쓰기). envelope 에 task_id 가 있으면 파일명 기반 canonical id 와 일치해야 하며,
    # 불일치는 흡수하지 않고 거부(raise, loud) — "고쳐서 계속" 하지 않는다.
    task_id = _safe_task_id(Path(task_path).stem)
    envelope_task_id = task.get("task_id")
    if envelope_task_id is not None and _safe_task_id(str(envelope_task_id)) != task_id:
        reason = (f"envelope task_id {envelope_task_id!r} != 파일명 기반 task_id {task_id!r}"
                   " — envelope 이 다른 task 를 사칭 (F1 차단)")
        _log_append("task_id_mismatch_rejected", task_id, to="agent_council",
                    envelope_task_id=str(envelope_task_id)[:300], reason=reason)
        raise ValueError(reason)
    ctx = task.get("context", {}) or {}
    injected = ctx.get("injected") or {}
    reply_to = str(_confine(_require_own_reply_to(task.get("reply_to"), task_id)
                            or RESULTS_DIR / f"{task_id}.json", RESULTS_DIR))
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
