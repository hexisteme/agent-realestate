"""tier_now 파생 3종(분포 IQR·추세·52주위치)의 결정론 단위테스트 — A모델 무점수 사실값.

검증: 알려진 가격 배열 → P25/P75/추세방향·%/52주위치 기대값 + 표본부족 가드(null).
대상: blog.build_explorer._pctile / _month_windows / derive_tier_now.
"""
from blog.build_explorer import _pctile, _month_windows, derive_tier_now
from blog.tistory_draft import build_tistory_section, build_daily_body


def test_tistory_interleaved_colspan_and_daily_body():
    """단지 상세가 요약 tr 바로 아래 colspan=4 행으로 인터리브되고, 탐색기 링크 텍스트≠URL,
    일일 본문에 '데이터 생성 과정' 라벨이 포함되는지 — paste 생존 구조 회귀방지."""
    rows = [dict(name="샘플단지", saeng="노원구 상계동", area_m2=79.0, pyeong=24,
                 units_band="2600세대", decade="1980년대", product_type="아파트",
                 molit_recent_eok=8.5, molit_n=12, pyeong_price_man=3541,
                 subway_m=350, gu_jeonse_ratio_pct=61, complex_no=12345)]
    sec = build_tistory_section("노원구", rows)
    assert '<td colspan="4"' in sec                     # (a) colspan 상세행 존재
    assert sec.rstrip().endswith("</table>")            # 표 뒤 별도 상세블록 없음
    body = build_daily_body([sec], "2026-07-09", "2026-07-08")
    assert "explorer.html</a>" not in body              # (b) 링크 텍스트가 bare URL 아님
    assert "데이터 생성 과정" in body                    # (c) 생성 과정 라벨


def _rec(eok: float, ym: str = "202605") -> dict:
    """억 단위 가격 → RTMS 레코드(price 원, ym)."""
    return {"price": int(eok * 1e8), "ym": ym, "apt": "테스트", "area": 84.0}


def test_pctile_linear_interp():
    xs = [1, 2, 3, 4, 5]
    assert _pctile(xs, 0.25) == 2.0
    assert _pctile(xs, 0.5) == 3.0
    assert _pctile(xs, 0.75) == 4.0
    assert _pctile([7.0], 0.25) == 7.0          # 단일 표본


def test_month_windows():
    recent, prior = _month_windows("2026-06-30")
    assert recent == {"202605", "202604", "202603"}            # 직전 3개 완결월
    assert prior == {"202602", "202601", "202512", "202511", "202510",
                     "202509", "202508", "202507", "202506"}   # 그 이전 9개월
    assert len(recent & prior) == 0


def test_distribution_and_position():
    # 5표본 [8.0,8.5,9.0,9.5,10.0]억 — 이상치컷 없음. 전부 recent(202605) 라 추세는 null(prior<2).
    recs = [_rec(p, "202605") for p in (8.0, 8.5, 9.0, 9.5, 10.0)]
    out = derive_tier_now(recs, "2026-06-30")
    assert out["p25_eok"] == 8.5 and out["p75_eok"] == 9.5      # 선형보간 분위수
    assert out["pos_52w"] == 50                                 # 최근중위9.0 in [8,10] → 50%
    assert out["trend_pct"] is None and out["trend_dir"] is None


def test_position_uses_recent_median():
    # 52주위치 '현재'=최근3개월 중위(헤드라인 12개월 중위 아님). 최근중위가 레인지 상단이면 ~고점 신호로 산다.
    # 직전9개월 [8.0,10.0,9.0,9.0] → 12개월 레인지 [8,10], 12개월중위 9.4 / 최근3개월 [9.8,9.8] → 최근중위 9.8.
    recs = ([_rec(8.0, "202506"), _rec(10.0, "202507"), _rec(9.0, "202508"), _rec(9.0, "202509")]
            + [_rec(9.8, "202604"), _rec(9.8, "202605")])
    out = derive_tier_now(recs, "2026-06-30")
    assert out["pos_52w"] == 90        # (9.8−8.0)/(10.0−8.0)×100=90 (12개월중위 9.4 기준이면 70 — 최근중위로 산정 확인)
    assert out["trend_dir"] == "▲"


def test_trend_direction_up():
    # 직전9개월 8.0억 ×3 vs 최근3개월 9.2억 ×3 → +15.0% ▲
    recs = ([_rec(8.0, ym) for ym in ("202506", "202507", "202508")]
            + [_rec(9.2, ym) for ym in ("202603", "202604", "202605")])
    out = derive_tier_now(recs, "2026-06-30")
    assert out["trend_dir"] == "▲" and out["trend_pct"] == 15.0
    assert out["p25_eok"] == 8.0 and out["p75_eok"] == 9.2      # n=6 분포
    assert out["pos_52w"] == 100                                # 최근중위9.2=레인지 상단[8.0,9.2] → 100% (지금 고점근처)


def test_trend_direction_down():
    # 직전 10.0억 vs 최근 9.0억 → -10.0% ▼
    recs = ([_rec(10.0, ym) for ym in ("202506", "202507")]
            + [_rec(9.0, ym) for ym in ("202604", "202605")])
    out = derive_tier_now(recs, "2026-06-30")
    assert out["trend_dir"] == "▼" and out["trend_pct"] == -10.0


def test_sample_guards():
    # ① n<2 → 전부 null
    assert derive_tier_now([_rec(9.0)], "2026-06-30") == {
        "p25_eok": None, "p75_eok": None, "trend_dir": None, "trend_pct": None, "pos_52w": None}
    # ② n=4(<5) → 분포·52주위치 null, 추세는 각 분기 2표본이라 살아있음
    recs4 = ([_rec(8.0, ym) for ym in ("202506", "202507")]
             + [_rec(9.0, ym) for ym in ("202603", "202604")])
    out4 = derive_tier_now(recs4, "2026-06-30")
    assert out4["p25_eok"] is None and out4["p75_eok"] is None and out4["pos_52w"] is None
    assert out4["trend_dir"] == "▲" and out4["trend_pct"] == 12.5
    # ③ 최근3개월 표본<2(최근 1표본) → 추세 null + 52주위치도 null(현재 시세 미정). 분포는 n>=5 라 살아있음
    recs5 = [_rec(p, "202506") for p in (8.0, 8.5, 9.0, 9.5)] + [_rec(10.0, "202605")]
    out5 = derive_tier_now(recs5, "2026-06-30")
    assert out5["trend_dir"] is None and out5["trend_pct"] is None
    assert out5["pos_52w"] is None                              # 최근3개월 1표본 → 위치 미정
    assert out5["p25_eok"] is not None                         # 분포는 n>=5 라 유지


def test_outlier_cut_shared_with_median():
    # 저가 이상치(4.0억)는 m0*0.6 컷으로 제거돼 분포/위치 산정에서 빠진다(molit_median 과 동일 컷).
    # 정상 [9,9,9,9,9]억 + 이상치 4.0억 → m0=9.0, cut=5.4 → 4.0 제거, 남은 5표본 전부 9.0.
    recs = [_rec(9.0, "202605") for _ in range(5)] + [_rec(4.0, "202605")]
    out = derive_tier_now(recs, "2026-06-30")
    assert out["p25_eok"] == 9.0 and out["p75_eok"] == 9.0      # 이상치 제외 후 전부 9.0
    assert out["pos_52w"] is None                               # 남은 표본 레인지 0(max==min) → null
