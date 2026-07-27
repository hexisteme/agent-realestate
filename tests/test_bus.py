"""Task/v1 경로 가드(task_id/reply_to) 회귀 테스트 — 2026-07-27 RDU-165 감사 이식,
round 3(F1/F2, codex+agy 교차검증)로 갱신: agent_intel 참조 구현과 동형(isomorphic).
_safe_task_id 는 fullmatch+길이상한(C2/C4), _confine 은 base 자신도 거부(C5), task_id 는
이제 task *파일명*에서 도출(F1)하고 envelope 과 불일치하면 raise, reply_to 는 이 task
자신의 결과파일명이 아니면 대체 없이 raise(C3/F2, 거부 전 로그 C13)."""
import json
from pathlib import Path

import pytest

from agent_realestate import bus


def _write_task(tmp_path, task: dict, filename: str | None = None) -> Path:
    """filename 기본값은 task 자신의 task_id (2026-07-27 round 3, F1): 라이브 버스는 항상
    task_id 로 파일명을 짓고, run_task() 는 이제 그 파일명에서 canonical id 를 뽑는다 —
    파일명과 envelope 이 *일치*하길 원하는 테스트는 filename 을 생략하면 된다. envelope
    task_id 자체가 손상된 테스트는 그 손상된 문자열을 파일명 성분으로 쓰지 않도록(테스트
    자신이 경로탈출을 하게 될 수 있음) 명시적으로 안전한 filename 을 넘긴다."""
    p = tmp_path / (filename or f"{task['task_id']}.json")
    p.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    return p


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(bus, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(bus, "LOG_DIR", tmp_path / "log")
    (tmp_path / "results").mkdir()


# ── C2: 후행 개행이 fullmatch 로 차단됨(match() 는 개행 앞에서 $ 가 매칭돼 통과시켰다) ──
def test_task_id_trailing_newline_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    task = {"task_id": "abc\n", "reply_to": str(tmp_path / "results" / "r.json")}
    tp = _write_task(tmp_path, task, filename="task.json")   # 손상된 task_id 를 파일명에 안 씀
    with pytest.raises(ValueError, match="형식 위반"):
        bus.run_task(tp)


# ── C4: task_id 길이 상한(128자) 초과 거부 ─────────────────────────
def test_task_id_over_length_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    long_id = "a" * 129
    task = {"task_id": long_id, "reply_to": str(tmp_path / "results" / f"{long_id}.json")}
    tp = _write_task(tmp_path, task, filename="task.json")
    with pytest.raises(ValueError, match="길이 초과"):
        bus.run_task(tp)


# ── C3/F2 (round 3): reply_to 가 '다른 task' 의 결과파일을 가리키면 raise (대체 없음) ──
def test_reply_to_sibling_task_file_rejected(tmp_path, monkeypatch):
    """그 조용한 대체가 F1(envelope task_id 위조)을 형제 Result 덮어쓰기로 완성시키는
    경로였다 — round 3 부터는 대체 없이 raise."""
    _isolate(tmp_path, monkeypatch)
    sibling = tmp_path / "results" / "OTHER_TASK.json"
    task = {"task_id": "t-mine", "reply_to": str(sibling)}
    tp = _write_task(tmp_path, task)
    with pytest.raises(ValueError, match="reply_to basename"):
        bus.run_task(tp)
    assert not sibling.exists()                                  # 형제 task 결과 안 덮어씀
    assert not (tmp_path / "results" / "t-mine.json").exists()    # 대체 경로에도 안 써짐


# ── C5/F2: reply_to 가 RESULTS_DIR 자기 자신을 가리켜도 raise ──────────────
def test_reply_to_naming_results_dir_itself_rejected(tmp_path, monkeypatch):
    """basename("results") 이 f"{task_id}.json" 과 다르므로 _require_own_reply_to 단계에서
    이미 raise — _confine 의 자기자신(C5) 분기는 이 경로에서 더 이상 도달하지 않는다."""
    _isolate(tmp_path, monkeypatch)
    task = {"task_id": "t-dir", "reply_to": str(tmp_path / "results")}
    tp = _write_task(tmp_path, task)
    with pytest.raises(ValueError, match="reply_to basename"):
        bus.run_task(tp)
    assert not (tmp_path / "results" / "t-dir.json").exists()


# ── F1 회귀: envelope task_id 위조가 형제 task 의 Result 를 덮어쓰지 못함 ──────────
def test_envelope_task_id_mismatch_with_filename_rejected(tmp_path, monkeypatch):
    """round 3 공격 재현: tasks/attacker.json 이 envelope 에 {"task_id": "victim", ...} 를
    넣어 파일명과 다른 task 를 사칭한다. canonical id 는 파일명(attacker)에서 뽑으므로
    불일치를 즉시 거부해야 하고, victim 의 기존 Result 는 절대 건드려지면 안 된다."""
    _isolate(tmp_path, monkeypatch)
    victim_result = tmp_path / "results" / "victim.json"
    sentinel = json.dumps({"status": "done", "task_id": "victim", "sentinel": "untouched"})
    victim_result.write_text(sentinel, encoding="utf-8")
    task = {"task_id": "victim", "reply_to": "results/attacker.json"}
    tp = _write_task(tmp_path, task, filename="attacker.json")
    with pytest.raises(ValueError) as excinfo:
        bus.run_task(tp)
    msg = str(excinfo.value)
    assert "victim" in msg and "attacker" in msg
    assert victim_result.read_text(encoding="utf-8") == sentinel
    assert not (tmp_path / "results" / "attacker.json").exists()


# ── F4 (Codex 발견 gap): basename 이 일치해도 _confine 이 실제 탈출을 잡아야 한다 ──────
# 위 C3/C5 테스트는 reply_to basename 이 task_id 와 달라 _require_own_reply_to 에서 먼저
# 걸려 _confine 자체는 한 번도 실행되지 않았다. 아래 두 테스트는 basename 을
# f"{task_id}.json" 로 정확히 맞춰 _confine 을 직접 실행시킨다.
def test_reply_to_absolute_outside_with_matching_basename_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    task_id = "t-f4-abs-realestate"
    outside = Path("/tmp") / f"{task_id}.json"
    try:
        task = {"task_id": task_id, "reply_to": str(outside)}
        tp = _write_task(tmp_path, task)
        with pytest.raises(ValueError, match="허용 디렉토리 밖"):
            bus.run_task(tp)
        assert not outside.exists()
        assert not (tmp_path / "results" / f"{task_id}.json").exists()
    finally:
        outside.unlink(missing_ok=True)   # 방어가 실패해도 실제 /tmp 오염 방지


def test_reply_to_relative_escape_with_matching_basename_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    task_id = "t-f4-rel-realestate"
    escaped = tmp_path / "results" / ".." / f"{task_id}.json"   # resolve 하면 tmp_path/{id}.json
    task = {"task_id": task_id, "reply_to": str(escaped)}
    tp = _write_task(tmp_path, task)
    original_task_bytes = tp.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="허용 디렉토리 밖"):
        bus.run_task(tp)
    # resolve 된 탈출 목표(tmp_path/{id}.json)는 이 task 파일 자신의 위치와 우연히 같다 —
    # "존재 안 함"이 아니라 "Result 로 덮어써지지 않음"(내용 불변)이 올바른 증명이다.
    assert tp.read_text(encoding="utf-8") == original_task_bytes
    assert not (tmp_path / "results" / f"{task_id}.json").exists()


# ── G1 (2026-07-27, 라운드3 codex+agy 독립 발견): 소문자만 — APFS 케이스 폴딩 ────────────
def test_apfs_case_variant_cannot_alias_existing_result(tmp_path, monkeypatch):
    """APFS 는 대소문자 무구분 — results/victim.json 과 results/VICTIM.json 이 같은 inode 다.
    대문자 id 를 허용하면 문자열 비교는 통과하는데 파일시스템이 둘을 접어 덮어쓴다. 대문자는
    거부되므로(소문자 변환 없이) 기존 Result 가 보존된다."""
    _isolate(tmp_path, monkeypatch)
    victim = tmp_path / "results" / "victim.json"
    done = json.dumps({"task_id": "victim", "status": "done"})
    victim.write_text(done, encoding="utf-8")
    task = {"task_id": "VICTIM", "reply_to": "results/VICTIM.json"}
    tp = _write_task(tmp_path, task, filename="VICTIM.json")
    with pytest.raises(ValueError):
        bus.run_task(tp)
    assert victim.read_text(encoding="utf-8") == done


@pytest.mark.parametrize("bad", ["abc\n", "a/b", "../../evil/id", "", "a" * 129,
                                 "a b", "Victim", "VICTIM", "aBc"])
def test_safe_task_id_rejects_unsafe(tmp_path, monkeypatch, bad):
    """G1: 대소문자 혼용/전부대문자도 다른 불안전 형태와 동일하게 거부."""
    _isolate(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        bus._safe_task_id(bad)


# ── G2 (2026-07-27, 라운드3 codex+agy): 생략 vs 명시적 null, str() 세탁 제거 ──────────────
def test_omitted_task_id_uses_filename(tmp_path, monkeypatch):
    """생략된 task_id 는 거부되지 않고 파일명 기반 canonical id 로 처리된다."""
    _isolate(tmp_path, monkeypatch)
    task = {"reply_to": str(tmp_path / "results" / "t-omitted.json")}
    tp = _write_task(tmp_path, task, filename="t-omitted.json")
    reply = bus.run_task(tp)
    result = json.loads(reply.read_text(encoding="utf-8"))
    assert result["task_id"] == "t-omitted"


def test_explicit_null_task_id_is_rejected(tmp_path, monkeypatch):
    """명시적 null 은 생략과 구분되어 거부된다 (이전엔 `is not None` 검사가 둘을 섞었다)."""
    _isolate(tmp_path, monkeypatch)
    task = {"task_id": None, "reply_to": str(tmp_path / "results" / "t-null.json")}
    tp = _write_task(tmp_path, task, filename="t-null.json")
    with pytest.raises(ValueError, match="문자열이 아님"):
        bus.run_task(tp)
    assert not (tmp_path / "results" / "t-null.json").exists()


@pytest.mark.parametrize("bad_type", [123, True, [], {}])
def test_non_string_task_id_is_rejected(tmp_path, monkeypatch, bad_type):
    """비문자열 task_id 는 str() 로 세탁되지 않고 타입 자체로 거부된다."""
    _isolate(tmp_path, monkeypatch)
    task = {"task_id": bad_type, "reply_to": str(tmp_path / "results" / "t-type.json")}
    tp = _write_task(tmp_path, task, filename="t-type.json")
    with pytest.raises(ValueError, match="문자열이 아님"):
        bus.run_task(tp)


# ── agy 라운드3 LOW: _confine 의 p == base_r 분기 직접 단위테스트 ────────────────────────
def test_confine_rejects_base_dir_itself(tmp_path, monkeypatch):
    """reply_to 경유로는 basename 체크가 먼저 raise 해 p == base_r 분기가 한 번도 실행되지
    않는다 — 기존 test_reply_to_naming_results_dir_itself_rejected 는 그래서 맹목적이다.
    _confine 을 직접 호출해 그 분기를 실행시킨다."""
    _isolate(tmp_path, monkeypatch)
    results = tmp_path / "results"
    with pytest.raises(ValueError, match="허용 디렉토리 밖"):
        bus._confine(results, results)
