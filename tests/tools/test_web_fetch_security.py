"""Tests for web_fetch SSRF protection and untrusted content marking."""

from __future__ import annotations

import json
import socket
from unittest.mock import patch

import pytest

from blackcat.agent.tools.web import WebFetchTool


def _fake_resolve_private(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_public(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_ip():
    tool = WebFetchTool()
    with patch("blackcat.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(url="http://169.254.169.254/computeMetadata/v1/")
    data = json.loads(result)
    assert "error" in data
    assert "private" in data["error"].lower() or "blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_internal_ip():
    """web_fetch should block private/internal IPs (localhost is allowed for LSP bridges)."""
    tool = WebFetchTool()
    def _resolve_private(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))]
    with patch("blackcat.security.network.socket.getaddrinfo", _resolve_private):
        result = await tool.execute(url="http://internal-service/admin")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_result_contains_untrusted_flag():
    """When fetch succeeds, result JSON must include untrusted=True and the banner."""
    tool = WebFetchTool()

    fake_html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"

    class FakeResponse:
        status_code = 200
        url = "https://example.com/page"
        text = fake_html
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            # Stream returns context manager for image detection
            raise AttributeError("No image content-type")

        async def get(self, url, headers=None):
            return FakeResponse()

    # Mock Jina to fail so it falls back to readability
    async def _fake_jina(url, max_chars):
        return None

    with patch("blackcat.security.network.socket.getaddrinfo", _fake_resolve_public), \
         patch("httpx.AsyncClient", FakeClient), \
         patch.object(tool, "_fetch_jina", _fake_jina):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data.get("untrusted") is True
    assert "external content" in data.get("text", "").lower()


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_redirect_before_returning_image(monkeypatch):
    tool = WebFetchTool()

    class FakeStreamResponse:
        headers = {"content-type": "image/png"}
        url = "http://10.0.0.1/secret.png"  # Private IP that should be blocked
        content = b"\x89PNG\r\n\x1a\n"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            return self.content

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr("blackcat.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("blackcat.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/image.png")

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()
