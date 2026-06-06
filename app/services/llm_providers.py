import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings

logger = logging.getLogger(__name__)

STRUCTURED_FIELDS = {
    "summary": "",
    "generated_docs": [],
    "changed_functions": [],
    "risks": [],
    "confidence_score": 0.5,
}


class BaseLLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class LocalFallbackProvider(BaseLLMProvider):
    name = "local_fallback"
    model = "deterministic"

    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        del prompt
        return deterministic_fallback_payload(fallback_context)


class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings, fallback_on_error: bool = True) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.llm_request_timeout_seconds
        self.max_retries = settings.llm_max_retries
        self.fallback_on_error = fallback_on_error
        self._fallback = LocalFallbackProvider()

    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._generate_json_with_retry(prompt)
        except (httpx.HTTPError, ValueError) as exc:
            if not self.fallback_on_error:
                raise
            logger.warning("Ollama generation failed; using deterministic fallback: %s", exc)
            return await self._fallback.generate_json(prompt, fallback_context)

    async def _generate_json_with_retry(self, prompt: str) -> dict[str, Any]:
        @retry(
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            stop=stop_after_attempt(self.max_retries),
            retry=retry_if_exception_type((httpx.HTTPError, ValueError)),
            reraise=True,
        )
        async def _call() -> dict[str, Any]:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2},
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            body = response.json()
            return normalize_structured_payload(body.get("response", ""))

        return await _call()


class GenericAPIProvider(BaseLLMProvider):
    name = "generic_api"

    def __init__(self, settings: Settings, fallback_on_error: bool = True) -> None:
        self.base_url = (settings.generic_llm_api_base_url or "").rstrip("/")
        self.api_key = settings.generic_llm_api_key
        self.model = settings.generic_llm_model
        self.timeout = settings.llm_request_timeout_seconds
        self.fallback_on_error = fallback_on_error
        self._fallback = LocalFallbackProvider()

    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            if not self.fallback_on_error:
                raise ValueError("GENERIC_LLM_API_BASE_URL and GENERIC_LLM_API_KEY are required")
            logger.warning("Generic LLM API is not configured; using deterministic fallback")
            return await self._fallback.generate_json(prompt, fallback_context)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Return only valid structured JSON.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return normalize_structured_payload(content)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            if not self.fallback_on_error:
                raise ValueError(f"Generic LLM API generation failed: {exc}") from exc
            logger.warning("Generic LLM API failed; using deterministic fallback: %s", exc)
            return await self._fallback.generate_json(prompt, fallback_context)


class LocalModelProvider(BaseLLMProvider):
    name = "local_model"

    def __init__(self, settings: Settings, fallback_on_error: bool = True) -> None:
        self.model = settings.local_model
        self.timeout = settings.local_model_timeout_seconds
        self.fallback_on_error = fallback_on_error
        self._fallback = LocalFallbackProvider()
        self._pipeline: Any | None = None

    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._generate_sync, prompt),
                timeout=self.timeout,
            )
        except Exception as exc:
            if not self.fallback_on_error:
                raise RuntimeError(f"Local model generation failed: {exc}") from exc
            logger.warning("Local model generation failed; using deterministic fallback: %s", exc)
            return await self._fallback.generate_json(prompt, fallback_context)

    def _generate_sync(self, prompt: str) -> dict[str, Any]:
        if self._pipeline is None:
            try:
                from transformers import pipeline
            except ImportError as exc:
                raise RuntimeError(
                    "Install codescribe[local-llm] to enable LocalModelProvider"
                ) from exc

            self._pipeline = pipeline(
                "text-generation",
                model=self.model,
                device=-1,
                max_new_tokens=512,
            )

        output = self._pipeline(
            "Return only JSON with keys summary, generated_docs, changed_functions, risks, "
            f"confidence_score.\n{prompt}"
        )
        text = output[0]["generated_text"] if output else ""
        json_start = text.find("{")
        json_text = text[json_start:] if json_start >= 0 else text
        return normalize_structured_payload(json_text)


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model
        self._fallback = LocalFallbackProvider()

    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            logger.warning("GEMINI_API_KEY missing; using deterministic fallback")
            return await self._fallback.generate_json(prompt, fallback_context)

        try:
            import google.generativeai as genai
        except ImportError:
            logger.warning("google-generativeai is not installed; using deterministic fallback")
            return await self._fallback.generate_json(prompt, fallback_context)

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model)
        response = await model.generate_content_async(prompt)
        return normalize_structured_payload(response.text)


class AutoLLMProvider(BaseLLMProvider):
    name = "auto"
    model = "auto"

    def __init__(self, settings: Settings) -> None:
        providers: list[BaseLLMProvider] = [OllamaProvider(settings, fallback_on_error=False)]
        if settings.generic_llm_api_base_url and settings.generic_llm_api_key:
            providers.append(GenericAPIProvider(settings, fallback_on_error=False))
        providers.extend(
            [
                LocalModelProvider(settings, fallback_on_error=False),
                LocalFallbackProvider(),
            ]
        )
        self.providers = providers

    async def generate_json(self, prompt: str, fallback_context: dict[str, Any]) -> dict[str, Any]:
        failures: list[str] = []
        for provider in self.providers:
            try:
                result = await provider.generate_json(prompt, fallback_context)
                self.model = provider.model
                self.name = f"auto:{provider.name}"
                return result
            except Exception as exc:
                failures.append(f"{provider.name}: {exc}")
                logger.info("Auto LLM provider skipped %s: %s", provider.name, exc)
        logger.warning("Auto LLM exhausted providers: %s", "; ".join(failures))
        return await LocalFallbackProvider().generate_json(prompt, fallback_context)


def build_llm_provider(settings: Settings) -> BaseLLMProvider:
    provider = settings.llm_provider.lower().strip()
    if provider == "auto":
        return AutoLLMProvider(settings)
    if provider == "gemini":
        return GeminiProvider(settings)
    if provider in {"generic", "generic_api", "openai_compatible"}:
        return GenericAPIProvider(settings)
    if provider in {"local_model", "local-llm"}:
        return LocalModelProvider(settings)
    if provider == "local_fallback":
        return LocalFallbackProvider()
    return OllamaProvider(settings)


def normalize_structured_payload(raw_response: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_response, dict):
        parsed = raw_response
    else:
        parsed = _parse_json_response(raw_response)

    normalized = {**STRUCTURED_FIELDS, **parsed}
    normalized["generated_docs"] = _coerce_list(normalized.get("generated_docs"))
    normalized["changed_functions"] = _coerce_list(normalized.get("changed_functions"))
    normalized["risks"] = _coerce_list(normalized.get("risks"))
    normalized["confidence_score"] = _coerce_score(normalized.get("confidence_score"))
    normalized["summary"] = str(normalized.get("summary") or "")
    return normalized


def deterministic_fallback_payload(context: dict[str, Any]) -> dict[str, Any]:
    symbols = context.get("symbols", [])
    functions = [symbol["name"] for symbol in symbols if symbol.get("kind") == "function"]
    docs = []
    for symbol in symbols:
        docs.append(
            {
                "name": symbol.get("name", "unknown"),
                "kind": symbol.get("kind", "symbol"),
                "documentation": (
                    f"`{symbol.get('name', 'unknown')}` was detected in the changed code. "
                    "Review its behavior, inputs, outputs, and side effects before publishing."
                ),
            }
        )

    return {
        "summary": context.get(
            "summary",
            "CodeScribe generated a local documentation draft from PR diff and AST metadata.",
        ),
        "generated_docs": docs or ["No symbols were detected in the changed code."],
        "changed_functions": functions,
        "risks": ["LLM provider unavailable; deterministic local fallback was used."],
        "confidence_score": 0.55,
    }


def _parse_json_response(raw_response: str) -> dict[str, Any]:
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {cleaned[:200]}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON response must be an object")
    return parsed


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _coerce_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, score))
