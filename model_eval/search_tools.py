"""
Search engine integration for Tavily (international) and Doubao (Chinese).

Functions
---------
run_search(engine, query, max_results=3)
    Query one or more search engines and return merged results.

format_search_results(results)
    Format search results into a context string for LLM injection.

Doubao search API notes (see README_SEARCH.md):
- Request keys are PascalCase and case-sensitive: `Query` + `SearchType`
  work; lowercase `query`/`search_type` are silently ignored and come back
  as "query or search type is empty". `SearchType` must be lowercase "web".
  `Count` caps results server-side.
- It returns HTTP 200 even on failure: errors arrive as 200 with
  `Result: null` and the detail in `ResponseMetadata.Error`, so
  raise_for_status() alone cannot catch them.
- Response items expose `Title`, `Url`, and three text fields of increasing
  length (`Snippet`, `Summary`, `Content`). Prefer `Content`, fall back
  through the other two.
"""

import os
import time
import random
import requests
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
DOUBAO_SEARCH_API_KEY = os.getenv("DOUBAO_SEARCH_API_KEY")
DOUBAO_SEARCH_URL = os.getenv(
    "DOUBAO_SEARCH_URL",
    "https://open.feedcoopapi.com/search_api/web_search",
)


def _search_tavily(query: str, max_results: int = 3) -> tuple[list[dict], str | None]:
    """
    Search using Tavily API (international search engine).

    Returns
    -------
    (results, error)
        results: list of {"title": str, "url": str, "content": str}
        error: None on success, error message string on failure
    """
    if not TAVILY_API_KEY:
        return [], "TAVILY_API_KEY not configured"

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "include_answer": False,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
            })
        return results, None
    except Exception as e:
        return [], f"Tavily error: {str(e)}"


def _search_doubao(query: str, max_results: int = 3) -> tuple[list[dict], str | None]:
    """
    Search using Doubao Search API (Chinese search engine).

    Returns
    -------
    (results, error)
        results: list of {"title": str, "url": str, "content": str}
        error: None on success, error message string on failure
    """
    if not DOUBAO_SEARCH_API_KEY or not DOUBAO_SEARCH_URL:
        return [], "DOUBAO_SEARCH_API_KEY or DOUBAO_SEARCH_URL not configured"

    last_error = None
    for attempt in range(4):
        if attempt:
            # Backoff with jitter — rate limits and connection resets are
            # transient when 20 threads hit the endpoint at once.
            time.sleep(2 ** attempt + random.random())
        try:
            response = requests.post(
                DOUBAO_SEARCH_URL,
                json={
                    # PascalCase keys are required; lowercase is silently ignored.
                    "Query": query,
                    "SearchType": "web",   # must be lowercase "web"
                    "Count": max_results,
                },
                headers={
                    "Authorization": f"Bearer {DOUBAO_SEARCH_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            # The API returns HTTP 200 even on failure; check ResponseMetadata.Error.
            meta_error = (data.get("ResponseMetadata") or {}).get("Error")
            if meta_error and (meta_error.get("Code") or meta_error.get("Message")):
                last_error = (
                    f"Doubao API error {meta_error.get('Code')}: "
                    f"{meta_error.get('Message')}"
                )
                continue  # retry — usually rate_limit_exceeded

            result = data.get("Result") or {}
            items = (
                result.get("WebResults")
                or result.get("Results")
                or result.get("Items")
                or []
            )

            results = []
            for item in items[:max_results]:
                content = (
                    item.get("Content")
                    or item.get("Summary")
                    or item.get("Snippet")
                    or ""
                )
                results.append({
                    "title": item.get("Title", ""),
                    "url": item.get("Url", ""),
                    "content": content,
                })
            return results, None
        except Exception as e:
            last_error = f"Doubao error: {str(e)}"
    return [], last_error


def run_search(
    engine: str | list[str] | None,
    query: str,
    max_results: int = 3
) -> tuple[list[dict], list[str], list[str]]:
    """
    Query one or more search engines and return merged, deduplicated results.

    Parameters
    ----------
    engine : str | list[str] | None
        None → no search
        "tavily" → Tavily only
        "doubao" → Doubao only
        ["tavily", "doubao"] → both, merged and deduplicated by URL
    query : str
        Search query text
    max_results : int
        Max results per engine (default 3)

    Returns
    -------
    (results, engines_used, errors)
        results: merged list of {"title", "url", "content"}, deduplicated by URL
        engines_used: list of engine names that returned results
        errors: list of error messages from failed engines
    """
    if not engine:
        return [], [], []

    engines = [engine] if isinstance(engine, str) else engine
    all_results = []
    engines_used = []
    errors = []

    for eng in engines:
        if eng == "tavily":
            results, error = _search_tavily(query, max_results)
        elif eng == "doubao":
            results, error = _search_doubao(query, max_results)
        else:
            errors.append(f"Unknown engine: {eng}")
            continue

        if error:
            errors.append(error)
        elif results:
            all_results.extend(results)
            engines_used.append(eng)

    # Deduplicate by URL (keep first occurrence)
    seen_urls = set()
    unique_results = []
    for item in all_results:
        url = item["url"]
        if url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(item)

    return unique_results, engines_used, errors


def format_search_results(results: list[dict]) -> str:
    """
    Format search results into a context string for LLM injection.

    Parameters
    ----------
    results : list[dict]
        Search results from run_search, each with "title", "url", "content"

    Returns
    -------
    str
        Formatted context string, or empty string if no results
    """
    if not results:
        return ""

    lines = ["以下是网络搜索结果，可作为参考：\n"]
    for i, item in enumerate(results, 1):
        lines.append(f"[{i}] {item['title']}")
        lines.append(f"来源: {item['url']}")
        lines.append(f"{item['content']}\n")

    return "\n".join(lines)
