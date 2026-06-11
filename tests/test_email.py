"""이메일 자동 발송 — 요약 본문 + SMTP 모킹(네트워크 없음)."""
from datetime import date

from agent_realestate.analysts.finance import build_finance_plan
from agent_realestate.analysts.redev import score_redev
from agent_realestate.analysts.scoring import score_candidates
from agent_realestate.analysts.trend import compute_trend
from agent_realestate.config import SmtpConfig
from agent_realestate.domain import (Candidate, DataSource, ExitStrategy, Listing,
                                     PriceKind, RedevStage)
from agent_realestate.notify import email_report
from agent_realestate.notify.email_report import compose_summary, send_report
from agent_realestate.synthesis.assembler import Evaluated


def _evaluated():
    l = Listing(complex_name="상계주공3", dong_ho="324동 506호", area_exclusive_m2=59, floor="5/15층",
                facing="동향", price_krw=830_000_000, price_kind=PriceKind.ASKING_LIVE,
                agent_name="노원공인", confirmed_date=date(2026, 5, 27), source=DataSource.NAVER_LIVE_CHROME)
    c = Candidate(listing=l, units=2213, built_year=1987, far_pct=180, land_share_pyeong=13,
                  land_share_is_estimate=True, redev_stage=RedevStage.SAFETY_PASS, jeonse_krw=400_000_000,
                  transit="노원역 7호선·GTX-C", district="노원구")
    r = score_redev(c)
    a = score_candidates([c], [r], ExitStrategy.HOLD_AND_RENT)[0]
    fin = build_finance_plan(price_krw=830_000_000, ltv_ratio=0.70, annual_income_krw=100_000_000,
                             own_capital_krw=420_000_000, rate=0.043, term_years=40, first_time=True,
                             area_exclusive_m2=59)
    tr = compute_trend([{"deal_ym": "2025-02", "price_krw": 8e8}, {"deal_ym": "2025-08", "price_krw": 8.1e8},
                        {"deal_ym": "2026-05", "price_krw": 8.3e8}])
    return [Evaluated(candidate=c, finance=fin, redev=r, axis=a, hold=None, break_even=None,
                      flags=(), adjusted_total=a.weighted_total, trend=tr)]


def test_compose_summary_has_keypoints():
    s = compose_summary(_evaluated(), ExitStrategy.HOLD_AND_RENT, date(2026, 5, 29))
    assert "핵심포인트" in s
    # 2026-06-03(사용자 OVERRIDE): 1순위 근거 = §5 10축 가중점수(주 프레임). base-rate 는 거시 보조.
    assert "우선검토" in s and "상계주공3" in s
    assert "조정점수(호가무관)" in s     # 1순위 근거 = 실순위 지표(3차 감사 B: adjusted_total 폐기)
    assert "점예측 아님" in s            # 정직 각주(점수는 미래 CAGR 예측 아님)
    assert "한계" in s and "독립확인" in s


class _FakeSMTP:
    last = None
    def __init__(self, host, port, timeout=0):
        self.host, self.port = host, port; self.ops = []
        _FakeSMTP.last = self
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): self.ops.append("tls")
    def login(self, u, p): self.ops.append(("login", u, p))
    def send_message(self, m): self.sent = m


def test_send_report_mocked(tmp_path, monkeypatch):
    html = tmp_path / "r.html"; html.write_text("<html>report</html>", encoding="utf-8")
    monkeypatch.setattr(email_report.smtplib, "SMTP", _FakeSMTP)
    cfg = SmtpConfig(host="smtp.gmail.com", port=587, user="me@gmail.com", pwd="apppass")
    send_report(str(html), "핵심포인트 요약", "floker@naver.com", "부동산분석 리포트", cfg)
    f = _FakeSMTP.last
    assert ("login", "me@gmail.com", "apppass") in f.ops
    assert f.sent["To"] == "floker@naver.com"
    assert f.sent["Subject"] == "부동산분석 리포트"
    # HTML 첨부 존재
    atts = [p for p in f.sent.iter_attachments()]
    assert len(atts) == 1 and atts[0].get_filename() == "r.html"
