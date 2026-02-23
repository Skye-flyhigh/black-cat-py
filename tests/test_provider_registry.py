"""Tests for the provider registry (ProviderSpec, lookup helpers)."""

from nanobot.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_model,
    find_by_name,
    find_gateway,
)


# ── find_by_model ──────────────────────────────────────────────────


def test_find_claude_model():
    spec = find_by_model("anthropic/claude-opus-4-5")
    assert spec is not None
    assert spec.name == "anthropic"


def test_find_gpt_model():
    spec = find_by_model("gpt-4o")
    assert spec is not None
    assert spec.name == "openai"


def test_find_deepseek_model():
    spec = find_by_model("deepseek-chat")
    assert spec is not None
    assert spec.name == "deepseek"


def test_find_gemini_model():
    spec = find_by_model("gemini-pro")
    assert spec is not None
    assert spec.name == "gemini"


def test_find_qwen_model():
    spec = find_by_model("qwen-max")
    assert spec is not None
    assert spec.name == "dashscope"


def test_find_kimi_model():
    spec = find_by_model("kimi-k2.5")
    assert spec is not None
    assert spec.name == "moonshot"


def test_find_by_model_case_insensitive():
    spec = find_by_model("Claude-3-Sonnet")
    assert spec is not None
    assert spec.name == "anthropic"


def test_find_by_model_unknown():
    assert find_by_model("totally-unknown-model") is None


def test_find_by_model_skips_gateways():
    """Gateways should not be matched by model keywords."""
    spec = find_by_model("openrouter")
    # "openrouter" is a keyword on the openrouter spec, but it's a gateway
    assert spec is None


def test_find_by_model_skips_ollama_qwen():
    """ollama/qwen3 must not match DashScope — it's a local model."""
    assert find_by_model("ollama/qwen3-vl:8b") is None


def test_find_by_model_skips_ollama_chat_qwen():
    """ollama_chat/qwen3 must not match DashScope."""
    assert find_by_model("ollama_chat/qwen3-vl:8b") is None


def test_find_by_model_bare_qwen_still_matches_dashscope():
    """Bare qwen (no local prefix) should still match DashScope."""
    spec = find_by_model("qwen-max")
    assert spec is not None
    assert spec.name == "dashscope"


# ── find_gateway ───────────────────────────────────────────────────


def test_find_gateway_by_key_prefix():
    spec = find_gateway("sk-or-abc123", None)
    assert spec is not None
    assert spec.name == "openrouter"


def test_find_gateway_by_base_keyword():
    spec = find_gateway(None, "https://aihubmix.com/v1")
    assert spec is not None
    assert spec.name == "aihubmix"


def test_find_gateway_openrouter_by_base():
    spec = find_gateway(None, "https://openrouter.ai/api/v1")
    assert spec is not None
    assert spec.name == "openrouter"


def test_find_gateway_unknown_base_is_vllm():
    """Unknown api_base should fall back to vLLM (local)."""
    spec = find_gateway(None, "http://localhost:8000/v1")
    assert spec is not None
    assert spec.name == "vllm"
    assert spec.is_local is True


def test_find_gateway_no_key_no_base():
    assert find_gateway(None, None) is None


def test_find_gateway_regular_key_no_base():
    assert find_gateway("sk-regular-key", None) is None


# ── find_by_name ───────────────────────────────────────────────────


def test_find_by_name_anthropic():
    spec = find_by_name("anthropic")
    assert spec is not None
    assert spec.env_key == "ANTHROPIC_API_KEY"


def test_find_by_name_groq():
    spec = find_by_name("groq")
    assert spec is not None
    assert spec.litellm_prefix == "groq"


def test_find_by_name_unknown():
    assert find_by_name("nonexistent") is None


# ── ProviderSpec properties ────────────────────────────────────────


def test_label_uses_display_name():
    spec = find_by_name("openrouter")
    assert spec.label == "OpenRouter"


def test_label_falls_back_to_title():
    spec = ProviderSpec(name="test", keywords=("test",), env_key="TEST_KEY")
    assert spec.label == "Test"


def test_providers_tuple_is_not_empty():
    assert len(PROVIDERS) > 0


def test_all_providers_have_unique_names():
    names = [spec.name for spec in PROVIDERS]
    assert len(names) == len(set(names))


def test_moonshot_model_overrides():
    spec = find_by_name("moonshot")
    assert spec is not None
    overrides = dict(spec.model_overrides)
    assert "kimi-k2.5" in overrides
    assert overrides["kimi-k2.5"]["temperature"] == 1.0


def test_anthropic_supports_prompt_caching():
    spec = find_by_name("anthropic")
    assert spec.supports_prompt_caching is True


def test_openrouter_supports_prompt_caching():
    spec = find_by_name("openrouter")
    assert spec.supports_prompt_caching is True


def test_deepseek_skip_prefixes():
    spec = find_by_name("deepseek")
    assert "deepseek/" in spec.skip_prefixes
