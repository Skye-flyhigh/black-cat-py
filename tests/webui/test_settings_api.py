from __future__ import annotations

import json

import httpx
import pytest

from blackcat.config.loader import load_config, save_config
from blackcat.config.schema import Config, ModelPresetConfig
from blackcat.providers.registry import find_by_name
from blackcat.webui.settings_api import (
    WebUISettingsError,
    _oauth_provider_status,
    create_model_configuration,
    provider_models_payload,
    settings_payload,
    settings_usage_payload,
    update_agent_settings,
    update_model_configuration,
    update_network_safety_settings,
    update_provider_settings,
    update_transcription_settings,
)

DYNAMIC_PROVIDER_NAME = "my-company-api"
DYNAMIC_PROVIDER_API_BASE = "https://example.test/v1"


def _dynamic_provider_config(
    *,
    api_base: str = DYNAMIC_PROVIDER_API_BASE,
    defaults: bool = False,
) -> Config:
    raw_config = {
        "providers": {
            DYNAMIC_PROVIDER_NAME: {
                "apiBase": api_base,
            }
        }
    }
    if defaults:
        raw_config["agents"] = {
            "defaults": {
                "provider": DYNAMIC_PROVIDER_NAME,
                "model": "gpt-4o-mini",
            }
        }
    return Config.model_validate(raw_config)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is True
    assert azure["api_key_required"] is False
    assert azure["auth_type"] == "api_key"
    assert azure["api_base"] == "https://r.openai.azure.com"


def test_settings_payload_azure_openai_aad_mode_is_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AAD mode: only api_base set (no api_key) -> still configured."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_base = "https://r.openai.azure.com"
    save_config(config, config_path)
    monkeypatch.setattr("blackcat.config.loader._current_config_path", config_path)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is True
    assert azure["api_key_required"] is False
    assert azure["api_base"] == "https://r.openai.azure.com"
    assert azure["api_key_hint"] is None


def test_settings_payload_azure_openai_missing_base_not_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_key alone (no api_base) is NOT a working config -> not configured."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_key = "k"
    save_config(config, config_path)
    monkeypatch.setattr("blackcat.config.loader._current_config_path", config_path)

    payload = settings_payload()
    azure = next(row for row in payload["providers"] if row["name"] == "azure_openai")

    assert azure["configured"] is False


def test_create_model_configuration_accepts_azure_openai_aad_mode(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider-validation accepts azure_openai with only api_base (AAD mode)."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.azure_openai.api_base = "https://r.openai.azure.com"
    save_config(config, config_path)
    monkeypatch.setattr("blackcat.config.loader._current_config_path", config_path)

    payload = create_model_configuration(
        {
            "label": ["Azure AAD"],
            "provider": ["azure_openai"],
            "model": ["my-deployment"],
        }
    )

    assert payload["agent"]["model_preset"] == "azure-aad"
    saved = load_config(config_path)
    assert saved.model_presets["azure-aad"].provider == "azure_openai"
    assert saved.model_presets["azure-aad"].model == "my-deployment"


def test_create_model_configuration_rejects_azure_openai_without_base(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """azure_openai without api_base must still be rejected as not configured."""
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr("blackcat.config.loader._current_config_path", config_path)

    with pytest.raises(WebUISettingsError, match="provider is not configured"):
        create_model_configuration(
            {
                "label": ["Azure"],
                "provider": ["azure_openai"],
                "model": ["my-deployment"],
            }
        )


def test_azure_openai_spec_no_longer_requires_api_key() -> None:
    """Contract guard: api_key is optional for azure_openai (AAD fallback)."""
    from blackcat.webui.settings_api import _provider_requires_api_key

    spec = find_by_name("azure_openai")
    assert spec is not None
    assert _provider_requires_api_key(spec) is False
