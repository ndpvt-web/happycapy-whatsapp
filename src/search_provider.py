"""Swappable web search provider abstraction.

Design axiom: The integration layer never knows which search backend runs.
Swapping providers = change one config value.

Provider selection: config["web_search_provider"]
  - "worker_api" (default): Real Brave Search via Worker API (best quality)
  - "ai_gateway": LLM knowledge synthesis via AI Gateway (no real search)
  - "tavily": Tavily Search API (requires web_search_api_key)
  - "brave": Direct Brave Search API (requires web_search_api_key)
"""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None


@dataclass
class SearchResult:
    """A single web search result."""
    title: str
    url: str
    snippet: str
    published: str = ""  # ISO date if available


class SearchProvider(ABC):
    """Abstract search provider interface."""

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web for a query. Returns structured results."""
        ...


class WorkerAPISearchProvider(SearchProvider):
    """Real web search via Brave Search through the Worker API.

    This is the primary provider -- it returns actual web results
    with AI-generated summaries. Uses the same endpoint as the
    web-search skill script.
    """

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not httpx:
            return [SearchResult("Error", "", "httpx not installed")]

        worker_url = os.environ.get("AGENT_WORKER_BASE_URL", "")
        worker_secret = os.environ.get("AGENT_WORKER_SECRET", "")
        sandbox_id = os.environ.get("FLY_APP_NAME", "unknown")

        if not worker_url or not worker_secret:
            return [SearchResult("Error", "", "Worker API not configured (AGENT_WORKER_BASE_URL/SECRET)")]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{worker_url.rstrip('/')}/api/tool/web-search",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {worker_secret}",
                        "X-Sandbox-Id": sandbox_id,
                    },
                    json={
                        "query": query,
                        "count": min(max_results, 20),
                        "summary": True,
                        "entityInfo": True,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data, max_results)
        except Exception as e:
            print(f"[search-provider] Worker API search failed: {type(e).__name__}: {e}")
            return [SearchResult("Search Error", "", f"Web search failed: {type(e).__name__}: {e}")]

    def _parse_response(self, data: dict, max_results: int) -> list[SearchResult]:
        """Parse Worker API / Brave Search response into SearchResult list."""
        results: list[SearchResult] = []

        # Add AI summary as first result if available
        summary = data.get("summary", {})
        if summary and summary.get("text"):
            summary_text = summary["text"]
            # Truncate to first ~500 chars for the snippet
            if len(summary_text) > 500:
                summary_text = summary_text[:497] + "..."
            results.append(SearchResult(
                title="AI Summary",
                url="",
                snippet=summary_text,
                published="",
            ))

        # Add actual web results
        for r in data.get("results", [])[:max_results]:
            # Strip HTML tags from description
            desc = r.get("description", "")
            desc = re.sub(r"<[^>]+>", "", desc)
            results.append(SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=desc[:300],
                published=r.get("age", ""),
            ))

        return results


class AIGatewaySearchProvider(SearchProvider):
    """Fallback: Uses AI Gateway LLM to synthesize knowledge (not real search).

    This does NOT actually search the web -- it uses LLM training knowledge.
    Use only when Worker API is unavailable.
    """

    def __init__(self, config: dict[str, Any]):
        self._config = config

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not httpx:
            return [SearchResult("Error", "", "httpx not installed")]

        api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
        api_url = self._config.get(
            "ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1"
        ).rstrip("/")

        if not api_key:
            return [SearchResult("Error", "", "AI_GATEWAY_API_KEY not set")]

        prompt = (
            f"You are a research assistant. Based on your training knowledge, provide "
            f"the {max_results} most relevant and factual pieces of information about: {query}\n\n"
            f"Return a JSON array with {max_results} entries. Each entry must have:\n"
            '- "title": a descriptive title for the information\n'
            '- "url": a relevant URL if you know one (or empty string if unsure)\n'
            '- "snippet": 2-3 sentence factual summary\n'
            '- "published": approximate date if known (ISO format), or empty string\n\n'
            "Be factual. Do not fabricate URLs you are not confident about -- use empty string instead.\n"
            "Return ONLY the JSON array, no other text."
        )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{api_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "google/gemini-3-flash-preview",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2000,
                        "temperature": 0.0,
                    },
                    timeout=45.0,
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data["choices"][0]["message"]["content"]
                return self._parse_results(raw, max_results)
        except Exception as e:
            print(f"[search-provider] AI Gateway search failed: {type(e).__name__}: {e}")
            return [SearchResult("Search Error", "", f"Search failed: {type(e).__name__}")]

    def _parse_results(self, raw: str, max_results: int) -> list[SearchResult]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            items = json.loads(text)
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            return [SearchResult("Parse Error", "", f"Could not parse: {raw[:200]}")]
        results = []
        for item in items[:max_results]:
            if isinstance(item, dict):
                results.append(SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("snippet", "")),
                    published=str(item.get("published", "")),
                ))
        return results


class TavilySearchProvider(SearchProvider):
    """Tavily Search API provider."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not httpx:
            return [SearchResult("Error", "", "httpx not installed")]
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": self._api_key, "query": query, "max_results": max_results},
                    timeout=15.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("content", "")[:300],
                        published=r.get("published_date", ""),
                    )
                    for r in data.get("results", [])[:max_results]
                ]
        except Exception as e:
            print(f"[search-provider] Tavily search failed: {e}")
            return [SearchResult("Search Error", "", f"Tavily failed: {e}")]


class BraveSearchProvider(SearchProvider):
    """Direct Brave Search API provider (requires own API key)."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not httpx:
            return [SearchResult("Error", "", "httpx not installed")]
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
                    params={"q": query, "count": max_results},
                    timeout=15.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("description", "")[:300],
                        published=r.get("age", ""),
                    )
                    for r in data.get("web", {}).get("results", [])[:max_results]
                ]
        except Exception as e:
            print(f"[search-provider] Brave search failed: {e}")
            return [SearchResult("Search Error", "", f"Brave failed: {e}")]


def get_provider(config: dict[str, Any]) -> SearchProvider:
    """Factory: return the configured search provider.

    Priority: worker_api (real search) > tavily > brave > ai_gateway (fallback).
    Default is "worker_api" which uses the system's Brave Search via Worker API.
    """
    provider_name = config.get("web_search_provider", "worker_api")
    api_key = config.get("web_search_api_key", "")

    if provider_name == "tavily" and api_key:
        return TavilySearchProvider(api_key)
    elif provider_name == "brave" and api_key:
        return BraveSearchProvider(api_key)
    elif provider_name == "ai_gateway":
        return AIGatewaySearchProvider(config)
    else:
        # Default: Worker API (real Brave Search, no extra key needed)
        worker_url = os.environ.get("AGENT_WORKER_BASE_URL", "")
        if worker_url:
            return WorkerAPISearchProvider()
        # Ultimate fallback if Worker API not configured
        return AIGatewaySearchProvider(config)
