"""거시 진입 계측: Node 가짜 DOM에서 이벤트 계약 검증. Google 로더·네트워크는 실행하지 않는다."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from blog import build_site
from blog.macro_context import build_macro_context, render_macro_strip_site
from blog.macro_entry import MACRO_ENTRY_JS, macro_entry_attributes


_NODE_DOM = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const source = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
function boot(options = {}) {
  const events = [], callbacks = {}, windowCallbacks = {}, observed = [];
  const a = {dataset: {macroEntry: 'macro', macroSurface: options.surface || 'index'},
    rect: {left: 0, right: 100, top: 900, bottom: 1000, width: 100, height: 100},
    getClientRects() {return this.hidden ? [] : [this.rect];},
    getBoundingClientRect() {return this.rect;}, closest() {return this;}};
  const b = {...a, dataset: {macroEntry: 'calc', macroSurface: options.surface || 'index'}};
  const invalid = {...a, dataset: {macroEntry: 'private?value=secret', macroSurface: 'index'}};
  const document = {visibilityState: options.hidden ? 'hidden' : 'visible', readyState: 'loading',
    querySelectorAll() {return options.empty ? [] : [a, b, invalid];},
    addEventListener(name, fn) {(callbacks[name] ||= []).push(fn);}};
  const window = {innerWidth: 800, innerHeight: 600,
    addEventListener(name, fn) {(windowCallbacks[name] ||= []).push(fn);},
    getComputedStyle() {return {visibility: 'visible', opacity: '1'};},
    gtag(...args) {events.push(args);}};
  let intersection;
  if (!options.noIO) window.IntersectionObserver = class {
    constructor(fn, opts) {intersection = fn; assert.deepEqual([...opts.threshold], [0.5]);}
    observe(el) {observed.push(el);}
    unobserve() {} disconnect() {}
  };
  if (options.noGtag) delete window.gtag;
  vm.runInNewContext(source, {window, document});
  const fire = (name, e = {}) => (callbacks[name] || []).forEach(fn => fn({type: name, ...e}));
  assert.equal(observed.length, 0); // 로더 파싱 중에는 DOM을 탐색하지 않는다.
  fire('DOMContentLoaded');
  return {events, a, b, invalid, document, window, observed, fire,
    intersect(ratio, el = a) {intersection([{target: el, intersectionRatio: ratio, isIntersecting: ratio > 0}]);},
    scroll() {(windowCallbacks.scroll || []).forEach(fn => fn());},
    activate(el = a, extra = {}) {fire(extra.type || 'click', {target: el, button: 0, isTrusted: true,
      defaultPrevented: false, preventDefault() {throw Error('링크 차단 금지');}, ...extra});}};
}
const names = s => s.events.map(e => e[1]);
"""


def _node(body: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node가 없어 DOM 계측 검증 생략")
    result = subprocess.run([node, "-e", _NODE_DOM + body], input=json.dumps(MACRO_ENTRY_JS),
                            text=True, capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr


def test_scroll_exposure_and_first_open_only_once_per_document():
    _node(r"""
const s = boot();
assert.equal(s.events.length, 0);
assert.equal(s.observed.length, 2); // 허용목록 밖 표식 제외
s.intersect(0); s.intersect(0.49); assert.equal(s.events.length, 0);
s.intersect(0.5); s.intersect(1); s.intersect(0); s.intersect(1, s.b);
s.activate(); s.activate(s.b); s.fire('visibilitychange');
assert.deepEqual(names(s), ['macro_entry_view', 'macro_entry_open']);
for (const e of s.events) assert.deepEqual(JSON.parse(JSON.stringify(e)),
  ['event', e[1], {surface: 'index', entry: 'macro', metric_version: '2'}]);
""")


@pytest.mark.parametrize("activation", [
    "{detail: 0}",                          # 키보드 Enter의 click
    "{ctrlKey: true}",                     # 새 탭
    "{metaKey: true}",                     # macOS 새 탭
    "{type: 'auxclick', button: 1}",        # 중간 버튼
])
def test_actual_activation_emits_view_before_open(activation):
    _node(f"""
const s = boot({{surface: 'daily'}});
s.activate(s.a, {activation});
assert.deepEqual(names(s), ['macro_entry_view', 'macro_entry_open']);
assert.equal(s.events[0][2].surface, 'daily');
""")


def test_hidden_tab_synthetic_cancelled_and_secondary_click_do_not_count():
    _node(r"""
const s = boot({hidden: true});
s.intersect(1); s.activate(); assert.equal(s.events.length, 0);
s.document.visibilityState = 'visible'; s.fire('visibilitychange');
assert.equal(s.events.length, 0); // 복귀만으론 노출이 아니며 다음 교차 관측이 필요
s.activate(s.a, {isTrusted: false}); s.activate(s.a, {defaultPrevented: true});
s.activate(s.a, {button: 2}); s.activate(s.a, {type: 'auxclick', button: 2});
s.activate(s.invalid); assert.equal(s.events.length, 0);
s.intersect(1); s.activate();
assert.deepEqual(names(s), ['macro_entry_view', 'macro_entry_open']);
""")


def test_missing_or_throwing_gtag_keeps_links_working_and_does_not_invent_view():
    _node(r"""
const s = boot({noGtag: true}); s.intersect(1); s.activate();
assert.equal(s.events.length, 0);
s.window.gtag = () => {throw Error('blocked');}; s.intersect(1); s.activate();
assert.equal(s.events.length, 0);
s.window.gtag = (...e) => s.events.push(e); s.activate();
assert.deepEqual(names(s), ['macro_entry_view', 'macro_entry_open']);
""")


def test_viewport_fallback_requires_actual_half_visible_area():
    _node(r"""
const s = boot({noIO: true}); assert.equal(s.events.length, 0);
s.a.rect.top = 551; s.a.rect.bottom = 651; s.scroll(); assert.equal(s.events.length, 0);
s.a.rect.top = 550; s.a.rect.bottom = 650; s.scroll(); s.scroll(); s.activate();
assert.deepEqual(names(s), ['macro_entry_view', 'macro_entry_open']);
const hidden = boot({noIO: true, hidden: true});
hidden.a.rect.top = 0; hidden.a.rect.bottom = 100; hidden.scroll(); assert.equal(hidden.events.length, 0);
hidden.document.visibilityState = 'visible'; hidden.fire('visibilitychange');
assert.deepEqual(names(hidden), ['macro_entry_view']);
""")


def test_empty_pages_and_modified_private_attributes_do_not_emit():
    _node(r"""
const empty = boot({empty: true}); empty.activate(); assert.equal(empty.events.length, 0);
const s = boot(); s.a.hidden = true; s.intersect(1); assert.equal(s.events.length, 0);
s.a.hidden = false; s.a.dataset.macroSurface = 'private-secret'; s.intersect(1); s.activate();
assert.equal(s.events.length, 0);
""")


def _context():
    snap = json.loads((Path(__file__).parent / "fixtures/macro/snapshot_2026-09-07.json").read_text())
    return build_macro_context(snap, "2026-09-07")


def test_daily_strip_and_ga_loader_wiring_and_disabled_mode(monkeypatch):
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTENTRY")
    ctx = _context()
    assert 'data-macro-entry="macro" data-macro-surface="daily"' in ctx["site_html"]
    assert "macro_click" in ctx["site_html"] and "code:'page'" in ctx["site_html"]
    assert "data-macro-entry" not in ctx["tistory_html"]
    snip = build_site.ga4_snippet()
    assert snip.index("gtag('config'") < snip.index('id="macro-entry-v2"')
    assert "DOMContentLoaded" in snip
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "")
    strip = render_macro_strip_site(ctx)
    assert build_site.ga4_snippet() == ""
    assert "data-macro-entry" not in strip and "macro_click" not in strip
    assert macro_entry_attributes("index", "macro") == ""
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTENTRY")
    assert macro_entry_attributes("private", "macro") == ""
    assert macro_entry_attributes("index", "private") == ""


@pytest.mark.parametrize("enabled", [True, False])
def test_index_build_marks_existing_macro_card_without_new_ui(tmp_path, monkeypatch, enabled):
    src, site = tmp_path / "src", tmp_path / "site"
    src.mkdir()
    (src / "dataset.json").write_text(json.dumps({"complexes": [], "data_asof": "2026-09-07"}))
    monkeypatch.setattr(build_site, "SRC", str(src))
    monkeypatch.setattr(build_site, "SITE", str(site))
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTENTRY" if enabled else "")
    build_site.build(today="2026-09-07", molit_path=str(tmp_path / "absent.json"))
    page = (site / "index.html").read_text()
    assert '<h3>📈 거시 지표</h3>' in page
    assert ('data-macro-entry="macro" data-macro-surface="index"' in page) is enabled
    assert ('id="macro-entry-v2"' in page) is enabled
    assert ("code:'page'" in page) is enabled
