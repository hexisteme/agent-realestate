"""`.env` 단일 키 조회가 다른 값을 프로세스 환경에 주입하지 않는지 확인한다."""
import os

from agent_realestate import config


def test_read_env_value_reads_one_key_without_mutating_environ(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# 주석\nOTHER_SECRET=synthetic\nTISTORY_KAKAO_ACCOUNT='owner@example.invalid'\n",
        encoding="utf-8")
    monkeypatch.setattr(config, "EXT_ROOT", tmp_path)
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    monkeypatch.delenv("TISTORY_KAKAO_ACCOUNT", raising=False)

    assert config.read_env_value("TISTORY_KAKAO_ACCOUNT") == "owner@example.invalid"
    assert config.read_env_value("MISSING_KEY") is None
    assert "OTHER_SECRET" not in os.environ
    assert "TISTORY_KAKAO_ACCOUNT" not in os.environ


def test_read_env_value_without_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXT_ROOT", tmp_path)
    assert config.read_env_value("TISTORY_KAKAO_ACCOUNT") is None


def test_load_env_file_keeps_existing_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("A_KEY=file\nB_KEY=\"quoted\"\n", encoding="utf-8")
    monkeypatch.setattr(config, "EXT_ROOT", tmp_path)
    monkeypatch.setenv("A_KEY", "env")
    monkeypatch.delenv("B_KEY", raising=False)

    config.load_env_file()
    assert os.environ["A_KEY"] == "env"
    assert os.environ["B_KEY"] == "quoted"
    monkeypatch.delenv("B_KEY")
