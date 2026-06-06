import httpx
import pytest

from app.core.config import Settings
from app.services.llm_providers import (
    AutoLLMProvider,
    LocalFallbackProvider,
    LocalModelProvider,
    OllamaProvider,
    build_llm_provider,
)


class SuccessfulOllamaClient:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, json: dict):
        assert url == "http://ollama.test/api/generate"
        assert json["model"] == "qwen3:8b"
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "response": (
                    '{"summary":"Adds a billing function",'
                    '"generated_docs":[{"name":"bill","kind":"function",'
                    '"documentation":"Returns the calculated bill."}],'
                    '"changed_functions":["bill"],'
                    '"risks":["Confirm currency handling"],'
                    '"confidence_score":0.91}'
                )
            },
        )


class FailingOllamaClient:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, json: dict):
        request = httpx.Request("POST", url)
        raise httpx.ConnectError("Ollama is not running", request=request)


def test_build_llm_provider_defaults_to_ollama() -> None:
    provider = build_llm_provider(Settings())

    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3:8b"


def test_build_llm_provider_supports_local_fallback() -> None:
    provider = build_llm_provider(Settings(llm_provider="local_fallback"))

    assert isinstance(provider, LocalFallbackProvider)


def test_build_llm_provider_supports_auto() -> None:
    provider = build_llm_provider(Settings(llm_provider="auto"))

    assert isinstance(provider, AutoLLMProvider)


@pytest.mark.asyncio
async def test_ollama_provider_parses_mocked_structured_response(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_providers.httpx.AsyncClient", SuccessfulOllamaClient)
    provider = OllamaProvider(
        Settings(
            ollama_base_url="http://ollama.test",
            ollama_model="qwen3:8b",
            llm_max_retries=1,
        )
    )

    result = await provider.generate_json(
        "prompt",
        {"symbols": [{"name": "bill", "kind": "function"}]},
    )

    assert result["summary"] == "Adds a billing function"
    assert result["changed_functions"] == ["bill"]
    assert result["confidence_score"] == 0.91


@pytest.mark.asyncio
async def test_ollama_provider_falls_back_when_connection_fails(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_providers.httpx.AsyncClient", FailingOllamaClient)
    provider = OllamaProvider(Settings(llm_max_retries=1))

    result = await provider.generate_json(
        "prompt",
        {"symbols": [{"name": "bill", "kind": "function"}]},
    )

    assert result["changed_functions"] == ["bill"]
    assert "deterministic local fallback" in result["risks"][0].lower()


@pytest.mark.asyncio
async def test_auto_provider_falls_through_to_local_model(monkeypatch) -> None:
    calls = []

    async def failing_ollama(self, prompt, fallback_context):
        del self, prompt, fallback_context
        calls.append("ollama")
        raise ValueError("ollama unavailable")

    async def successful_local_model(self, prompt, fallback_context):
        del self, prompt, fallback_context
        calls.append("local_model")
        return {
            "summary": "local model response",
            "generated_docs": [],
            "changed_functions": ["from_local"],
            "risks": [],
            "confidence_score": 0.7,
        }

    monkeypatch.setattr(OllamaProvider, "generate_json", failing_ollama)
    monkeypatch.setattr(LocalModelProvider, "generate_json", successful_local_model)

    provider = AutoLLMProvider(Settings(llm_provider="auto"))
    result = await provider.generate_json("prompt", {"symbols": []})

    assert calls == ["ollama", "local_model"]
    assert result["changed_functions"] == ["from_local"]
