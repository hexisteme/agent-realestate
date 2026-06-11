"""리포트 이메일 자동 발송 (핵심포인트 본문 + HTML 첨부). stdlib(smtplib/email) 만.
SMTP 비밀번호는 .env(SMTP_PASS) — 에러/로그에 마스킹."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from ..analysts.compset import assign_sg, lookup_band
from ..config import SmtpConfig
from ..domain import ExitStrategy

EOK = 100_000_000


def compose_summary(evaluated, strategy: ExitStrategy, today, compset: dict | None = None) -> str:
    """Evaluated 리스트(정렬 전 가능) → 핵심포인트 plaintext.

    2026-06-03(사용자 OVERRIDE): 1순위 근거 = §5 10축 가중점수(주 비교 프레임, 하드필터 통과 게이트 후
    조정점수 desc). 생활권 base-rate 는 거시 보조로 병기. 점수는 미래 CAGR 점예측 아님(정직 각주)."""
    def _sg(e):
        return e.candidate.saenghwalgwon or assign_sg(compset, e.candidate.listing.complex_name)
    def _hardfail(e):
        from ..analysts.risk import has_hard_fail
        return has_hard_fail(e.flags) or e.candidate.redev_stage.blocks_residence
    # ★지표 단일화 잔존분(3차 감사 B): adjusted_total(가격포함, 폐기)이 아니라 HTML §A 와 동일한
    #   adjusted_fundamental(호가무관) — 이메일 우선검토 순서가 리포트 §A 와 달랐다.
    ev = sorted(evaluated, key=lambda e: (not _hardfail(e), e.adjusted_fundamental), reverse=True)
    L = [f"[agent_realestate] 부동산분석 리포트 핵심포인트",
         f"생성일 {today} · 전략 {strategy.value}", ""]
    L.append("■ 우선검토 순서(하드필터 통과 게이트 → 조정점수[호가무관]): " + " > ".join(
        f"{e.candidate.listing.complex_name}({e.adjusted_fundamental:.3f}){'⚠️' if e.flags else ''}" for e in ev))
    if ev:
        t = ev[0]
        l = t.candidate.listing
        tb = lookup_band(compset, _sg(t), l.area_exclusive_m2)
        br = (f"생활권 {tb.saenghwalgwon} base-rate median +{tb.cagr_median*100:.1f}%/년(거시 보조)"
              if tb else "생활권 base-rate 미주입 — 거시 보조 없음")
        L += ["", f"■ 우선검토 1: {l.complex_name} (전용{l.area_exclusive_m2:.0f}㎡ {l.price_krw/EOK:.2f}억)",
              f"   입지: {t.candidate.transit}",
              f"   근거: 조정점수(호가무관) {t.adjusted_fundamental:.3f}(주 프레임·같은 급지 내 적합도 종합) · {br} [점수는 미래 CAGR 점예측 아님]",
              f"   재건축: {t.redev.stage_label}·용적률 {t.redev.far_pct:.0f}%·대지지분 {t.redev.land_share_pyeong:.1f}평"]
        if t.trend:
            L.append(f"   실거래(MOLIT): {t.trend.note}")
        if t.finance:
            L.append(f"   자금: 대출 {t.finance.loan_krw/EOK:.2f}억({t.finance.loan_binding}) · "
                     f"자기자본 필요 {t.finance.equity_required_krw/EOK:.2f}억 · "
                     + ("진입가능" if t.finance.equity_ok else "자기자본 부족"))
    flagged = [e for e in ev if e.flags]
    if flagged:
        L.append("")
        L.append("■ Risk Flag: " + " / ".join(
            f"{e.candidate.listing.complex_name}({','.join(f.code for f in e.flags)})" for e in flagged))
    L += ["", "■ 한계: 호가·단지메타는 수동/캐시 입력으로 코어가 진위를 검증하지 않음 — "
          "매수 전 네이버부동산·등기부·국세청 독립확인 필수. 상세는 첨부 HTML §0~§11.",
          "(자동 발송 — agent_realestate)"]
    return "\n".join(L)


def send_report(html_path: str, summary: str, to: str, subject: str, cfg: SmtpConfig) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(summary)
    p = Path(html_path)
    msg.add_attachment(p.read_bytes(), maintype="text", subtype="html", filename=p.name)
    try:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as s:
            s.starttls()
            s.login(cfg.user, cfg.pwd)
            s.send_message(msg)
    except Exception as e:
        raise SystemExit(f"이메일 전송 실패: {str(e).replace(cfg.pwd, '***') if cfg.pwd else str(e)}")
