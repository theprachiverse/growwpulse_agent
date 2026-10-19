"""Unit tests for configuration loading and validation (Task 0.2)."""

import pytest
from pydantic import ValidationError

from pulse.config import (
    AppConfig,
    ComposeConfig,
    IngestConfig,
    Settings,
    ThemingConfig,
)


def test_default_config_loads_valid() -> None:
    config = AppConfig()
    assert config.ingest.window_weeks == 10
    assert config.theming.max_themes == 5
    assert config.compose.max_words == 250
    assert config.sources.play_store.enabled is True
    assert config.sources.app_store.enabled is True


def test_window_weeks_validation() -> None:
    # Allowed: 8..12
    assert IngestConfig(window_weeks=8).window_weeks == 8
    assert IngestConfig(window_weeks=12).window_weeks == 12

    with pytest.raises(ValidationError):
        IngestConfig(window_weeks=7)

    with pytest.raises(ValidationError):
        IngestConfig(window_weeks=13)


def test_max_themes_validation() -> None:
    # Allowed: <= 5
    assert ThemingConfig(max_themes=3).max_themes == 3
    assert ThemingConfig(max_themes=5).max_themes == 5

    with pytest.raises(ValidationError):
        ThemingConfig(max_themes=6)


def test_max_words_validation() -> None:
    # Allowed: <= 250
    assert ComposeConfig(max_words=200).max_words == 200
    assert ComposeConfig(max_words=250).max_words == 250

    with pytest.raises(ValidationError):
        ComposeConfig(max_words=251)


def test_quote_words_validation() -> None:
    assert ComposeConfig(quote_words=[8, 40]).quote_words == [8, 40]

    with pytest.raises(ValidationError):
        ComposeConfig(quote_words=[40, 8])  # min >= max

    with pytest.raises(ValidationError):
        ComposeConfig(quote_words=[10])  # len != 2


def test_settings_masked_dict() -> None:
    settings = Settings(
        LLM_PROVIDER="groq",
        LLM_API_KEY="gsk_1234567890abcdef1234",
        PULSE_RECIPIENT="lead@groww.in",
    )
    masked = settings.masked_dict()
    assert masked["llm_provider"] == "groq"
    assert masked["pulse_recipient"] == "lead@groww.in"
    # Ensure api key is masked
    assert "gsk_...1234" in masked["llm_api_key"]
    assert "1234567890abcdef" not in masked["llm_api_key"]


def test_target_words_defaults_and_validation():
    from pulse.config import ComposeConfig

    assert ComposeConfig().target_words == 220
    assert ComposeConfig(max_words=200).target_words == 200  # default follows a lowered cap
    with pytest.raises(ValueError):
        ComposeConfig(max_words=200, target_words=230)
