"""K10 v2: 거시 진입 링크 노출/첫 활성화를 문서 수명마다 각각 한 번 계측한다.

노출 = 보이는 탭에서 링크 면적 50% 이상 교차, 또는 실제 사용자 활성화.
재노출·다른 링크·뒤로 가기 캐시 복원은 같은 문서라 중복하지 않는다.
전송 값은 surface(index/daily), entry(macro/calc), metric_version('2')뿐이다.
"""
from __future__ import annotations


def macro_click_attribute(code: str) -> str:
    """기존 macro_click 계약을 유지하며 계측 비활성·예외가 링크를 방해하지 않게 한다."""
    from blog.build_site import GA4_MEASUREMENT_ID
    if not GA4_MEASUREMENT_ID:
        return ""
    return ("onclick=\"try{if(typeof gtag==='function')gtag('event','macro_click',"
            f"{{code:'{code}'}})" + "}catch(_){}\"")


def macro_entry_attributes(surface: str, entry: str) -> str:
    """기존 링크에만 표식을 붙인다. GA4 비활성일 때 표식과 onclick 모두 생략한다."""
    from blog.build_site import GA4_MEASUREMENT_ID
    if not GA4_MEASUREMENT_ID or surface not in {"index", "daily"} or entry not in {"macro", "calc"}:
        return ""
    return (f'data-macro-entry="{entry}" data-macro-surface="{surface}" '
            + macro_click_attribute("page" if entry == "macro" else "calc"))


MACRO_ENTRY_JS = r"""
(function () {
  function initMacroEntry() {
    const links = Array.from(document.querySelectorAll('a[data-macro-entry][data-macro-surface]'))
      .filter(a => ['macro', 'calc'].includes(a.dataset.macroEntry) &&
                   ['index', 'daily'].includes(a.dataset.macroSurface));
    if (!links.length) return;
    let viewed = false, opened = false;
    const visibleTab = () => document.visibilityState === 'visible';
    function emit(name, a) {
      if (typeof window.gtag !== 'function') return false;
      const surface = a.dataset.macroSurface, entry = a.dataset.macroEntry;
      if (!['index', 'daily'].includes(surface) || !['macro', 'calc'].includes(entry)) return false;
      try {
        window.gtag('event', name, {surface: surface, entry: entry, metric_version: '2'});
        return true;
      } catch (_) { return false; }
    }
    function show(a) {
      if (!viewed && visibleTab()) viewed = emit('macro_entry_view', a);
      return viewed;
    }
    function displayed(a) {
      const style = window.getComputedStyle(a);
      return a.getClientRects().length > 0 && style.visibility !== 'hidden' &&
        style.visibility !== 'collapse' && style.opacity !== '0';
    }
    function viewportCheck() {
      if (viewed || !visibleTab()) return;
      for (const a of links) {
        if (!displayed(a)) continue;
        const r = a.getBoundingClientRect(), area = r.width * r.height;
        const width = Math.max(0, Math.min(r.right, window.innerWidth) - Math.max(r.left, 0));
        const height = Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
        if (area > 0 && width * height / area >= 0.5 && show(a)) break;
      }
    }
    let observer;
    if (typeof window.IntersectionObserver === 'function') {
      observer = new window.IntersectionObserver(entries => {
        for (const e of entries) {
          if (e.isIntersecting && e.intersectionRatio >= 0.5 && displayed(e.target)) show(e.target);
        }
        if (viewed) observer.disconnect();
      }, {threshold: [0.5]});
      links.forEach(a => observer.observe(a));
    } else {
      // 교차 API 미지원: 실제 viewport 면적 검사. 단순 페이지 로드는 노출이 아니다.
      window.addEventListener('scroll', viewportCheck, {passive: true});
      window.addEventListener('resize', viewportCheck, {passive: true});
      viewportCheck();
    }
    document.addEventListener('visibilitychange', () => {
      if (viewed || !visibleTab()) return;
      if (observer) links.forEach(a => { observer.unobserve(a); observer.observe(a); });
      else viewportCheck();
    });
    function activate(e) {
      if (opened || !e.isTrusted || e.defaultPrevented || !visibleTab()) return;
      if ((e.type === 'click' && e.button !== 0) || (e.type === 'auxclick' && e.button !== 1)) return;
      const a = e.target.closest && e.target.closest('a[data-macro-entry][data-macro-surface]');
      if (!links.includes(a)) return;
      // 키보드·새 탭·중간 클릭도 실제 활성화다. IO 콜백보다 빨라도 분모를 먼저 기록한다.
      if (show(a)) opened = emit('macro_entry_open', a);
    }
    document.addEventListener('click', activate);
    document.addEventListener('auxclick', activate);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initMacroEntry, {once: true});
  else initMacroEntry();
})();
"""


def macro_entry_script() -> str:
    """ga4_snippet의 활성 분기에서 로더 뒤에만 삽입한다."""
    return f'<script id="macro-entry-v2">{MACRO_ENTRY_JS}</script>\n'
