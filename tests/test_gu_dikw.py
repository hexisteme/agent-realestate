"""구 단위 글 K·W 층(2026-10-22) — 가격을 가르는 축(K)·자기자본별 닿는 단지 수(W)·제목 실험 팔."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from agent_realestate.analysts import finance as fin
from blog import gu_dikw as gd
from blog.wording_guard import FORBIDDEN_WORDS, LEAD_FORBIDDEN_PATTERNS

EOK = 10**8


def _row(name, eok, *, units_band="2백세대급", decade="2000년대", area_band="60-84㎡", pp=4000, gu="송파"):
    return dict(gu=gu, name=name, product_type="아파트", molit_recent_eok=eok, pyeong_price_man=pp,
                units_band=units_band, decade=decade, area_band=area_band, area_m2=84.0, saeng=f"{gu}구 어딘가",
                pyeong=25, molit_n=15, molit_p25_eok=eok and eok * 0.9, molit_p75_eok=eok and eok * 1.1,
                molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None)


def _need(price, income):
    dsr = fin.compute_dsr_loan(income, (gd.RATE_PCT + gd.STRESS_PP) / 100, gd.TERM_YEARS, gd.DSR_LIMIT)
    loan = min(int(price * gd.LTV_REGULATED), fin.resolve_loan_abs_cap(price), dsr)
    return price - loan + fin.compute_acquisition_tax(price, False, 84) + fin.compute_broker_fee(price)


# ── W: 닿는 가격 ────────────────────────────────────────────────────────────

def test_닿는_가격은_필요자기자본이_자기자본_안에_드는_최대_가격이다():
    r = gd.compute_reachable_price(5 * EOK, EOK)
    assert _need(r.price_krw, EOK) <= 5 * EOK < _need(r.price_krw + gd.PRICE_STEP_KRW, EOK)
    assert 7.5 * EOK < r.price_krw < 8.5 * EOK
    assert r.binding == "LTV"


def test_15억_초과_주택은_대출한도가_4억이라_닿는_가격이_15억_언저리에_머문다():
    a = gd.compute_reachable_price(10 * EOK, 150_000_000)
    b = gd.compute_reachable_price(11 * EOK, 150_000_000)
    assert 15 * EOK <= a.price_krw <= b.price_krw < 15.2 * EOK
    plateau = gd.find_cap_plateau(150_000_000)
    assert plateau is not None and plateau[0] <= 10 and plateau[1] >= 11


def test_LTV가_묶으면_연소득을_늘려도_닿는_가격이_같다():
    assert gd.compute_reachable_price(3 * EOK, 70_000_000) == gd.compute_reachable_price(3 * EOK, 150_000_000)


def test_닿는_단지_수는_실거래_중위가_있는_단지만_센다():
    rows = [_row("가", 5.0), _row("나", 8.0), _row("다", None), _row("라", 12.0)]
    assert gd.count_reachable_complexes(rows, 8 * EOK) == 2


# ── K: 가격을 가르는 축 ─────────────────────────────────────────────────────

def _k_rows():
    rows = []
    for i in range(3):
        rows.append(_row(f"대{i}", 20.0, units_band="2천세대급", pp=12000 + i, decade="2000년대"))
        rows.append(_row(f"소{i}", 6.0, units_band="소규모", pp=5000 + i, decade="2000년대"))
        rows.append(_row(f"중{i}", 9.0, units_band="5백세대급", pp=7000 + i, decade="1990년대"))
    rows.append(_row("외", 9.0, units_band="1천세대급", pp=99999, decade="2020년대"))   # n=1 구분은 비교에서 뺀다
    return rows


def test_가르는_축은_n3_이상_구분의_중위_차가_큰_순서다():
    axes = gd.compute_price_split_axes(_k_rows())
    assert axes[0].axis == "세대수대"
    assert [b[0] for b in axes[0].buckets] == ["소규모", "5백세대급", "2천세대급"]   # 자연 순서, n=1 제외
    assert axes[0].spread_man == 12001 - 5001
    assert all(n >= 3 for a in axes for _, _, n in a.buckets)
    assert [a.axis for a in axes] == sorted([a.axis for a in axes], key=lambda x: -next(a.spread_man for a in axes if a.axis == x))


def test_지식_줄은_서열_원인_전망_어휘가_없고_축_이름을_말한다():
    rows = _k_rows()
    lines = gd.build_knowledge_lines("송파", rows, rows)
    text = " ".join(lines)
    assert "세대수대" in text and "원인" in text
    for w in FORBIDDEN_WORDS:
        assert w not in text
    for p in LEAD_FORBIDDEN_PATTERNS:
        assert not re.search(p, text), p


# ── W 줄·제목 실험 ──────────────────────────────────────────────────────────

def _w_rows():
    return [_row(f"단지{i}", eok) for i, eok in enumerate((5.0, 6.5, 7.5, 9.0, 11.0, 14.0, 16.0, 22.0))]


def test_지혜_줄은_가정과_한도를_묶는_것과_레버를_말한다():
    lines = gd.build_wisdom_lines("송파", _w_rows())
    text = " ".join(lines)
    assert "[가정]" in text and "묶는" in text and "실거주" in text
    assert re.search(r"자기자본 1억", text) and re.search(r"연소득", text)
    for p in LEAD_FORBIDDEN_PATTERNS:
        assert not re.search(p, text), p


def _gus():
    import blog.build_explorer as be
    return list(be.GU_LAWD)


def test_제목_팔은_결정론이고_같은_구는_회전_바퀴마다_번갈아든다():
    day = date(2026, 10, 31)
    arms = [gd.assign_title_arm("송파", (day + timedelta(days=14 * k)).isoformat(), _gus()) for k in range(4)]
    assert arms[0] != arms[1] and arms[0] == arms[2] and arms[1] == arms[3]
    assert gd.assign_title_arm("송파", "2026-10-31", _gus()) == gd.assign_title_arm("송파", "2026-11-05", _gus())


def test_제목_팔은_14일마다_25구가_13대12이고_하루_2구는_1대1이다():
    from blog.gu_tistory_post import GU_CYCLE_DAYS, select_rotation_gus
    start = date(2026, 10, 28)
    for c in range(3):
        cycle = []
        for d in range(GU_CYCLE_DAYS):
            day = (start + timedelta(days=c * GU_CYCLE_DAYS + d)).isoformat()
            arms = [gd.assign_title_arm(g, day, _gus()) for g in select_rotation_gus(_gus(), day)]
            assert abs(arms.count("A") - arms.count("B")) <= 1, (day, arms)
            cycle += arms
        assert len(cycle) == 25 and abs(cycle.count("A") - cycle.count("B")) == 1   # Codex R1: 11:14 교대 → 13:12


def test_회전_변경_뒤_첫_글은_직전_주간_회전_글과_팔이_엇갈린다():
    # 10-23(금) 용산~중랑, 10-26(월) 강남~관악, 10-27(화) 광진~도봉은 주 번호 홀짝(옛 배정)으로 나갔다.
    from blog.gu_tistory_post import select_rotation_gus
    gus = sorted(_gus())

    def weekly_arm(gu, day):
        d = date.fromisoformat(day)
        return "B" if ((d.toordinal() - d.weekday()) // 7 + gus.index(gu)) % 2 == 0 else "A"
    last = {g: "2026-10-26" for g in gus[:5]} | {g: "2026-10-27" for g in gus[5:10]} | {g: "2026-10-23" for g in gus[20:]}
    first: dict[str, str] = {}
    for d in range(14):
        day = (date(2026, 10, 28) + timedelta(days=d)).isoformat()
        for g in select_rotation_gus(gus, day):
            first.setdefault(g, day)
    for g, day in last.items():
        assert gd.assign_title_arm(g, first[g], gus) != weekly_arm(g, day), (g, day, first[g])


def test_상황형_제목은_구명_자기자본_단지수를_싣고_금칙어가_없다():
    title = gd.build_situation_title("송파", _w_rows(), "2026-10-23", "2026-10-22")
    assert title.startswith("송파구 아파트")
    m = re.search(r"자기자본 (\d+)억·연소득 1억이면 닿는 단지는\? 대표 평형 중위로 8곳 중 (\d+)곳", title)
    assert m, title
    e, k = int(m.group(1)), int(m.group(2))
    assert k == gd.count_reachable_complexes(_w_rows(), gd.compute_reachable_price(e * EOK, EOK).price_krw)
    assert k * 2 >= 8 and "국토부 2026-10-22" in title
    for w in FORBIDDEN_WORDS:
        assert w not in title


# ── 그림 ────────────────────────────────────────────────────────────────────

def test_그림_두_장은_결정론_PNG이고_이름이_본문이미지_계약을_통과한다(tmp_path):
    from blog.body_media import _NAME
    rows = _k_rows() + _w_rows()
    first = gd.write_dikw_images("송파", rows, rows, "2026-10-23", tmp_path)
    second = gd.write_dikw_images("송파", rows, rows, "2026-10-23", tmp_path)
    assert [i.sha256 for i in first] == [i.sha256 for i in second] and len(first) == 2
    for img in first:
        assert _NAME.fullmatch(img.name), img.name
        payload = (tmp_path / img.name).read_bytes()
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        assert img.width == gd.CHART_WIDTH and img.height > 300


def test_꺼져_있으면_동작하지_않는다(monkeypatch):
    monkeypatch.delenv("RE_GU_DIKW", raising=False)
    assert gd.enabled() is False
    monkeypatch.setenv("RE_GU_DIKW", "1")
    assert gd.enabled() is True



def _arm_records(clicks_of, gap=14):
    """바퀴 첫날 11-06 부터 두 바퀴 — 구마다 gap 일 떨어진 두 글."""
    from blog.gu_tistory_post import select_rotation_gus
    start = date(2026, 11, 6)
    out = []
    for i in range(2):
        for d in range(14):
            day = (start + timedelta(days=i * gap + d)).isoformat()
            for g in select_rotation_gus(_gus(), day):
                arm = gd.assign_title_arm(g, day, _gus())
                out.append(dict(gu=g, day=day, arm=arm, impressions=100, clicks=clicks_of(i, arm)))
    return out


def test_제목_판정은_발행주_추세만_있으면_B를_고르지_않는다():
    # Codex R2 반례: 제목 효과 없이 첫 주 2클릭·다음 주 1클릭 → 구별 승패 과반·CTR 합계로는 B 가 이겼다
    v = gd.judge_title_arms(_arm_records(lambda i, arm: 2 if i == 0 else 1))
    assert v.verdict == "A" and v.click_effect == pytest.approx(0) and v.ctr_effect == pytest.approx(0)
    assert sorted((v.n_b_first, v.n_a_first)) == [12, 13]


def test_제목_판정은_발행주_추세를_빼고_남은_B_효과를_잡는다():
    v = gd.judge_title_arms(_arm_records(lambda i, arm: (3 if i == 0 else 1) + (arm == "B")))
    assert v.verdict == "B" and v.click_effect == pytest.approx(1) and v.ctr_effect == pytest.approx(0.01)


def test_제목_판정은_한쪽_순서만_있거나_이웃_바퀴가_아니면_판정하지_않는다():
    first_b = [r for r in _arm_records(lambda i, arm: 1)
               if gd.assign_title_arm(r["gu"], "2026-11-06", _gus()) == "B"]
    assert gd.judge_title_arms(first_b).verdict == "판정 불가"
    apart = _arm_records(lambda i, arm: 1, gap=28)   # 두 바퀴 떨어진 같은 팔 두 편 — 짝 없음
    assert gd.judge_title_arms(apart).verdict == "판정 불가"


# ── 구 원고·사이트 연결 ─────────────────────────────────────────────────────

def _dikw_ds():
    rows = []
    import blog.build_explorer as be
    for gu in be.GU_LAWD:                                     # 25구 — 회전이 하루 2구를 고른다
        rows += [dict(r, gu=gu, name=f"{gu}{r['name']}") for r in _k_rows() + _w_rows()]
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-10-22", "generated": "2026-10-23"}


def test_켜면_구_원고에_W_K_그림과_계약이_붙고_B팔만_제목이_바뀐다(tmp_path, monkeypatch):
    import blog.build_explorer as be
    from blog.body_media import read_body_media
    from blog.gu_tistory_post import write_gu_posts
    from blog.tistory_contract import parse_helper
    monkeypatch.setenv("RE_GU_TISTORY_POSTS", "1")
    monkeypatch.setenv("RE_GU_DIKW", "1")
    paths = write_gu_posts(_dikw_ds(), "2026-10-20", "2026-10-19", str(tmp_path))
    assert paths
    arms = set()
    for p in paths:
        data = parse_helper(p)
        gu = p.rsplit("-gu-", 1)[1].split("-tistory-draft")[0]
        images = read_body_media(p, data)
        assert [i.name.split("-")[1] for i in images] == ["reach", "axis"]
        body = data["body"]
        assert body.index("어디까지 닿나") < body.index("가르는 축") < body.index("<table")
        arm = gd.assign_title_arm(gu, "2026-10-20", list(be.GU_LAWD))
        arms.add(arm)
        assert ("닿는 단지는?" in data["title"]) == (arm == "B")
        assert len(body.encode("utf-8")) + 2 * 1000 <= 28000
    assert arms == {"A", "B"}


def test_끄면_그림도_계약도_없다(tmp_path, monkeypatch):
    from blog.body_media import body_media_path
    from blog.gu_tistory_post import write_gu_posts
    monkeypatch.setenv("RE_GU_TISTORY_POSTS", "1")
    monkeypatch.delenv("RE_GU_DIKW", raising=False)
    for p in write_gu_posts(_dikw_ds(), "2026-10-20", "2026-10-19", str(tmp_path)):
        assert "<img" not in open(p, encoding="utf-8").read() and not body_media_path(p).exists()


def test_사이트_빌드는_구_원고_그림도_검증해_복사한다(tmp_path, monkeypatch):
    import blog.build_site as bs
    from blog.gu_tistory_post import write_gu_posts
    monkeypatch.setenv("RE_GU_TISTORY_POSTS", "1")
    monkeypatch.setenv("RE_GU_DIKW", "1")
    write_gu_posts(_dikw_ds(), "2026-10-20", "2026-10-19", str(tmp_path))
    monkeypatch.setattr(bs, "SRC", str(tmp_path))
    monkeypatch.setattr(bs, "SITE", str(tmp_path / "site"))
    copied = bs._daily_body_media("2026-10-20")
    assert len(copied) == 4 and all((tmp_path / "site" / c).is_file() for c in copied)
    assert bs._daily_body_media("2026-10-19") == []          # 미래 날짜 원고는 복사하지 않는다


@pytest.mark.parametrize("prices,subject", [((3.0, 4.0, 4.5, 30.0), "LTV가"), ((3.0, 4.0, 30.0, 31.0, 32.0, 33.0), "절대한도가")])
def test_한도_이름에_맞는_조사를_붙인다(prices, subject):
    rows = [_row(f"단지{i}", eok) for i, eok in enumerate(prices)]
    text = " ".join(gd.build_wisdom_lines("송파", rows))
    assert f"({subject} 먼저 묶는다)" in text, text


def test_닿는_가격과_같은_중위는_닿는다_부동소수_오차():
    # Codex R1: 9.47 * 1e8 == 947000000.0000001 — 상한과 같은 단지를 빠뜨렸다
    price = gd.compute_reachable_price(650_000_000, 70_000_000).price_krw
    assert price == 947_000_000
    assert gd.count_reachable_complexes([{"molit_recent_eok": 9.47}], price) == 1


def test_필요자기자본은_가격_격자에서_줄지_않는다():
    # 1원 단위로는 중개보수 요율 경계(5천만·2억)에서 줄 수 있다(Codex R1 P3) — 이분 탐색은 격자 위에서만 돈다
    for inc in gd.CURVE_INCOMES_KRW + (gd.TITLE_INCOME_KRW + 10_000_000,):
        prev = -1
        for k in range(0, gd._PRICE_MAX_STEPS + 1):
            cur = gd._required_equity(k * gd.PRICE_STEP_KRW, inc)
            assert cur >= prev, (inc, k)
            prev = cur


def test_15억_문턱에서_멈춘_것을_DSR_탓으로_쓰지_않는다():
    # Codex R1: 자기자본 11억·연소득 1억 → 1.1억이면 대출은 늘지만 가격은 15억 그대로 — 묶는 것이 아니라 문턱이다
    line = gd.build_lever_line(_w_rows(), 11)
    assert "DSR이 먼저" not in line and "15억" in line and "문턱" in line


def test_W_줄은_대표_평형_기준과_입주_유예_특례를_밝힌다():
    text = " ".join(gd.build_wisdom_lines("송파", _w_rows()))
    assert "대표 평형" in text and "입주" in text and "유예" in text
    assert "일반 주택 신청 기한 2027-12-31" in text and "등록임대" in text      # Codex R2: 등록 매입임대는 15개월 별도 기한
