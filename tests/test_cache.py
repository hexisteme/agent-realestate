"""캐시 라운드트립 — 정책파라미터(①)·실거래(②)·등기부 대지지분(③)·재건축."""
import agent_realestate.config as cfg
from agent_realestate.cache import store


def _conn(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "CACHE_DB", tmp_path / "t.sqlite")
    return store.connect()


def test_policy_param(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    store.upsert_param(conn, "ltv_first_regulated", 0.70, "https://fsc", "2026-05-27", "생애최초 규제 70%")
    p = store.get_param(conn, "ltv_first_regulated")
    assert p["value"] == 0.70 and p["url"] == "https://fsc"
    assert store.get_param(conn, "ltv_general") is None


def test_price_series_trend_input(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    store.upsert_price(conn, "상계주공3", 59, "2026-01", 800_000_000)
    store.upsert_price(conn, "상계주공3", 59, "2026-05", 830_000_000)
    store.upsert_price(conn, "상계주공3", 59, "2026-05", 830_000_000)  # 중복 무시(UNIQUE)
    s = store.get_price_series(conn, "상계주공3", 60)  # tol 5 → 매칭
    assert len(s) == 2
    # MOLIT aptNm 은 '상계주공3단지' — 후보명 '상계주공3' 으로도 매칭돼야 (부분일치)
    store.upsert_price(conn, "상계주공3단지", 59, "2026-05", 830_000_000, "MOLIT_API")
    s2 = store.get_price_series(conn, "상계주공3", 59)
    assert any(r["price_krw"] == 830_000_000 for r in s2)


def test_land_registry_override(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    store.upsert_land(conn, "상계주공3", 59, 13.4, "iros.go.kr", "2026-05-27")
    m = store.get_land(conn, "상계주공3", 60)   # tol 3 → 매칭
    assert m["land_share_pyeong"] == 13.4
    assert store.get_land(conn, "상계주공3", 84) is None  # tol 밖


def test_redev_cache(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    store.upsert_redev(conn, "상계주공5", "노원구", "MGMT_DISPOSAL", "2026-03", "cleanup.seoul", "2026-05-27")
    assert store.get_redev(conn, "상계주공5")["stage"] == "MGMT_DISPOSAL"
