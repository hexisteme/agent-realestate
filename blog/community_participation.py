"""구·단지별 공개 생활 관찰 제보 진입점.

정적 사이트는 자체 계정/댓글 저장소를 두지 않는다. GitHub Issue Form의 공개 이력과 신고 기능을
사용하되, 제출 내용은 검토 전 가격·후기·점수·순위에 자동 합산하지 않는다.
"""

from __future__ import annotations

import html
from urllib.parse import urlencode


REPOSITORY_URL = "https://github.com/hexisteme/agent-realestate"
ISSUE_TEMPLATE = "resident-observation.yml"


def observation_issue_url(gu: str, complex_name: str | None = None) -> str:
    gu_text = str(gu or "").strip()
    complex_text = str(complex_name or "").strip()
    subject = f"[생활관찰][{gu_text}]"
    if complex_text:
        subject += f" {complex_text}"
    query = urlencode({"template": ISSUE_TEMPLATE, "title": subject})
    return f"{REPOSITORY_URL}/issues/new?{query}"


def observation_list_url(gu: str, complex_name: str | None = None) -> str:
    gu_text = str(gu or "").strip()
    complex_text = str(complex_name or "").strip()
    marker = f"[생활관찰][{gu_text}]"
    if complex_text:
        marker += f" {complex_text}"
    query = urlencode({"q": f'is:issue state:open in:title "{marker}"'})
    return f"{REPOSITORY_URL}/issues?{query}"


def _tracking_script() -> str:
    # 자유문·계정·이메일은 analytics로 보내지 않는다. 고정된 화면/구/단지명만 전송한다.
    return (
        "<script>(()=>{const a=document.currentScript.previousElementSibling;"
        "if(!a)return;a.addEventListener('click',()=>{if(Array.isArray(window.dataLayer))"
        "window.dataLayer.push({event:'community_report_click',surface:a.dataset.surface,"
        "gu:a.dataset.gu,complex_name:a.dataset.complex||'',category:'observation'});});})();</script>"
    )


def _cta(gu: str, complex_name: str | None, surface: str) -> str:
    gu_text = str(gu or "").strip()
    complex_text = str(complex_name or "").strip()
    label = f"{complex_text} 생활 관찰 제보하기" if complex_text else f"{gu_text} 생활 관찰 제보하기"
    attrs = {
        "href": observation_issue_url(gu_text, complex_text or None),
        "data-surface": surface,
        "data-gu": gu_text,
        "data-complex": complex_text,
    }
    rendered = " ".join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())
    submit_link = (
        f'<a {rendered} target="_blank" rel="nofollow noopener" '
        'style="display:inline-block;margin-top:7px;padding:8px 11px;border-radius:7px;'
        'background:#1d6f6a;color:#fff;font-weight:600;text-decoration:none">'
        f'{html.escape(label)}</a>'
    )
    browse_label = f"{complex_text} 관찰·댓글 보기" if complex_text else f"{gu_text} 관찰·댓글 보기"
    browse_link = (
        f'<a href="{html.escape(observation_list_url(gu_text, complex_text or None), quote=True)}" '
        'target="_blank" rel="nofollow noopener" style="display:inline-block;margin:7px 0 0 8px">'
        f'{html.escape(browse_label)}</a>'
    )
    return submit_link + _tracking_script() + browse_link


def render_gu_panel(gu: str) -> str:
    gu_text = html.escape(str(gu or ""))
    return (
        '<section class="panel"><h2>구별 주민 관찰 커뮤니티</h2>'
        f'<p>{gu_text}의 주차·난방·소음·교통·보행·시설 변화를 날짜와 함께 제보하고 댓글로 보완할 수 있습니다.</p>'
        '<p>GitHub 공개 계정과 글이 노출됩니다. 동·호수, 전화번호, 실명, 아동 정보, '
        '거주 인증자료와 특정인 비방은 올리지 마세요.</p>'
        '<p><b>제보는 게시 전 검토 대상이며 외부 후기 표본·가격·점수·순위에 자동 합산되지 않습니다.</b></p>'
        f'{_cta(str(gu or ""), None, "gu")}</section>'
    )


def render_complex_card(gu: str, complex_name: str) -> str:
    name_text = html.escape(str(complex_name or ""))
    return (
        '<section class="card"><div class="q">이 사이트에 새 관찰 제보</div>'
        f'<p>{name_text}의 주차·난방·소음·교통·시설 변화를 날짜와 함께 남길 수 있습니다.</p>'
        '<p style="font-size:12px;color:#5c584f">공개 GitHub 제보이며 개인정보·거주 인증자료·특정인 '
        '비방을 올리면 안 됩니다. 검토 전에는 외부 후기 표본과 어떤 수치에도 합산되지 않습니다.</p>'
        f'{_cta(gu, complex_name, "complex")}</section>'
    )
