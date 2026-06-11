"""전역 설정 — 경로, 캐시 위치, mount guard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 데이터 루트 — RE_DATA_ROOT env 로 override 가능 (범용화 2026-06-11: 타 머신은 클론 디렉토리 등
# 자유 지정). 기본값은 운영 머신의 EXT_SSD (APFS 사고 재발 방지 mount guard, ~/.claude/CLAUDE.md 2026-04-21).
EXT_ROOT = Path(os.environ.get("RE_DATA_ROOT", "/Volumes/EXT_SSD/bot/agent_realestate"))
DATA_DIR = EXT_ROOT / "data"
CACHE_DB = DATA_DIR / "realestate_cache.sqlite"
REPORT_DIR = Path.cwd() / "report"   # 리포트 출력 — 작업 디렉토리 하위 report/ (전역 규칙 통일, ~/.claude/CLAUDE.md 필수작업규칙 #6, 2026-05-30). 데이터·캐시는 APFS 가드대로 EXT_ROOT 유지.

# 금융 기본 가정 (요청 시 override). 정책 수치는 절대 하드코딩 단언 금지 — 리포트의
# 정책 섹션은 PolicySnapshot(출처 URL + 확인일)로만 채운다 (RDU-059). 아래는 *계산용
# 기본 파라미터*일 뿐 정책 사실이 아니다.
DEFAULT_MORTGAGE_RATE = 0.043      # 연 이자율 가정 (사용자 입력으로 override)
DEFAULT_MORTGAGE_TERM_YEARS = 40
DEFAULT_DSR_LIMIT = 0.40
OPPORTUNITY_RATE = 0.035           # 자기자본 기회비용 (예금금리 가정)
DEFAULT_BROKER_FEE_RATE = 0.004    # 중개수수료율 가정

# 이메일 자동 발송 — 수신 주소는 .env 의 RE_EMAIL_TO (개인정보 비공개·범용화, 2026-06-11).
EMAIL_TO = os.environ.get("RE_EMAIL_TO", "")
EMAIL_SUBJECT = "부동산분석 리포트"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    pwd: str

    @property
    def ready(self) -> bool:
        return bool(self.user and self.pwd)


def smtp_config() -> SmtpConfig:
    """발신 SMTP 설정 (.env). Gmail 기본 — SMTP_USER=보내는주소, SMTP_PASS=앱 비밀번호."""
    return SmtpConfig(
        host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=os.environ.get("SMTP_USER", ""),
        pwd=os.environ.get("SMTP_PASS", ""),
    )


def assert_mount() -> None:
    """데이터 루트가 없으면 즉시 중단 (기본: EXT_SSD mount guard / RE_DATA_ROOT 지정 시 그 경로)."""
    if not EXT_ROOT.exists():
        raise SystemExit(f"데이터 루트 미존재({EXT_ROOT}) — agent_realestate 중단 (mount guard)")


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_report_dir() -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return REPORT_DIR


def load_env_file() -> None:
    """agent_realestate/.env (chmod 600, gitignored) 의 KEY=VALUE 를 os.environ 에 주입.
    시크릿(MOLIT_API_KEY 등)이 코드·로그·대화에 노출되지 않도록 파일에서만 읽는다.
    외부 의존성 없이 단순 파싱 (이미 설정된 env 는 덮어쓰지 않음)."""
    envp = EXT_ROOT / ".env"
    if not envp.exists():
        return
    for line in envp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
