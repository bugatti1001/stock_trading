"""
Unified AI Client
统一封装 Claude 和 MiniMax 调用，所有 AI 服务通过此模块发起请求
"""
import logging
from typing import Iterator, List, Dict, Optional

logger = logging.getLogger(__name__)

# Client instance caches keyed by API key — avoids creating new connections on every call
_anthropic_clients: Dict[str, object] = {}
_openai_clients: Dict[str, object] = {}


def _get_anthropic_client(api_key: str):
    """Return a cached Anthropic client for the given API key."""
    import anthropic
    if api_key not in _anthropic_clients:
        _anthropic_clients[api_key] = anthropic.Anthropic(api_key=api_key, timeout=120.0)
    return _anthropic_clients[api_key]


def _get_openai_client(api_key: str, base_url: str):
    """Return a cached OpenAI-compatible client for the given API key + base URL."""
    import openai
    cache_key = f"{api_key}|{base_url}"
    if cache_key not in _openai_clients:
        _openai_clients[cache_key] = openai.OpenAI(api_key=api_key, base_url=base_url)
    return _openai_clients[cache_key]


def _get_provider_and_keys(provider: Optional[str] = None,
                           api_key: Optional[str] = None):
    """
    获取 AI 提供商及对应的 API Key。
    支持显式传入（线程安全场景）或从用户设置自动读取。
    """
    from app.config.settings import (
        get_ai_provider, get_anthropic_key, get_minimax_key,
        AI_MODEL, MINIMAX_DEFAULT_MODEL,
    )

    if provider is None:
        provider = get_ai_provider()

    if provider == 'minimax':
        key = api_key or get_minimax_key()
        model = MINIMAX_DEFAULT_MODEL
    else:
        provider = 'claude'
        key = api_key or get_anthropic_key()
        model = AI_MODEL

    if not key:
        raise ValueError(f'未配置 {provider.upper()} API Key，请在登录时填写')

    return provider, key, model


def create_message(system: str = '',
                   messages: Optional[List[Dict]] = None,
                   max_tokens: int = 4096,
                   model: Optional[str] = None,
                   temperature: Optional[float] = None,
                   provider: Optional[str] = None,
                   api_key: Optional[str] = None) -> str:
    """
    调用 AI 生成回复，返回文本。
    自动选择 Claude 或 MiniMax，也可显式指定 provider/api_key。
    """
    prov, key, default_model = _get_provider_and_keys(provider, api_key)
    model = model or default_model
    messages = messages or []

    if prov == 'minimax':
        return _minimax_create(key, model, system, messages, max_tokens, temperature)
    else:
        return _claude_create(key, model, system, messages, max_tokens, temperature)


def stream_message(system: str = '',
                   messages: Optional[List[Dict]] = None,
                   max_tokens: int = 4096,
                   model: Optional[str] = None,
                   provider: Optional[str] = None,
                   api_key: Optional[str] = None) -> Iterator[str]:
    """
    流式调用 AI，逐段 yield 文本。
    自动选择 Claude 或 MiniMax，也可显式指定。
    """
    prov, key, default_model = _get_provider_and_keys(provider, api_key)
    model = model or default_model
    messages = messages or []

    if prov == 'minimax':
        yield from _minimax_stream(key, model, system, messages, max_tokens)
    else:
        yield from _claude_stream(key, model, system, messages, max_tokens)


# ── Claude (Anthropic) ──────────────────────────────────────────

def _claude_create(api_key, model, system, messages, max_tokens, temperature):
    client = _get_anthropic_client(api_key)
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs['system'] = system
    if temperature is not None:
        kwargs['temperature'] = temperature
    msg = client.messages.create(**kwargs)
    return msg.content[0].text.strip()


def _claude_stream(api_key, model, system, messages, max_tokens):
    client = _get_anthropic_client(api_key)
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs['system'] = system
    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            yield text


# ── MiniMax (OpenAI-compatible) ─────────────────────────────────

def _build_openai_messages(system, messages):
    """将 Anthropic 格式的 messages 转为 OpenAI 格式（加入 system message）"""
    oai_messages = []
    if system:
        oai_messages.append({'role': 'system', 'content': system})
    for m in messages:
        oai_messages.append({'role': m['role'], 'content': m['content']})
    return oai_messages


def _minimax_create(api_key, model, system, messages, max_tokens, temperature):
    from app.config.settings import MINIMAX_BASE_URL
    client = _get_openai_client(api_key, MINIMAX_BASE_URL)
    kwargs = dict(
        model=model,
        messages=_build_openai_messages(system, messages),
        max_tokens=max_tokens,
    )
    if temperature is not None:
        kwargs['temperature'] = temperature
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def _minimax_stream(api_key, model, system, messages, max_tokens):
    from app.config.settings import MINIMAX_BASE_URL
    client = _get_openai_client(api_key, MINIMAX_BASE_URL)
    stream = client.chat.completions.create(
        model=model,
        messages=_build_openai_messages(system, messages),
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
