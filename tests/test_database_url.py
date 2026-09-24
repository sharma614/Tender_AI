import importlib
import sys

import pytest


def test_build_database_url_uses_db_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_USER", "myuser")
    monkeypatch.setenv("DB_PASSWORD", "Strong!Pass@2026")
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "tenderai")

    sys.modules.pop("app.database", None)
    database = importlib.import_module("app.database")
    assert database.build_database_url() == (
        "postgresql://myuser:Strong%21Pass%402026@db.example.com:5432/tenderai"
    )


def test_initialize_database_raises_when_postgres_configured_but_unreachable(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_USER", "myuser")
    monkeypatch.setenv("DB_PASSWORD", "Strong!Pass@2026")
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "tenderai")

    sys.modules.pop("app.database", None)
    database = importlib.import_module("app.database")

    def boom(*args, **kwargs):
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(database, "create_engine", boom)

    with pytest.raises(RuntimeError, match="PostgreSQL configured"):
        database.initialize_database()
