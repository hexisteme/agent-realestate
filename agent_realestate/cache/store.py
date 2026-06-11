"""Static 계층 캐시 (sqlite). cron/Claude-MCP 가 채우고 report 가 읽는다.

테이블:
  policy_snapshot — §2 표시용 정책 사실(자유문 + URL + 확인일, RDU-059)
  policy_param    — finance 가 쓰는 *기계가독* 정책 수치(LTV율 등) + 출처 (①)
  price_series    — MOLIT 실거래 시계열 → §1 시세 추세/기저율 (②)
  land_registry   — 등기부 대지지분 실측값 (③, is_estimate=False 로 승격)
"""

from __future__ import annotations

import sqlite3

from ..config import CACHE_DB, ensure_data_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL, statement TEXT NOT NULL, law_ref TEXT,
    url TEXT NOT NULL, confirmed_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_param (
    key TEXT PRIMARY KEY,           -- ltv_first_regulated, acq_first_relief ...
    value REAL NOT NULL, url TEXT NOT NULL, confirmed_date TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS price_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_name TEXT NOT NULL, area_exclusive_m2 REAL, deal_ym TEXT,
    price_krw INTEGER, source TEXT DEFAULT 'MOLIT_API',
    UNIQUE(complex_name, area_exclusive_m2, deal_ym, price_krw)
);
CREATE TABLE IF NOT EXISTS land_registry (
    complex_name TEXT NOT NULL, area_exclusive_m2 REAL NOT NULL,
    land_share_pyeong REAL NOT NULL, source_url TEXT, confirmed_date TEXT NOT NULL,
    PRIMARY KEY(complex_name, area_exclusive_m2)
);
CREATE TABLE IF NOT EXISTS redev_stage (
    complex_name TEXT NOT NULL, district TEXT, stage TEXT NOT NULL,
    stage_date TEXT, source_url TEXT, confirmed_date TEXT NOT NULL,
    PRIMARY KEY(complex_name, stage)
);
CREATE TABLE IF NOT EXISTS complex_meta (   -- R5: 단지 메타 캐시(세대수·용적률·준공) 재사용
    complex_name TEXT PRIMARY KEY, units INTEGER, far_pct REAL, built_year INTEGER,
    source_url TEXT, confirmed_date TEXT
);
"""


def _meta_match(conn, complex_name):
    cur = conn.execute("SELECT complex_name,units,far_pct,built_year,source_url,confirmed_date"
                       " FROM complex_meta WHERE complex_name=? OR complex_name LIKE ? OR ? LIKE '%'||complex_name||'%' LIMIT 1",
                       (complex_name, f"%{complex_name}%", complex_name))
    return cur.fetchone()


def connect() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(CACHE_DB)
    conn.executescript(_SCHEMA)
    return conn


# ── 정책 (§2 표시) ───────────────────────────────────────────────
def upsert_policy(conn, topic, statement, url, confirmed_date, law_ref="") -> None:
    conn.execute("INSERT INTO policy_snapshot(topic,statement,law_ref,url,confirmed_date)"
                 " VALUES(?,?,?,?,?)", (topic, statement, law_ref, url, confirmed_date))
    conn.commit()


def latest_policies(conn) -> list[dict]:
    cur = conn.execute("SELECT topic,statement,law_ref,url,MAX(confirmed_date) FROM policy_snapshot"
                       " GROUP BY topic")
    return [dict(topic=r[0], statement=r[1], law_ref=r[2], url=r[3], confirmed_date=r[4]) for r in cur]


# ── 정책 파라미터 (① finance 자동주입) ──────────────────────────
def upsert_param(conn, key, value, url, confirmed_date, note="") -> None:
    conn.execute("INSERT OR REPLACE INTO policy_param(key,value,url,confirmed_date,note)"
                 " VALUES(?,?,?,?,?)", (key, float(value), url, confirmed_date, note))
    conn.commit()


def get_param(conn, key) -> dict | None:
    cur = conn.execute("SELECT value,url,confirmed_date,note FROM policy_param WHERE key=?", (key,))
    r = cur.fetchone()
    return None if r is None else dict(value=r[0], url=r[1], confirmed_date=r[2], note=r[3])


# ── 실거래 시계열 (② §1 추세) ───────────────────────────────────
def upsert_price(conn, complex_name, area_exclusive_m2, deal_ym, price_krw, source="MOLIT_API") -> None:
    conn.execute("INSERT OR IGNORE INTO price_series(complex_name,area_exclusive_m2,deal_ym,price_krw,source)"
                 " VALUES(?,?,?,?,?)", (complex_name, float(area_exclusive_m2), deal_ym, int(price_krw), source))
    conn.commit()


def get_price_series(conn, complex_name, area_exclusive_m2, tol=5.0) -> list[dict]:
    # 단지명 부분일치 (MOLIT aptNm '상계주공3단지' ↔ 후보 '상계주공3' 정합, 양방향 contains)
    like = f"%{complex_name}%"
    cur = conn.execute(
        "SELECT deal_ym,price_krw FROM price_series"
        " WHERE (complex_name LIKE ? OR ? LIKE '%'||complex_name||'%')"
        " AND abs(area_exclusive_m2-?)<=? ORDER BY deal_ym",
        (like, complex_name, float(area_exclusive_m2), tol))
    return [dict(deal_ym=r[0], price_krw=r[1]) for r in cur]


# ── 등기부 대지지분 실측 (③) ────────────────────────────────────
def upsert_land(conn, complex_name, area_exclusive_m2, land_share_pyeong, source_url, confirmed_date) -> None:
    conn.execute("INSERT OR REPLACE INTO land_registry(complex_name,area_exclusive_m2,land_share_pyeong,source_url,confirmed_date)"
                 " VALUES(?,?,?,?,?)", (complex_name, float(area_exclusive_m2), float(land_share_pyeong), source_url, confirmed_date))
    conn.commit()


def get_land(conn, complex_name, area_exclusive_m2, tol=3.0) -> dict | None:
    cur = conn.execute(
        "SELECT land_share_pyeong,source_url,confirmed_date FROM land_registry WHERE complex_name=?"
        " AND abs(area_exclusive_m2-?)<=? LIMIT 1", (complex_name, float(area_exclusive_m2), tol))
    r = cur.fetchone()
    return None if r is None else dict(land_share_pyeong=r[0], source_url=r[1], confirmed_date=r[2])


# ── 재건축 단계 캐시 ─────────────────────────────────────────────
def upsert_redev(conn, complex_name, district, stage, stage_date, source_url, confirmed_date) -> None:
    conn.execute("INSERT OR REPLACE INTO redev_stage(complex_name,district,stage,stage_date,source_url,confirmed_date)"
                 " VALUES(?,?,?,?,?,?)", (complex_name, district, stage, stage_date, source_url, confirmed_date))
    conn.commit()


def get_redev(conn, complex_name) -> dict | None:
    cur = conn.execute("SELECT stage,stage_date,source_url,confirmed_date FROM redev_stage"
                       " WHERE complex_name=? OR complex_name LIKE ? ORDER BY confirmed_date DESC LIMIT 1",
                       (complex_name, f"%{complex_name}%"))
    r = cur.fetchone()
    return None if r is None else dict(stage=r[0], stage_date=r[1], source_url=r[2], confirmed_date=r[3])


# ── 단지 메타 캐시 (R5) ──────────────────────────────────────────
def upsert_meta(conn, complex_name, units, far_pct, built_year, source_url="", confirmed_date="") -> None:
    conn.execute("INSERT OR REPLACE INTO complex_meta(complex_name,units,far_pct,built_year,source_url,confirmed_date)"
                 " VALUES(?,?,?,?,?,?)", (complex_name, int(units), float(far_pct), int(built_year), source_url, confirmed_date))
    conn.commit()


def get_meta(conn, complex_name) -> dict | None:
    r = _meta_match(conn, complex_name)
    return None if r is None else dict(complex_name=r[0], units=r[1], far_pct=r[2], built_year=r[3],
                                       source_url=r[4], confirmed_date=r[5])
