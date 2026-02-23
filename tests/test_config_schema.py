"""Tests for the configuration schema."""

from nanobot.config.schema import (
    AgentDefaults,
    AgentsConfig,
    ChannelsConfig,
    Config,
    DiscordConfig,
    FeishuConfig,
    GatewayConfig,
    ProviderConfig,
    ProvidersConfig,
    TelegramConfig,
    ToolsConfig,
    WhatsAppConfig,
)


# ── Channel configs ────────────────────────────────────────────────


def test_whatsapp_defaults():
    cfg = WhatsAppConfig()
    assert cfg.enabled is False
    assert cfg.bridge_url == "ws://localhost:3001"
    assert cfg.bridge_token == ""
    assert cfg.allow_from == []


def test_telegram_defaults():
    cfg = TelegramConfig()
    assert cfg.enabled is False
    assert cfg.token == ""
    assert cfg.proxy is None
    assert cfg.reply_to_message is False


def test_discord_defaults():
    cfg = DiscordConfig()
    assert cfg.enabled is False
    assert cfg.intents == 37377


def test_feishu_defaults():
    cfg = FeishuConfig()
    assert cfg.enabled is False
    assert cfg.app_id == ""


# ── Agent defaults ─────────────────────────────────────────────────


def test_agent_defaults():
    d = AgentDefaults()
    assert d.workspace == "~/.nanobot/workspace"
    assert "claude" in d.model.lower() or "anthropic" in d.model.lower()
    assert d.max_tokens > 0
    assert 0.0 <= d.temperature <= 2.0
    assert d.max_tool_iterations > 0


# ── Provider config ────────────────────────────────────────────────


def test_provider_config_defaults():
    p = ProviderConfig()
    assert p.api_key == ""
    assert p.api_base is None
    assert p.extra_headers is None


def test_provider_config_with_values():
    p = ProviderConfig(api_key="sk-test", api_base="https://api.example.com")
    assert p.api_key == "sk-test"
    assert p.api_base == "https://api.example.com"


# ── Gateway config ─────────────────────────────────────────────────


def test_gateway_defaults():
    g = GatewayConfig()
    assert g.host == "0.0.0.0"
    assert g.port == 18790


# ── Tools config ───────────────────────────────────────────────────


def test_tools_config_defaults():
    t = ToolsConfig()
    assert t.restrict_to_workspace is False
    assert t.mcp_servers == {}


# ── Root Config ────────────────────────────────────────────────────


def test_config_defaults():
    cfg = Config()
    assert isinstance(cfg.agents, AgentsConfig)
    assert isinstance(cfg.channels, ChannelsConfig)
    assert isinstance(cfg.providers, ProvidersConfig)
    assert isinstance(cfg.gateway, GatewayConfig)
    assert isinstance(cfg.tools, ToolsConfig)


def test_config_workspace_path():
    cfg = Config()
    ws = cfg.workspace_path
    assert ws.is_absolute()
    assert "~" not in str(ws)


def test_config_get_api_key_none():
    """No API keys configured should return None."""
    cfg = Config()
    assert cfg.get_api_key() is None


def test_config_get_api_key_from_anthropic():
    cfg = Config()
    cfg.providers.anthropic.api_key = "sk-ant-test"
    key = cfg.get_api_key("anthropic/claude-opus-4-5")
    assert key == "sk-ant-test"


def test_config_get_provider_fallback():
    """When no model keyword matches, fall back to first provider with a key."""
    cfg = Config()
    cfg.providers.openai.api_key = "sk-openai"
    provider = cfg.get_provider("unknown-model-xyz")
    assert provider is not None
    assert provider.api_key == "sk-openai"


def test_config_get_api_base_for_gateway():
    cfg = Config()
    cfg.providers.openrouter.api_key = "sk-or-test"
    base = cfg.get_api_base("openrouter/claude-3")
    # Should return the default openrouter base
    assert base is not None
    assert "openrouter" in base


def test_config_get_api_base_none():
    cfg = Config()
    assert cfg.get_api_base() is None


# ── Channels config nested ─────────────────────────────────────────


def test_channels_config():
    ch = ChannelsConfig()
    assert isinstance(ch.whatsapp, WhatsAppConfig)
    assert isinstance(ch.telegram, TelegramConfig)
    assert isinstance(ch.discord, DiscordConfig)
    assert isinstance(ch.feishu, FeishuConfig)
