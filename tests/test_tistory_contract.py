from pathlib import Path

import pytest

from blog.tistory_contract import (
    DraftContractError,
    DraftPayload,
    parse_helper,
    parse_helper_text,
    render_helper,
    write_helper,
)
from blog import tistory_publish, tistory_publish_pw


def test_helper_round_trip_preserves_entities_and_body_markup(tmp_path):
    payload = DraftPayload(
        title="서울 A&B <오늘>",
        tags="서울아파트,실거래가",
        body='<p data-x="a&b">5억 &amp; 6억</p>',
    )
    path = write_helper(tmp_path / "draft.html", payload, "2026-09-26")
    assert parse_helper(path) == payload.as_dict()
    assert parse_helper_text(render_helper(payload, "2026-09-26")) == payload
    assert Path(path).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("source", [
    "<p>raw body only</p>",
    "<textarea id=t>title</textarea><textarea id=g>tags</textarea>",
    "<textarea id=t>title</textarea><textarea id=t>other</textarea>"
    "<textarea id=g>tags</textarea><textarea id=b>body</textarea>",
    "<textarea id=t></textarea><textarea id=g>tags</textarea><textarea id=b>body</textarea>",
])
def test_non_helper_or_ambiguous_helper_is_rejected(source):
    with pytest.raises(DraftContractError):
        parse_helper_text(source)


def test_both_publishers_fail_closed_before_browser_on_raw_body(tmp_path):
    draft_dir = tmp_path / "report/blog/tistory"
    draft_dir.mkdir(parents=True)
    (draft_dir / "2026-09-26-tistory-draft.html").write_text("<p>raw body</p>", encoding="utf-8")
    assert tistory_publish.publish(str(tmp_path), mode="inject", date="2026-09-26").startswith(
        "ERR:draft contract invalid"
    )
    assert tistory_publish_pw.publish(str(tmp_path), mode="inject", date="2026-09-26") == (
        "ERR:draft contract invalid"
    )
