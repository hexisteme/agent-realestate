from pathlib import Path

import pytest

from blog.run_daily import write_digest_outputs
from blog.tistory_contract import parse_helper


DIGEST = {
    "title": "서울 아파트 합성 다이제스트",
    "tags": "서울아파트,실거래가",
    "tistory_html": "<p>합성 티스토리 본문</p>",
    "site_html": "<!DOCTYPE html><html><head><title>서울 아파트 합성 다이제스트</title></head><body>합성 사이트 본문</body></html>",
    "summary": "합성 요약",
}


def test_write_digest_outputs_round_trips_and_keeps_dated_latest_identical(tmp_path):
    outputs = write_digest_outputs(DIGEST, "2026-09-26", str(tmp_path))
    assert parse_helper(outputs["draft"]) == {
        "title": DIGEST["title"], "tags": DIGEST["tags"], "body": DIGEST["tistory_html"],
    }
    assert Path(outputs["dated"]).read_text(encoding="utf-8") == DIGEST["site_html"]
    assert Path(outputs["latest"]).read_text(encoding="utf-8") == DIGEST["site_html"]


@pytest.mark.parametrize("bad", [
    {},
    {**DIGEST, "title": ""},
    {**DIGEST, "extra": "unexpected"},
])
def test_incomplete_or_ambiguous_digest_is_rejected_before_writing(tmp_path, bad):
    with pytest.raises(ValueError, match="contract"):
        write_digest_outputs(bad, "2026-09-26", str(tmp_path))
    assert not list(tmp_path.rglob("*"))
