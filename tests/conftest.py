"""Shared fixtures and markers for tests."""

import httpx
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: tests that require a running LLM (Ollama)")


def _ollama_reachable() -> bool:
    """Check if Ollama is running at localhost:11434."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def ollama_available():
    """Skip test if Ollama is not reachable."""
    if not _ollama_reachable():
        pytest.skip("Ollama not running at localhost:11434")


# Default test model — ministral-3 is small, fast, and supports native tool calling.
# LiteLLM handles the "ollama/" prefix natively (no api_base needed).
LLM_TEST_MODEL = "ollama/ministral-3:8b"
