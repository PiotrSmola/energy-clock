import pytest

from config.settings import env


def test_env_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGY_CLOCK_TEST_VAR", "value")

    assert env("ENERGY_CLOCK_TEST_VAR", "default") == "value"


def test_env_falls_back_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENERGY_CLOCK_TEST_VAR", raising=False)

    assert env("ENERGY_CLOCK_TEST_VAR", "default") == "default"


def test_env_treats_empty_value_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGY_CLOCK_TEST_VAR", "")

    assert env("ENERGY_CLOCK_TEST_VAR", "default") == "default"
