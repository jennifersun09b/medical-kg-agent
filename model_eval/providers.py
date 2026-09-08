"""
API adaptors for the 4 Chinese LLM providers.
Each provider exposes a single function:
    chat(model_id, messages, **kwargs) -> str

All providers use an OpenAI-compatible chat-completions interface
(most Chinese platforms now support it) so we can share one client factory.
"""

import os
import functools

import httpx
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Timeouts and retries
# ---------------------------------------------------------------------------
# The SDK default is Timeout(connect=5.0, read=600) with max_retries=2. That
# 5s connect budget is too tight for these China-hosted endpoints: measured
# TLS handshakes reach 5.4s on tokenhub and p90=3.0s on open.bigmodel.cn, so
# a handshake that hiccups blows the budget, burns all 3 attempts, and fails
# after ~16s with a misleading "Request timed out." — long before the model
# ever starts generating.
#
# Fix: give the handshake room, and cap read at a value that actually relates
# to generation time (slowest observed answer was doubao at 209s).
TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)

# 5 attempts total. The SDK retries connect errors and 429/5xx with
# exponential backoff, which is exactly the transient-failure shape here.
MAX_RETRIES = 4

# Keep-alive matters as much as the timeout: chat() used to build a fresh
# client per question, so all 210 calls paid a new TLS handshake. Reusing one
# pooled connection per provider removes almost every chance to hit this.
_LIMITS = httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300.0)


def _make_client(env_var: str, base_url: str) -> OpenAI:
    return OpenAI(
        api_key=os.environ[env_var],
        base_url=base_url,
        timeout=TIMEOUT,
        max_retries=MAX_RETRIES,
        http_client=httpx.Client(timeout=TIMEOUT, limits=_LIMITS),
    )


# ---------------------------------------------------------------------------
# Client factories — one per base-URL / key pair
#
# @lru_cache makes these singletons, so the underlying HTTP connection pool is
# shared across every question instead of rebuilt per call.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _qianwen_client() -> OpenAI:
    """千问 via Alibaba Cloud 百炼 (DashScope OpenAI-compat endpoint)."""
    return _make_client(
        "QIANWEN_API_KEY",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


@functools.lru_cache(maxsize=None)
def _doubao_client() -> OpenAI:
    """豆包 via ByteDance Ark platform (OpenAI-compat endpoint)."""
    return _make_client(
        "DOUBAO_API_KEY",
        "https://ark.cn-beijing.volces.com/api/v3",
    )


@functools.lru_cache(maxsize=None)
def _zhipu_client() -> OpenAI:
    """智谱 GLM via ZhipuAI (OpenAI-compat endpoint)."""
    return _make_client(
        "ZHIPU_API_KEY",
        "https://open.bigmodel.cn/api/paas/v4",
    )


@functools.lru_cache(maxsize=None)
def _kimi_client() -> OpenAI:
    """Kimi via Moonshot AI (OpenAI-compat endpoint)."""
    return _make_client(
        "KIMI_API_KEY",
        "https://api.moonshot.cn/v1",
    )


@functools.lru_cache(maxsize=None)
def _deepseek_client() -> OpenAI:
    """DeepSeek (OpenAI-compat endpoint)."""
    return _make_client(
        "DEEPSEEK_API_KEY",
        "https://api.deepseek.com/v1",
    )


@functools.lru_cache(maxsize=None)
def _hunyuan_client() -> OpenAI:
    """
    混元 / 元宝 via Tencent Cloud TokenHub (大模型服务平台).

    NOTE: this is NOT the legacy Hunyuan endpoint
    (api.hunyuan.cloud.tencent.com) — Tencent has migrated Hunyuan to the
    TokenHub gateway, which uses a different host and its own API keys.
    Console: https://console.cloud.tencent.com/tokenhub
    """
    return _make_client(
        "HUNYUAN_API_KEY",
        "https://tokenhub.tencentmaas.com/v1",
    )


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_CLIENTS = {
    "qianwen": _qianwen_client,
    "doubao":  _doubao_client,
    "zhipu":   _zhipu_client,
    "kimi":    _kimi_client,
    "deepseek":_deepseek_client,
    "hunyuan": _hunyuan_client,
}


def chat(provider: str, model_id: str, messages: list[dict], **kwargs) -> str:
    """
    Send a chat request and return the assistant's text reply.

    Parameters
    ----------
    provider : str
        One of the keys in _CLIENTS (e.g. "qianwen").
    model_id : str
        The model identifier used by that provider.
    messages : list[dict]
        OpenAI-style message list, e.g.
        [{"role": "user", "content": "..."}]
    **kwargs
        Extra params forwarded to the completions endpoint
        (temperature, max_tokens, etc.).

    Returns
    -------
    str
        The model's reply text.
    """
    if provider not in _CLIENTS:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Available: {list(_CLIENTS.keys())}"
        )

    client = _CLIENTS[provider]()
    response = client.chat.completions.create(
        model=model_id,
        messages=messages,
        **kwargs,
    )
    return response.choices[0].message.content
