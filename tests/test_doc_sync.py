"""capability.py → AGENT_CAPABILITIES.md auto-capabilities 블록 drift 가드.

agent_newTech 의 test_doc_sync_in_sync 패턴 이식. 문서(역량 manifest)가 코드 상수와
어긋나면(특히 '입력 진위검증' 데이터소스 상태) 검출 — `agent-realestate doc-sync` 로 재생성.
손정합 drift 를 구조적으로 차단.
"""

from __future__ import annotations

import pytest
from agent_realestate import config
from agent_realestate.capability import capability_reference_md


def test_doc_sync_in_sync():
    """AGENT_CAPABILITIES auto-capabilities 블록 == capability.capability_reference_md()
    (코드→문서 단일소스). 실패 시 `agent-realestate doc-sync` 실행 필요."""
    doc_path = config.EXT_ROOT / "AGENT_CAPABILITIES.md"
    if not doc_path.exists():   # 문서는 gitignore(/AGENT_CAPABILITIES.md) 라 CI 에는 없다 — 로컬 전용 가드(2026-09-05, 07-09 이후 CI 적색 원인)
        pytest.skip(f"{doc_path} 없음(로컬 전용 drift 가드)")
    doc = doc_path.read_text(encoding="utf-8")
    b = doc.find("<!-- BEGIN:auto-capabilities")
    e = doc.find("<!-- END:auto-capabilities -->")
    assert b != -1 and e != -1, "AGENT_CAPABILITIES auto-capabilities 마커 부재"
    block = doc[doc.find("-->", b) + 3:e].strip()
    assert block == capability_reference_md().strip(), \
        "AGENT_CAPABILITIES auto-capabilities 가 capability 상수와 drift — `agent-realestate doc-sync` 실행 필요"
