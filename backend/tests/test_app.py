import pytest

from app.core.config import Settings
from app.main import create_app
from app.workers.settings import WorkerSettings, ping


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    settings = Settings()
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_create_app_uses_given_settings() -> None:
    settings = Settings(cors_origins=["http://example.test"])
    app = create_app(settings)
    assert app.state.settings is settings


async def test_worker_ping_task_registered() -> None:
    assert ping in WorkerSettings.functions
    assert await ping({}) == "pong"
