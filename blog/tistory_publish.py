"""티스토리 일일 자동 게시 — 로그인된 Chrome 세션에 AppleScript로 주입.

경로 판정(2026-06-23 라이브 검증): 무인 자동발행 자체는 카카오 로그인 캡차로 막히지만,
**사용자가 로그인해 둔 Chrome 세션을 재사용**하면 글쓰기 에디터에 주입+카테고리선택+임시저장이 된다.
검증된 메커니즘(floker.tistory.com 글쓰기 = TinyMCE):
  - 제목   : textarea#post-title-inp  (value + input 이벤트)
  - 본문   : iframe#editor-tistory_ifr 의 body.innerHTML  (TinyMCE 라이브 본문)
  - 태그   : input#tagText
  - 카테고리: button#category-btn 클릭 → 메뉴(.mce-menu-item/.mce-text)에서 항목 텍스트 매칭 클릭
             ('서울(아파트)' 은 '부동산' 하위, 드롭다운 표기 '- 서울(아파트)')
  - 임시저장: 텍스트 '임시저장' 버튼(.action) → '저장 완료' 토스트, 임시저장 카운트 증가
  - 발행   : button#publish-layer-btn '완료' (공개 — 레이어 추가 매핑 필요, 미구현)

`tistory_draft.write_daily_draft` 가 만든 복사-헬퍼(report/blog/tistory/<날짜>-tistory-draft.html)
에서 제목/태그/본문을 그대로 파싱해 주입 → 게시 내용은 기존 파이프라인 산출물과 1:1 동일.
안전 롤아웃: 기본 mode='draft'(임시저장까지) — 사람이 검토 후 [발행]. 정상 확인 뒤 'publish' 승격.

카테고리 생성(트리 인라인편집+저장 AJAX)은 자동화 불안정 → 사용자가 1회 수동 생성('부동산 >
서울(아파트)'). 본 모듈은 *글마다 카테고리 선택*(반복)만 자동화한다.

AppleScript 가 각 단계 JS 파일을 UTF-8로 읽어 execute(따옴표 이스케이프 회피) + 'with timeout'
으로 -1712 방지. 사전조건(1회): Chrome '보기→개발자→Apple Events의 JavaScript 허용' ON + 로그인.
"""
from __future__ import annotations
import os, re, json, glob, html, tempfile, subprocess, argparse

NEWPOST_URL = os.environ.get("TISTORY_NEWPOST_URL", "https://floker.tistory.com/manage/newpost/")
CATEGORY_MATCH = os.environ.get("TISTORY_CATEGORY", "서울(아파트)")  # 드롭다운 항목 텍스트 부분일치
DRAFT_GLOB = "report/blog/tistory/*-tistory-draft.html"


def _parse_helper(path: str) -> dict:
    """복사-헬퍼 페이지의 textarea(id=t/g/b)에서 제목·태그·본문 HTML 을 복원."""
    src = open(path, encoding="utf-8").read()

    def field(fid: str) -> str:
        m = re.search(rf'<textarea id={fid}[^>]*>(.*?)</textarea>', src, re.DOTALL)
        return html.unescape(m.group(1)) if m else ""

    return {"title": field("t").strip(), "tags": field("g").strip(), "body": field("b").strip()}


def _latest_draft(outroot: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(outroot, DRAFT_GLOB)), reverse=True)
    return hits[0] if hits else None


# ── 단계별 JS 생성 (데이터는 json.dumps 로 안전한 JS 리터럴) ──────────────────────
def _js_inject(title: str, body: str, tags: str) -> str:
    return f"""(function(){{
  try {{
    var TITLE={json.dumps(title)}, BODY={json.dumps(body)}, TAGS={json.dumps(tags)};
    var ti=document.getElementById('post-title-inp');
    if(!ti) return 'ERR:no_title_input';
    ti.value=TITLE; ti.dispatchEvent(new Event('input',{{bubbles:true}}));
    var ifr=document.getElementById('editor-tistory_ifr');
    if(!ifr) return 'ERR:no_editor_iframe';
    var d=ifr.contentDocument||ifr.contentWindow.document;
    d.body.innerHTML=BODY;
    ['input','keyup','change'].forEach(function(t){{ d.body.dispatchEvent(new Event(t,{{bubbles:true}})); }});
    var tg=document.getElementById('tagText');
    if(tg && TAGS){{ tg.value=TAGS; tg.dispatchEvent(new Event('input',{{bubbles:true}})); }}
    return 'INJECT_OK:bodyLen='+d.body.innerHTML.length;
  }} catch(e) {{ return 'INJECT_EXC:'+(e&&e.message||e); }}
}})()"""


_JS_CAT_OPEN = """(function(){
  var b=document.getElementById('category-btn')||[].slice.call(document.querySelectorAll('button,a')).filter(function(x){return /카테고리더보기/.test(x.textContent||'');})[0];
  if(!b) return 'NO_CAT_BTN';
  b.click(); return 'CAT_OPENED';
})()"""


def _js_cat_select(match: str) -> str:
    return f"""(function(){{
  var M={json.dumps(match)};
  var it=[].slice.call(document.querySelectorAll('.mce-menu-item,.mce-text,li,a,span')).filter(function(x){{
    return ((x.textContent||'').replace(/\\s+/g,' ').trim()).indexOf(M)>-1 && (x.textContent||'').length<40;
  }})[0];
  if(!it) return 'CAT_NOTFOUND:'+M;
  ((it.closest && it.closest('.mce-menu-item')) || it).click();
  return 'CAT_SELECTED:'+M;
}})()"""


# 단계 JS (검증된 셀렉터, 2026-06-23):
_JS_DRAFT = ("(function(){var s=[].slice.call(document.querySelectorAll('button,a'))"
             ".filter(function(x){return (x.textContent||'').trim()==='임시저장';})[0];"
             "if(!s)return 'NO_SAVE_BTN'; s.click(); return 'DRAFT_SAVED';})()")
# 발행(공개): 완료 레이어 열기 → 공개 라디오(#open20) 선택 → 발행 버튼(#publish-btn).
_JS_COMPLETE_OPEN = ("(function(){var b=document.getElementById('publish-layer-btn');"
                     "if(!b)return 'NO_COMPLETE_BTN'; b.click(); return 'COMPLETE_OPENED';})()")
_JS_SELECT_PUBLIC = ("(function(){var r=document.getElementById('open20');"  # open20=공개, open0=비공개
                     "if(!r)return 'NO_PUBLIC_RADIO'; r.click(); r.checked=true;"
                     "r.dispatchEvent(new Event('change',{bubbles:true}));"
                     "var b=document.getElementById('publish-btn');"
                     "return 'PUBLIC_SELECTED:btn='+(b?(b.textContent||'').trim():'?');})()")
_JS_PUBLISH_GO = ("(function(){var b=document.getElementById('publish-btn');"
                  "if(!b)return 'NO_PUBLISH_BTN'; var t=(b.textContent||'').trim(); b.click();"
                  "return 'PUBLISH_GO:'+t;})()")


def _steps_for(mode: str, title: str, body: str, tags: str) -> list[str]:
    """mode 별 실행할 JS 단계 목록 (순서대로, 각 사이 2s delay)."""
    base = [_js_inject(title, body, tags), _JS_CAT_OPEN, _js_cat_select(CATEGORY_MATCH)]
    if mode == "draft":
        return base + [_JS_DRAFT]
    if mode == "publish":
        return base + [_JS_COMPLETE_OPEN, _JS_SELECT_PUBLIC, _JS_PUBLISH_GO]
    return base  # inject: 주입+카테고리만


_APPLESCRIPT = '''on run argv
  set npURL to item 1 of argv
  set out to ""
  with timeout of 240 seconds
    tell application "Google Chrome"
      activate                      -- 전면화: 07:05 백그라운드 렌더러 throttle 로 인한 -1712 방지
      delay 1
      set target to missing value
      repeat with w in windows
        set k to 0
        repeat with tb in tabs of w
          set k to k + 1
          if (URL of tb) contains "manage/newpost" then
            set target to tb
            set active tab index of w to k   -- newpost 탭을 활성 탭으로
            set index of w to 1              -- 그 창을 맨 앞으로
          end if
        end repeat
      end repeat
      if target is missing value then
        set target to make new tab at end of tabs of (front window) with properties {URL:npURL}
        set index of (front window) to 1
      else
        set URL of target to npURL       -- 재사용 탭도 항상 새로 로드: 스테일 상태(no_title_input 원인) 차단
      end if
      -- 에디터 준비 폴링: #post-title-inp + 본문 iframe 이 나타날 때까지 최대 ~40s 대기.
      -- (고정 delay 8 은 07:05 부하/스테일 탭에서 부족 → 06-26·27 no_title_input 캐스케이드 원인.)
      set ready to false
      repeat 20 times
        delay 2
        try
          set chk to (execute target javascript "(document.getElementById('post-title-inp')&&document.getElementById('editor-tistory_ifr'))?'READY':'WAIT'")
        on error
          set chk to "WAIT"
        end try
        if chk is "READY" then
          set ready to true
          exit repeat
        end if
      end repeat
      if not ready then return "ERR:editor_not_ready_after_poll(~40s) — 로그인/페이지로드 확인"
      repeat with i from 2 to (count of argv)
        if out is not "" then set out to out & " | "
        set out to out & (execute target javascript (read POSIX file (item i of argv) as «class utf8»))
        delay 2
      end repeat
      delay 3                            -- 발행 후 리다이렉트 대기
      set out to out & " | FINAL_URL=" & (URL of target)   -- 발행 검증: newpost 이탈이면 게시 성공 신호
    end tell
  end timeout
  return out
end run'''


def _write(tmp: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    f.write(tmp); f.close(); return f.name


def publish(outroot: str = ".", mode: str = "draft", date: str | None = None) -> str:
    """최신(또는 지정일) 티스토리 draft 를 로그인된 Chrome 에 주입 + 카테고리 선택 + (저장|공개발행).
    mode: inject(주입+카테고리만) | draft(임시저장) | publish(공개 발행)."""
    if date:
        path = os.path.join(outroot, f"report/blog/tistory/{date}-tistory-draft.html")
    else:
        path = _latest_draft(outroot)
    if not path or not os.path.isfile(path):
        return f"ERR:no draft helper found ({path})"
    data = _parse_helper(path)
    if not data["body"]:
        return f"ERR:empty body parsed from {path}"

    paths = [_write(js) for js in _steps_for(mode, data["title"], data["body"], data["tags"])]
    af = tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False, encoding="utf-8")
    af.write(_APPLESCRIPT); af.close()

    def _run_once() -> str:
        r = subprocess.run(["osascript", af.name, NEWPOST_URL, *paths],
                           capture_output=True, text=True, timeout=300)
        return (r.stdout or "").strip() or (r.stderr or "").strip()

    try:
        out = _run_once()
        # 타임아웃/에러이고 종단 동작(발행·저장)이 아직 안 일어났을 때만 1회 재시도
        # (PUBLISH_GO/DRAFT_SAVED 가 이미 찍혔으면 재시도 시 중복발행 위험 → 금지).
        failed = (("-1712" in out) or ("시간 초과" in out) or ("execution error" in out)
                  or ("editor_not_ready" in out) or ("no_title_input" in out) or (not out))
        no_terminal = ("PUBLISH_GO" not in out) and ("DRAFT_SAVED" not in out)
        if failed and no_terminal:
            out = _run_once() + "  [retried]"
        return f"[{os.path.basename(path)}] {out}"
    finally:
        for p in (*paths, af.name):
            try: os.unlink(p)
            except OSError: pass


def main():
    ap = argparse.ArgumentParser(description="티스토리 draft 를 로그인 Chrome 에 주입(카테고리 선택+반자동 게시)")
    ap.add_argument("--mode", choices=["inject", "draft", "publish"], default="draft")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 최신)")
    ap.add_argument("--outroot", default=".", help="agent_realestate 루트 (기본: cwd)")
    a = ap.parse_args()
    print(publish(a.outroot, a.mode, a.date))


if __name__ == "__main__":
    main()
